# ruff: noqa: E402

# incantation to support reloading of all files in the addon
submodule_names = [
    "godot_utils",
    "prefs",
    "export",
    "tools",
    "instance_props",
    "gltf2_export_user_extension",
]

if "bpy" in locals():
    import importlib
    import sys

    for name in submodule_names:
        full_name = f"{__name__}.{name}"
        if full_name in sys.modules:
            print("reload ", full_name)
            module = sys.modules[full_name]
            importlib.reload(module)

import bpy

from . import prefs
from . import tools
from . import export
from . import instance_props
from .gltf2_export_user_extension import glTF2ExportUserExtension

# Make sure the GLTF2 export extension is picked up
__all__ = ["glTF2ExportUserExtension"]


def register():
    prefs.register()
    export.register()
    instance_props.register()
    tools.register()


def unregister():
    tools.unregister()
    instance_props.unregister()
    export.unregister()
    prefs.unregister()
