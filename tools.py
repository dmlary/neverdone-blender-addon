import bpy

from . import prefs


class GW_OT_set_collision_object_visibility(bpy.types.Operator):
    bl_idname = f"{prefs.PREFIX.lower()}.set_collision_obj_visibility"
    bl_label = "Set collision object visibility"
    bl_description = "Hide or show all collision objects in the viewport display"

    hidden: bpy.props.BoolProperty()

    def execute(self, context):
        addon_prefs: prefs.GW_preferences = bpy.context.preferences.addons[
            __package__
        ].preferences
        prefix = addon_prefs.collision_object_prefix

        count = 0
        for obj in context.scene.objects:
            if not obj.name.startswith(prefix):
                continue

            # Set the viewport visibility
            obj.hide_viewport = self.hidden

            # Also configure the default display for collision objects
            obj.display_type = "WIRE"
            obj.color = addon_prefs.collision_object_color
            obj.display.show_shadows = False

        # Just a notice that we're done
        self.report(
            {"INFO"},
            f"{'Hid' if self.hidden else 'Revealed'} {count} collision objects",
        )
        return {"FINISHED"}


class GW_PT_tools_npanel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Godot Workflow"
    bl_label = "Tools"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        op = row.operator(
            GW_OT_set_collision_object_visibility.bl_idname,
            text="Show Collision Objects",
            icon="RESTRICT_VIEW_OFF",
        )
        op.hidden = False
        op = row.operator(
            GW_OT_set_collision_object_visibility.bl_idname,
            text="Hide Collision Objects",
            icon="RESTRICT_VIEW_ON",
        )
        op.hidden = True


classes = [
    GW_OT_set_collision_object_visibility,
    GW_PT_tools_npanel,
]


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)
