from typing import Optional
import bpy
import pathlib
import os
import re
from . import debug, export

# API prefix used for all operators
PREFIX = "neverdone"
NPANEL_NAME = "Neverdone (Godot)"

# Animation interpolation modes, copied from the sampling infterpolation
# fallback values.  Not in export because we want to expose a default value
# in the preference to support projects using stop-motion animation.
ANIM_INTERPOLATION = [
    ("LINEAR", "Linear", "Linear interpolation between keyframes", 0),
    ("STEP", "Step", "No interpolation between keyframes", 1),
]


class GW_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    project_path: bpy.props.StringProperty(
        name="Project Path",
        default="",
        subtype="DIR_PATH",
        description="Path to the project directory (Defaults to git repository root)",
    )
    source_path_rel: bpy.props.StringProperty(
        name="Asset Subpath",
        default="asset_source",
        description="Asset source directory, relative to Project Path",
    )
    godot_path_rel: bpy.props.StringProperty(
        name="Godot Project Subpath",
        default="game",
        description="Godot project directory, relative to Project Path",
    )
    collision_object_prefix: bpy.props.StringProperty(
        name="Collision Object Prefix",
        default="COL-",
        description="Prefix for CollisionShape Objects",
    )
    collision_object_color: bpy.props.FloatVectorProperty(
        name="Collision Object Color",
        subtype="COLOR",
        size=4,
        default=(0.0, 1.0, 1.0, 1.0),  # Default to White
        min=0.0,
        max=1.0,
        description="Color for CollisionShape3D objects",
    )
    physics_body_prefix: bpy.props.StringProperty(
        name="Physics Body Object Prefix",
        default="BODY-",
        description="Prefix for Physics Body Objects",
    )
    path_prefix: bpy.props.StringProperty(
        name="Path3D Object Prefix",
        default="PATH-",
        description="Prefix for Path3D Objects",
    )
    anim_interpolation_default: bpy.props.EnumProperty(
        name="Default Animation Interpolation",
        items=ANIM_INTERPOLATION,
        description="Default Sampling Interpolation Fallback for Animation Export Collections",
    )

    def draw(self, _context):
        layout = self.layout

        row = layout.row()
        project_dir = self.project_root()
        row.alert = not (pathlib.Path(project_dir).exists())
        row.prop(self, "project_path", placeholder=str(project_dir))

        # Show the source path property, along with a second row displaying the
        # absolute path
        layout.prop(self, "source_path_rel")
        row = layout.row()
        row = row.split(factor=0.25)
        row.enabled = False
        row.label()
        row.label(text=str(self.source_path()))

        # Do the same for the godot project path
        layout.prop(self, "godot_path_rel")
        row = layout.row()
        row = row.split(factor=0.25)
        row.enabled = False
        row.label()
        row.label(text=str(self.godot_path()))

        layout.separator()
        layout.prop(self, "collision_object_prefix")
        layout.prop(self, "collision_object_color")

        layout.separator()
        layout.prop(self, "path_prefix")

        layout.separator()
        layout.prop(self, "anim_interpolation_default")

    def get_output_path(self, tail: str = "") -> pathlib.Path:
        blend_file = pathlib.Path(bpy.data.filepath)
        rel_path = blend_file.relative_to(self.source_path()).with_suffix("")
        output_path = self.godot_assets_path(rel_path)
        debug.inspect(
            dict(blend_file=blend_file, rel_path=rel_path, output_path=output_path)
        )
        # relative_to() doesn't yet support walk_up=True, so workaround here
        return pathlib.Path(os.path.relpath(output_path, blend_file.parent))

    def source_path(self, path="") -> pathlib.Path:
        """Return a path in the source directory"""
        return self.project_root() / self.source_path_rel / path

    def godot_path(self, path="") -> pathlib.Path:
        """Return a path in the godot directory"""
        print(f"input {path}")
        return self.project_root() / self.godot_path_rel / path

    def godot_assets_path(self, path="", rel=False) -> pathlib.Path:
        """Return an assets path in the godot directory"""
        print(f"input {path}")
        if rel:
            base = pathlib.Path(self.godot_path_rel)
        else:
            base = self.project_root() / self.godot_path_rel
        return base / "assets" / path

    def project_root(self) -> pathlib.Path:
        """Get the project root directory.

        Gets the project root directory either from the addon preferences, or
        traverses up the directory tree until it finds the top of the current git
        repository.
        """
        # try pulling the project root from the preferences first
        if self.project_path:
            return pathlib.Path(self.project_path)

        # failing that, walk up the directory tree from the blend file looking for
        # a .git/ directory
        path = pathlib.Path(bpy.data.filepath).parent
        while path != path.parent:
            if (path / ".git").is_dir():
                return path
            path = path.parent

        # Failed to find any directory, return the directory of the .blend file
        return pathlib.Path(bpy.data.filepath).parent

    def normalize_path_part(self, part: str) -> str:
        """
        Convert CamelCase word segments to snake_case, preserving non-alphanumeric
        delimiters (e.g. hyphens) and leaving sequential capitals intact.

        Examples:
            "GodotPlushie"    -> "godot_plushie"
            "CH-GodotPlushie" -> "ch-godot_plushie"
            "CHPlushie"       -> "ch_plushie"
            "CH"              -> "ch"
        """
        # "someWord" -> "some_Word": a lowercase/digit directly before an uppercase
        lowercase_then_upper = r"(?<=[a-z0-9])(?=[A-Z])"

        # "CHWord" -> "CH_Word": a run of caps directly before a Capital+lowercase pair.
        # This avoids splitting "CH" alone, since there's no trailing lowercase.
        acronym_then_capital = r"(?<=[A-Z])(?=[A-Z][a-z])"

        s = re.sub(lowercase_then_upper, "_", part)
        s = re.sub(acronym_then_capital, "_", s)
        return s.lower()

    def get_anim_interpolation_default_value(self) -> int:
        return 0 if self.anim_interpolation_default == "LINEAR" else 1


classes = [
    GW_preferences,
]


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)
