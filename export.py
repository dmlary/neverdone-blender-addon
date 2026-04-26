import pathlib
import os
import bpy

from .prefs import GW_preferences, PREFIX

# name used for Collection Exporters
EXPORTER_NAME = "Godot Workflow"

# Different export targets
EXPORT_TYPES = [
    ("NONE", "None", "", "NONE", 0),
    ("GLTF", "GLTF", "", "ASSET_MANAGER", 1),
    ("SCENE", "Scene + GLTF", "", "SEQUENCE", 2),
    ("ANIMATION", "Animation", "", "RENDER_ANIMATION", 3),
]

# Different GLTF import root node types
ROOT_NODE_TYPES = [
    ("NODE", "Node3D", "Default; no collision shape support", "ANTIALIASED", 0),
    (
        "CHARACTER_BODY",
        "CharacterBody3D",
        "3D physics body specialized for characters moved by script",
        "ARMATURE_DATA",
        3,
    ),
    (
        "RIGID_BODY",
        "RigidBody3D",
        "3D physics body that is moved by a physics simulation",
        "MATERIAL",
        2,
    ),
    (
        "STATIC_BODY",
        "StaticBody3D",
        "3D physics body that cannot be moved by external forces",
        "SHADING_BBOX",
        1,
    ),
]


def create_asset_id() -> str:
    """Creat a new asset_id uri"""
    id = int.from_bytes(os.urandom(8))
    return str(id & 0x7FFFFFFFFFFFFFFF)


class GW_PG_export_properties(bpy.types.PropertyGroup):
    export_type: bpy.props.EnumProperty(name="File Type", items=EXPORT_TYPES)
    root_node_type: bpy.props.EnumProperty(
        name="GLTF Root Node Type",
        items=ROOT_NODE_TYPES,
    )
    base_scene_res_path: bpy.props.StringProperty(
        name="Base Scene Resource Path",
        default="",
        description="When set to a valid Godot Resource URI, the imported scene will inherit from this base scene.  If not set, the scene will not inherit from any other scene.",
    )
    anim_lib_res_path: bpy.props.StringProperty(
        name="Animation Library Resource Path",
        default="",
        description="Godot Resource path for the animation library exported tracks will be added to",
    )


