import re
import sys
import rich
from typing import Optional
from pathlib import Path


class GodotType:
    """Godot native type; provides conversion to blender property type"""

    def __init__(self, type: str, default=None):
        self.type = type
        if default is None:
            default = 0
        self.default = default

    def __repr__(self) -> str:
        return f"GodotType(type='{self.type}')"

    def get_prop_type(self):
        """Return the bpy.props type used to represent this type"""
        # we lazy import this to make it possible to test the rest of this
        # file without blender's packages.
        import bpy

        if self.type == "Animation":
            return bpy.props.PointerProperty
        elif self.type == "bool":
            return bpy.props.BoolProperty
        elif self.type == "object":
            return lambda **kwargs: bpy.props.PointerProperty(
                type=bpy.types.Object, **kwargs
            )
        else:
            raise RuntimeError(f"Unknown property type: {self.type}")


class GdScript:
    """Holds data parsed from a gdsdript file."""

    # XXX need to expand this to pull default values for exports
    EXPORT_PATTERN = re.compile(r"^@export\s+var\s+(\w+)")

    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self.class_name: str = ""
        self.extends: str = ""
        self.props: dict[str, GodotType] = {}
        self._load(path)

    def _load(self, path: Path):
        if path.suffix != ".gd":
            raise RuntimeError(f"Unsupported script file type: {path}")

        gltk_type = None
        with open(path) as f:
            for line in f.read().splitlines():
                if not self.class_name and line.startswith("class_name "):
                    self.class_name = line.split()[1]
                elif not self.extends and line.startswith("extends "):
                    self.extends = line.split()[1]
                elif line.startswith("# blender:"):
                    gltk_type = line.split()[2]
                elif result := self.EXPORT_PATTERN.match(line):
                    if gltk_type:
                        self.props[result[1]] = GodotType(gltk_type)
                        gltk_type = None


class Scene:
    SCRIPT_PATTERN = re.compile(r'^script\s*=\s*ExtResource\("(.*?)"\)*')

    def __init__(self, project_path: Path, path: Path) -> None:
        if path.suffix != ".tscn":
            raise RuntimeError(f"Unsupported scene file type: {path}")

        self.path: Path = path
        self.root_instance: Optional[Path] = None
        self.script: Optional[Path] = None
        self.props: dict[str, str] = {}
        self._load(project_path)

    def _load(self, project_path: Path):
        print(f"loading scene: {self.path}")

        entries: dict[str, str] = {}
        seen_root_node = None
        with open(self.path) as f:
            for line in f.read().splitlines():
                if len(line) < 2:
                    continue
                elif line[0] == "[" and line[-1] == "]":
                    type, *props = line[1:-1].split()
                    if type == "ext_resource":
                        id = next(
                            (prop[4:-1] for prop in props if prop.startswith("id=")),
                            None,
                        )
                        if not id:
                            continue
                        path = next(
                            (prop[6:-1] for prop in props if prop.startswith("path=")),
                            None,
                        )
                        if not path:
                            continue
                        entries[id] = path
                    elif not seen_root_node and type == "node":
                        seen_root_node = True
                        instance = next(
                            (
                                prop[22:-2]
                                for prop in props
                                if prop.startswith("instance=")
                            ),
                            None,
                        )
                        if not instance:
                            continue
                        res_path = entries.get(instance)
                        self.root_instance = project_path / res_path[6:]
                elif not self.script and line.startswith("script"):
                    result = self.SCRIPT_PATTERN.match(line)
                    if not result:
                        return

                    id = result[1]
                    res_path = entries.get(id)
                    self.script = project_path / res_path[6:]


class Project:
    """Godot project reference; contains Scenes and GdScripts."""

    def __init__(self, project_root: Path) -> None:
        self.project_root: Path = project_root
        self._scenes: dict[Path, Scene] = {}
        self._scripts: dict[Path, GdScript] = {}
        self._named_class_scripts: dict[str, GdScript] = {}

        # To support `extends <NamedClass>` in scripts, we're going to need to
        # scan all .gd scripts the first time we have to look up a named class.

    def get_scene_exports(self, tscn_path: Path) -> dict[str, GodotType]:
        scenes = []
        path = tscn_path
        while path is not None:
            scene = self.load_scene(path)
            rich.inspect(scene)
            scenes.append(scene)
            path = scene.root_instance
        scenes.reverse()

        props = {}
        for scene in scenes:
            if not scene.script:
                continue
            script = self.load_script(scene.script)
            rich.inspect(script)
            props |= script.props

        return props

    def load_scene(self, path: Path) -> Scene:
        scene = self._scenes.get(path)
        if not scene:
            scene = Scene(project_path=self.project_root, path=path)
            self._scenes[path] = scene
        return scene

    def load_script(
        self, path: Optional[Path] = None, name: Optional[str] = None
    ) -> GdScript:
        # try hitting the cache first
        if path is not None:
            script = self._scripts.get(path)
        elif name is not None:
            script = self._named_class_scripts.get(name)
        else:
            raise RuntimeError("load_script() must supply `path=` or `name=`")

        # No match in the cache, try loading it directly
        if script is None:
            if path:
                script = self._scripts[path] = GdScript(path)
            elif name:
                # named class means we've gotta load all the scripts
                self._load_all_scripts()
                script = self._named_class_scripts.get(name)

        if script is None:
            raise RuntimeError(f"script not found: {path=}, {name=}")
        return script

    def _load_all_scripts(self):
        """reload all scripts, updating _scripts and _named_class_scripts.

        This function loads all scripts found in the project so that we can
        discover all named classes declared in the project.
        """
        self._scripts.clear()
        self._named_class_scripts.clear()

        for path in self.project_root.rglob("*.gd"):
            script = GdScript(path)
            self._scripts[path] = script
            if script.class_name:
                self._named_class_scripts[script.class_name] = script


if __name__ == "__main__":
    print(f"running on: {repr(sys.argv)}")
    # scan_project(sys.argv[1])
    p = Project(Path(sys.argv[1]))
    rich.print(p.get_scene_exports(Path(sys.argv[2])))
