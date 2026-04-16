import pathlib
import rich
import bpy
from bpy.app.handlers import persistent

from . import export
from . import godot_utils as gd
from .prefs import GW_preferences

# Dynamic property group used when setting scene instance properties
LinkedScenePropertyGroup = None

# Custom property used to store godot properties for scene instances
LINKED_SCENE_PROPS_NAME = "godot_scene_props"

PHYSICS_BODY_TYPES = [
    (
        "CHARACTER_BODY",
        "CharacterBody3D",
        "Physics body specialized for characters moved by script",
        "ARMATURE_DATA",
        0,
    ),
    (
        "RIGID_BODY",
        "RigidBody3D",
        "Physics body that is moved by a physics simulation",
        "MATERIAL",
        1,
    ),
    (
        "STATIC_BODY",
        "StaticBody3D",
        "Physics body that cannot be moved by external forces",
        "SHADING_BBOX",
        2,
    ),
]


class GW_PG_node_instance_properties(bpy.types.PropertyGroup):
    body_type: bpy.props.EnumProperty(
        name="Physics Body Type",
        default="STATIC_BODY",
        items=PHYSICS_BODY_TYPES,
    )


class GW_PT_scene_props_npanel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Godot Workflow"
    bl_label = "Godot Instance Properties"

    def draw(self, context):
        layout = self.layout
        addon_prefs: GW_preferences = context.preferences.addons[
            __package__
        ].preferences

        layout.label(text="Node Properties")
        obj = context.active_object
        if obj and obj.name.startswith(addon_prefs.physics_body_prefix):
            layout.prop(context.active_object.godot_instance_props, "body_type")
        else:
            row = layout.row()
            row.alignment = "CENTER"
            row.enabled = False
            row.label(text="None")

        layout.separator()

        layout.label(text="Exported Scene Properties")
        if hasattr(context.scene, "godot_scene_props"):
            global LinkedScenePropertyGroup
            scene_props = context.scene.godot_scene_props
            for key in sorted(LinkedScenePropertyGroup.__annotations__):
                layout.prop(scene_props, key)
        else:
            row = layout.row()
            row.alignment = "CENTER"
            row.enabled = False
            row.label(text="None")
            return


def make_godot_scene_getter(obj, prop_name, default=None):
    def getter(_self):
        props = obj.get(LINKED_SCENE_PROPS_NAME, {})
        return props.get(prop_name, default)

    return getter


def make_godot_scene_setter(obj, prop_name):
    def setter(_self, value):
        if LINKED_SCENE_PROPS_NAME not in obj:
            obj[LINKED_SCENE_PROPS_NAME] = {}
        props = obj[LINKED_SCENE_PROPS_NAME]
        props[prop_name] = value

    return setter


def make_godot_scene_props_updater(obj, prop_name):
    def update(self, context) -> None:
        if LINKED_SCENE_PROPS_NAME not in obj:
            obj[LINKED_SCENE_PROPS_NAME] = {}
        props = obj[LINKED_SCENE_PROPS_NAME]
        props[prop_name] = self[prop_name]

    return update


def active_obj_changed_callback():
    """Called when the active object changes, updates Godot scene prop list"""
    rich.print("Active object: ", bpy.context.active_object)

    # Active object changed, so remove the property group.  The new active
    # object may not be a linked scene with properties.
    global LinkedScenePropertyGroup
    if LinkedScenePropertyGroup is not None:
        del bpy.types.Scene.godot_scene_props
        bpy.utils.unregister_class(LinkedScenePropertyGroup)
        del LinkedScenePropertyGroup
        LinkedScenePropertyGroup = None

    context = bpy.context
    obj = context.active_object
    if obj is None:
        return
    if obj.type != "EMPTY" or obj.instance_type != "COLLECTION":
        rich.print("Not a collection instance")
        return

    # Verify the collection is a SCENE export_type
    collection = obj.instance_collection
    if not collection:
        rich.print("Instance collection not found")
        return

    export_props = collection.godot_workflow_props
    if not export_props:
        rich.print("godot_workflow_props not found")
        return

    if export_props.export_type != "SCENE":
        rich.print("not a scene export")
        return

    # Get the export path for the collection
    export_path = ""
    for ex in collection.exporters:
        if ex.name == export.EXPORTER_NAME:
            export_path = ex.filepath
            break
    if not export_path:
        print("collection is missing Godot Exporter with filepath")
        return
    # export path is relative to the collection library its linked in from.  We
    # need to convert it to an absoulte path
    export_path = bpy.path.abspath(export_path, library=collection.library)

    # Get the .tscn path from the .gltf export path
    tscn_path = pathlib.Path(bpy.path.abspath(export_path)).with_suffix(".tscn")

    # Extract the scene exports from the .tscn file
    addon_prefs: GW_preferences = context.preferences.addons[__package__].preferences
    project = gd.Project(project_root=addon_prefs.godot_path())
    props = project.get_scene_exports(tscn_path)

    # Set up the property group for the scene properties
    LinkedScenePropertyGroup = type(
        "LinkedScenePropertyGroup",
        (bpy.types.PropertyGroup,),
        {},
    )
    for prop_name in sorted(props):
        prop_type = props[prop_name].get_prop_type()
        LinkedScenePropertyGroup.__annotations__[prop_name] = prop_type(
            update=make_godot_scene_props_updater(obj, prop_name),
        )
    rich.print(LinkedScenePropertyGroup.__annotations__)
    bpy.utils.register_class(LinkedScenePropertyGroup)
    bpy.types.Scene.godot_scene_props = bpy.props.PointerProperty(
        type=LinkedScenePropertyGroup,
    )

    # Copy any values from the active object into the scene props
    scene_props = context.scene.godot_scene_props

    # Need to use items() here, because as soon as we have a PointerProperty as
    # a value, the default iterator doesn't return a key/value tuple.  No clue
    # why it's different.
    for k, v in obj.get(LINKED_SCENE_PROPS_NAME, {}).items():
        scene_props[k] = v


@persistent
def load_post_handler_callback(*args):
    """Reset some state when a new file is loaded"""

    # Ensure we're subscribed to active object being changed.  We need to do
    # this in load_post because subscribing in register doesn't work.  We also
    # clear the handler here to prevent multiple subscriptions when we open
    # multiple files in the same session.
    bpy.msgbus.clear_by_owner(subscribe_active_obj_changed_handle)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=bpy,
        args=(),
        notify=active_obj_changed_callback,
    )

    # We loaded a new file, so the active object changed.  We need to clear the
    # current LinkedScenePropertyGroup, and possibly create a new one.
    active_obj_changed_callback()


# handle for our subscription to active object changed
subscribe_active_obj_changed_handle = object()

classes = [
    GW_PG_node_instance_properties,
    GW_PT_scene_props_npanel,
]


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(load_post_handler_callback)
    bpy.types.Object.godot_instance_props = bpy.props.PointerProperty(
        type=GW_PG_node_instance_properties,
    )


def unregister():
    bpy.msgbus.clear_by_owner(subscribe_active_obj_changed_handle)
    for c in classes:
        bpy.utils.unregister_class(c)
    if load_post_handler_callback in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post_handler_callback)
    del bpy.types.Object.godot_instance_props