class GW_OT_setup_collection_export(bpy.types.Operator):
    bl_idname = f"{PREFIX.lower()}.setup_collection_export"
    bl_label = "Setup Collection Export"
    bl_description = (
        "Create or update the Godot Workflow export for the active collection"
    )

    @classmethod
    def poll(cls, context):
        export_props: GW_PG_export_properties = context.collection.godot_workflow_props
        return bool(export_props.export_type != "NONE")

    def execute(self, context):
        export_props: GW_PG_export_properties = context.collection.godot_workflow_props

        # set the as_scene flag if we're exporting as a scene
        context.collection["as_scene"] = export_props.export_type == "SCENE"

        # GLTF and SCENE are the same other than the `as_scene` flag
        if export_props.export_type == "GLTF" or export_props.export_type == "SCENE":
            return self._setup_asset_export(context, export_props)
        if export_props.export_type == "ANIMATION":
            return self._setup_anim_export(context, export_props)
        else:
            self.report(
                {"ERROR"}, f"Export type not implemented: {export_props.export_type}"
            )
            return {"CANCELLED"}

    def _setup_asset_export(self, context, export_props: GW_PG_export_properties):
        """Configure the export for the active collection"""

        # set an asset id for the collection if one is not already there
        collection = context.collection
        if "asset_id" not in collection:
            collection["asset_id"] = create_asset_id()

        # Get the exporter for the active collection
        exporter = self._get_exporter(collection)

        # update the export properties
        exporter_props = exporter.export_properties
        self._set_common_exporter_props(exporter_props, collection)
        # Apply Modifiers = True
        exporter_props.export_apply = True
        # Export Animations = False
        exporter_props.export_animations = False

        # Just a notice that we're done
        self.report({"INFO"}, f"Updated {collection.name} exporter {exporter.name}")
        return {"FINISHED"}

    def _setup_anim_export(self, context, export_props: GW_PG_export_properties):
        """Configure the export for the active collection"""
        collection = context.collection

        # set an asset id for the export collection if one is not already there
        if "asset_id" not in collection:
            collection["asset_id"] = create_asset_id()

        # Get the exporter for the active collection
        exporter = self._get_exporter(collection)

        # update the export properties
        exporter_props = exporter.export_properties
        self._set_common_exporter_props(exporter_props, collection)

        # When using object-based animation, where multiple objects within the
        # collection are animated using action slots, if we use a value here
        # like BROADCAST, We end up with duplicate animation tracks being
        # exported to the GLTF file, and only one object ends up animated in
        # Godot.
        #
        # Instead, I'm going to use ACTIVE_ACTIONS here, which will merge any
        # actions active within the collection into a single animation.  We're
        # also going to set the name of the exported animation to match the
        # collection name.  We do this to reduce the redundant/confusion around
        # is the Action name the thing, or the collection name the thing?
        # Note: we also update this name during the pre-export hook of the GLTF
        # export user extension. 
        exporter_props.export_animation_mode = "ACTIVE_ACTIONS"
        exporter_props.export_nla_strips_merged_animation_name = collection.name

        exporter_props.export_frame_range = True
        exporter_props.export_anim_slide_to_zero = True
        exporter_props.export_negative_frame = "CROP"

        # We need to set the animation library subpath for the collection.  We
        # bundle all animation tracks for the same rig into one animation
        # library.  So the animation library subpath is based off the RIG-*
        # collection the armature was linked in from.
        export_props: GW_PG_export_properties = collection.godot_workflow_props
        export_props.anim_lib_res_path = ""

        # XXX don't like magic strings here; figure out a good way to expose
        # godot assets/animation library paths in addon prefs.
        #
        # anim_lib = pathlib.Path("assets/lib/animation_libs")
        # anim_lib /= asset_collection.name + "-anim_lib.tres"
        # export_props.anim_lib_res_path = f"res://{anim_lib}"

        # Just a notice that we're done
        self.report({"INFO"}, f"Updated {collection.name} exporter {exporter.name}")
        return {"FINISHED"}

    def _get_exporter(self, collection):
        """Get or create the collection exporter for exporting to Godot"""
        out = None
        for ex in collection.exporters:
            if ex.name == EXPORTER_NAME:
                out = ex
                break
            # When a collection with an exporter is duplicated, an 'empty'
            # exporter is created; empty name, and empty filepath.  If we find
            # one, use that as our exporter
            if ex.name == "" and ex.filepath == "":
                ex.name = EXPORTER_NAME
                out = ex
                break

        if not out:
            bpy.ops.collection.exporter_add(name="IO_FH_gltf2")
            out = collection.exporters[-1]
            out.name = EXPORTER_NAME

        return out

    def _set_common_exporter_props(
        self, exporter_props, collection, output_subdir=True
    ):
        addon_prefs: GW_preferences = bpy.context.preferences.addons[
            __package__
        ].preferences

        exporter_props.export_format = "GLTF_SEPARATE"
        exporter_props.export_extras = True
        exporter_props.filepath = "//" + str(
            addon_prefs.get_output_path()
            / addon_prefs.normalize_path_part(f"{collection.name}.gltf")
        )
        # Only export deformation bones
        exporter_props.export_def_bones = True


class GW_OT_export(bpy.types.Operator):
    bl_idname = f"{PREFIX.lower()}.export"
    bl_label = "Export"
    bl_description = "Export configured collections within the requested scope"

    # Scope of export,
    export_scope: bpy.props.EnumProperty(
        name="Export Scope",
        items=[
            # All configured collections in the blend file
            ("ALL", "All", "", "NONE", 0),
            # The active collection
            ("SINGLE", "Single", "", "NONE", 1),
            # All configured child collections of the active collection
            ("CHILDREN", "Children", "", "NONE", 2),
        ],
    )

    def execute(self, context):
        if self.export_scope == "SINGLE":
            self._export_collection(context.collection)
        elif self.export_scope == "ALL":
            for col in bpy.data.collections:
                if col.godot_workflow_props.export_type != "NONE":
                    self._export_collection(col)
        else:
            self.report(
                {"Error"},
                "Unsupported export scope {self.export_scope}",
            )
        return {"FINISHED"}

    def _export_collection(self, collection):
        # don't export linked collections
        if collection.library:
            return

        # don't export overriden linked collections
        if collection.override_library:
            return

        # try to get the workflow exporter
        exporter_index = next(
            (i for i, e in enumerate(collection.exporters) if e.name == EXPORTER_NAME),
            None,
        )
        if exporter_index is None:
            self.report(
                {"WARNING"},
                f"Exporter {EXPORTER_NAME} not configured for {collection.name}",
            )
            return

        # bpy.context.collection = collection
        with bpy.context.temp_override(
            collection=collection, selected_editable_objects=collection.objects[:]
        ):
            bpy.ops.collection.exporter_export(index=exporter_index)


class GW_PT_export_npanel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Godot Workflow"
    bl_label = "Godot Export"

    def draw(self, context):
        layout = self.layout
        export_props: GW_PG_export_properties = context.collection.godot_workflow_props

        # Collapsable panel for configuring collection export
        init_header, init_panel = layout.panel(
            "init_panel",
            default_closed=False,
        )
        init_header.label(text="Export Configuration")
        if init_panel:
            row = init_panel.row()
            row.label(text=context.collection.name)
            init_panel.prop(export_props, "export_type")
            if export_props.export_type in ["GLTF", "SCENE"]:
                row = init_panel.row()
                # XXX Disabled for now, but we should check for collision
                # objects that aren't parented to BODY-* objects.

                # if (
                #     export_props.root_node_type != "STATIC_BODY"
                #     and self._collection_has_collision_shapes(context.collection)
                # ):
                #     row.alert = True
                row.prop(export_props, "root_node_type")
            if export_props.export_type == "SCENE":
                res_path = export_props.base_scene_res_path
                row = init_panel.row()
                row.alert = res_path != "" and not res_path.startswith("res://")
                row.prop(export_props, "base_scene_res_path")
            # if export_props.export_type == "ANIMATION":
            #     row = init_panel.row()
            #     row.alert = export_props.anim_lib_res_path == ""
            #     row.prop(
            #         export_props,
            #         "anim_lib_res_path",
            #         placeholder=export_props.anim_lib_res_path,
            #     )

            init_panel.operator(GW_OT_setup_collection_export.bl_idname)

        # Buttons to export specific collections
        op = layout.operator(
            GW_OT_export.bl_idname,
            text=f"Export: {context.collection.name}",
            icon="DOT",
        )
        op.export_scope = "SINGLE"
        op = layout.operator(GW_OT_export.bl_idname, text="Export All", icon="OUTLINER")
        op.export_scope = "ALL"
        layout.separator()

    def _collection_has_collision_shapes(self, collection):
        """Check if the collision contains any collision shape objects."""
        addon_prefs: GW_preferences = bpy.context.preferences.addons[
            __package__
        ].preferences

        for obj in collection.all_objects:
            if obj.name.startswith(addon_prefs.collision_object_prefix):
                return True
        return False


classes = [
    GW_PG_export_properties,
    GW_OT_setup_collection_export,
    GW_OT_export,
    GW_PT_export_npanel,
]


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Collection.godot_workflow_props = bpy.props.PointerProperty(
        type=GW_PG_export_properties
    )


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)
    del bpy.types.Collection.godot_workflow_props
