import os
import re
from datetime import datetime
from typing import Any, List, Optional, Tuple
from . import debug
import numpy as np
import bpy
import mathutils

from . import prefs
from . import export
from . import instance_props
from .materials import build_material_channel_map

# Used during material export to get the number of channels in the source
# attribute
CHANNEL_COUNT = {
    "FLOAT": 1, "INT": 1, "BOOLEAN": 1, "INT8": 1,
    "INT16_2D": 2, "INT32_2D": 2,
    "FLOAT2": 2,
    "FLOAT_VECTOR": 3,
    "FLOAT4": 4, "FLOAT_COLOR": 4, "BYTE_COLOR": 4, "QUATERNION": 4,
    "FLOAT4X4": 16,
}


def get_collision_shape_box(obj) -> Optional[dict[str, Any]]:
    """If the mesh describes a cube shape, return collision shape info"""
    mesh = obj.data
    if len(mesh.vertices) != 8:
        return None

    # copy the vertices out into a numpy array
    coords = np.empty(24, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape((8, 3))

    # find the center of the mesh
    center = np.mean(coords, axis=0)

    # calculate the distance squared of each vertex from the center
    dist_sq = np.sum(np.square(coords - center), axis=1)

    # verify all vertixes are the same distance from the center.  We have an
    # allowance of 0.01 blender units to allow for using the shrinkwrap
    # modifier on a cube to create the collision shape.
    delta = np.max(dist_sq) - np.min(dist_sq)
    if delta > 0.01:
        return None

    # XXX can check for right angles if we start having problems with spheres
    # being confused for cubes

    # Calculate the size of the box from the min and max vertices
    size = np.max(coords, axis=0) - np.min(coords, axis=0)

    return {
        "type": "PRIMITIVE",
        "shape": "BOX",
        "center": mathutils.Vector(center),
        "size": size,
    }


def geonode_modifier_get_input(mod, name):
    """Get the value of a geonode input from the supplied modifier"""
    if name not in mod.node_group.interface.items_tree:
        print(f"Unknown input field `{name}` for `{mod.node_group.name}`")
        return None

    item = mod.node_group.interface.items_tree[name]
    return mod[item.identifier]


def get_collision_shape_modifier(obj) -> Optional[dict[str, Any]]:
    """Extract CollisionShape geometry node modifier info if present."""
    if not obj.modifiers:
        return None

    mod = next((m for m in obj.modifiers if m.name.startswith("GN-CollisionShape_")))
    if mod is None:
        print(f"no CollisionShape geometry node modifiers on: {mod.name}")
        return None

    if mod.name.endswith("Capsule"):
        return {
            "type": "PRIMITIVE",
            "shape": "CAPSULE",
            "radius": geonode_modifier_get_input(mod, "Radius"),
            "height": geonode_modifier_get_input(mod, "Height"),
        }
    # XXX Add other shapes as needed
    else:
        print(f"Unsupported CollisionShape geometry node: {mod.name}")


def gather_collision_shape(obj):
    # Check if the collision shape can be represented by a Box
    collision_info = get_collision_shape_box(obj)

    # Check if the object has a collision shape geometry node modifier
    if collision_info is None:
        print("trying colshape modifier")
        collision_info = get_collision_shape_modifier(obj)

    if collision_info is None:
        print("colshape is mesh")
        # it's not a box, make it a mesh
        collision_info = {"type": "MESH"}

    debug.print(f"{collision_info=}")
    return collision_info


def get_library_collection_root(collection):
    """Find the root collection for a library collection"""
    # if there's no library, there's no root
    if not collection.library:
        return None

    # Go through each parent of the collection, looking for those with a
    # matching library
    for parent in bpy.data.collections:
        if collection.name not in parent.children:
            continue
        if parent.library != collection.library:
            continue

        # found one that matches; root will be this parent's root
        return get_library_collection_root(parent)

    # No parents had a .library value that matches; this collection is the
    # root
    return collection


def get_object_asset_collection(obj):
    """For a given object, get the asset collection it came from

    This function will find the collection that the object belongs to, then
    walk up the collection parent chain to find the Asset collection the
    object was linked from.
    """
    if obj is None:
        return None

    # We're only looking at override library here because our primary use-case
    # for this is finding the asset collection for a linked armature.  If we
    # need to handle local collections, this will need to change.
    for collection in obj.override_library.reference.users_collection:
        root = get_library_collection_root(collection)
        if root and "asset_id" in root:
            return root

    return None


def get_object_nodepath(collection, obj) -> str:
    """Given an object, find the NodePath to the object from the collection.

    This function traverses the object tree in the collection until it
    encounters the specified object.  If the object is found, this function
    will return a Godot NodePath to the object from the root of the imported
    GLTF scene.

    If the object is not found, this function will return an empty string.
    """
    queue = [(collection, "/")]
    while queue:
        curr, path = queue.pop()

        if curr == obj:
            return f"{path}{obj.name}"

        if isinstance(curr, bpy.types.Collection):
            queue += [(c, path) for c in curr.children]
            queue += [(c, path) for c in curr.objects]
        else:
            queue += [(c, f"{path}{curr.name}/") for c in curr.children]

    return ""


def enum_get_label(items, value) -> str:
    """Get the label from enum items by value"""
    for ty, label, *_ in items:
        if ty == value:
            return label
    return f"unknown enum value {value}"


class glTF2ExportUserExtension:
    def __init__(self):
        # We need to wait until we create the gltf2UserExtension to import the
        # gltf2 modules.  Otherwise, it may fail because the gltf2 may not be
        # loaded yet
        from io_scene_gltf2.io.com.gltf2_io_extensions import Extension

        self.Extension = Extension

        # Grab the addon preferences for later use
        self.addon_prefs: prefs.GW_preferences = bpy.context.preferences.addons[
            __package__
        ].preferences

        ## tuples of original material and placeholder material; populated in
        ## pre_export_hook(), and reverted in post_export_hook()
        self._swapped_materials = []

        ## Collection instances that were converted to empties during
        ## pre_export_hook()
        self._collection_instances = []

        ## Collision objects that were revealed during pre_export_hook(), tnat
        ## need to be hidden in post_export_hook()
        self._enabled_objects = []

        ## Export properties configured on the export collection
        self.export_props: export.GW_PG_export_properties

    def pre_export_hook(self, export_settings):
        log = export_settings["log"]
        collection = bpy.data.collections[export_settings["gltf_collection"]]
        self.export_props = collection.godot_workflow_props

        # If this is an animation export, ensure the export animation name
        # matches the collection name.  More details in
        # export._setup_anim_export()
        # NOTE: We probably should do something like this for the export
        # destination path.  Right now the user has to click on setup again if
        # they change the name of the collection.
        if self.export_props.export_type == "ANIMATION":
            # force the exported animation name to match the collection name
            export_settings["gltf_nla_strips_merged_animation_name"] = collection.name

            # Also update the collection exporter config so the animation name
            # matches the collection name, so the user isn't too confused when
            # they manually set a name, and it exports as something else.
            # It's not the best solution here, but it's some sort of visual
            # feedback
            exporter = next(
                (e for e in collection.exporters if e.name == export.EXPORTER_NAME),
                None,
            )
            if exporter:
                exporter.export_properties.export_nla_strips_merged_animation_name = (
                    collection.name
                )

        self._pre_process_collection(collection, log)

    def post_export_hook(self, export_settings):
        debug.print("post export hook")
        log = export_settings["log"]
        collection = bpy.data.collections[export_settings["gltf_collection"]]
        self._post_process_collection(collection, log)

    # def gather_material_hook(self, gltf2_material, blender_material, export_settings):
    #     return

    def gather_scene_hook(self, gltf2_scene, blender_scene, export_settings):
        debug.print("gather scene hook")
        collection = bpy.data.collections[export_settings["gltf_collection"]]

        # Add the human-readable export type as an asset_type extra in the scene
        gltf2_scene.extras["asset_type"] = self.export_props.export_type

        # Add the human-readable type for the root node
        gltf2_scene.extras["root_node_type"] = enum_get_label(
            export.ROOT_NODE_TYPES, self.export_props.root_node_type
        )

        # Godot won't re-import the split GLTF file if only the .bin file
        # changes.  As a tradeoff to make the export more reliable, we're going
        # to guarantee a change in the .gltf file every time we export.  The
        # down side of this will be unnecessary changes in the commit log if
        # we're exporting unchanged collections.
        gltf2_scene.extras["_export_time"] = datetime.now().strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        # Due to float accumulation errors, we can't properly calculate the
        # FPS from the GLTF file (32-bit float can't represent 1/30 properly).
        # So we're just going to explicitly store fps & fps_base in the scene
        # extras, and use that to set import FPS in godot
        gltf2_scene.extras["frame_rate_ratio"] = [
            bpy.context.scene.render.fps,
            bpy.context.scene.render.fps_base,
        ]

        # When exporting collections, be sure to include the instance offset in
        # the extras.  This allows the user to "Export as Collection", which
        # uses the bounding box center, then offset it using the instance
        # offset.  This is necessary to make scenes that use collection
        # instances look the same between Blender & Godot.
        if collection.instance_offset.length_squared > 0.0:
            gltf2_scene.extras["instance_offset"] = list(collection.instance_offset)

    def gather_node_hook(self, gltf2_node, blender_object, export_settings):
        debug.print("gather node hook")
        log = export_settings["log"]

        # imports the proper name to align with animation tracks for the rig.
        if blender_object.type == "ARMATURE":
            if "." in gltf2_node.name:
                if re.search(r"\.\d{3}", gltf2_node.name):
                    gltf2_node.name = gltf2_node.name[:-4]

        # Physics body objects; add extras to configure the physics node
        elif blender_object.name.startswith(self.addon_prefs.physics_body_prefix):
            gltf2_node.extras["physics_body"] = {
                "type": enum_get_label(
                    instance_props.PHYSICS_BODY_TYPES,
                    blender_object.godot_instance_props.body_type,
                ),
            }

        # Special handling for PATH- objects; convert the path to points for a
        # Path3D in Godot
        elif blender_object.name.startswith(self.addon_prefs.path_prefix):
            count = len(blender_object.data.splines)
            if count > 1:
                log.warning(
                    f"WARN: {blender_object.name} has {count} splines; only exporting first spline"
                )
            if count == 0:
                log.warning(
                    f"WARN: {blender_object.name} has no splines; path not exported"
                )
            else:
                spline = blender_object.data.splines[0]
                if spline.type != "BEZIER":
                    log.warning(
                        f"WARN: {blender_object.name} has unsupported {spline.type} spline; path not transferred"
                    )
                else:
                    points = []
                    for point in spline.bezier_points:
                        points.append(
                            [point.co[:], point.handle_left[:], point.handle_right[:]]
                        )
                    gltf2_node.extras = {"path_points": points}

        # We need to do a little bit of cleanup of scene instance properties
        elif instance_props.LINKED_SCENE_PROPS_NAME in blender_object:
            extras = gltf2_node.extras[instance_props.LINKED_SCENE_PROPS_NAME]
            for k, v in extras.items():
                if not isinstance(v, bpy.types.Object):
                    continue

                # Convert the object pointer into a string containing a Godot
                # NodePath relative to this object in the mported scene.  The
                # scene root is the collection we are exporting.
                collection = bpy.data.collections[export_settings["gltf_collection"]]
                src_path = get_object_nodepath(collection, blender_object)

                dest_path = get_object_nodepath(collection, v)
                if not dest_path:
                    log.error(
                        f"Scene instance property `{k}` in object "
                        + f"`{blender_object.name}` "
                        + "references object outside of export collection "
                        + f"`{collection.name}`: {v.name}",
                    )
                    extras[k] = "ERROR_UNKNOWN_NODE_PATH"
                    return

                node_path = os.path.relpath(dest_path, src_path)
                extras[k] = node_path

    def gather_node_mesh_hook(self, gltf_mesh, blender_object, export_settings):
        debug.print({
            "fn": "gather_node_mesh_hook",
            "gltf_mesh": gltf_mesh,
            "blender_mesh": blender_object,
            })


        # Walk through all materials on this mesh, and take their channel maps
        # and unify them into a 
        unified_map = {}
        materials = [slot.material for slot in blender_object.material_slots]
        for material in materials:
            mat_map = material.get("_godot_channel_map")
            if not mat_map:
                continue
            for entry in mat_map:
                dest_name = entry["dest"]["attr"]
                dest = (dest_name, entry["dest"]["ch"])
                src_name = entry["src"]["attr"]
                src = (src_name, entry["src"]["ch"])

                # Check for collisions
                if dest in unified_map and unified_map[dest] != src:
                    print("!!! ERROR !!!: Materials on object"
                        f" {blender_object.name} specify different shader"
                        " input channels for the same output channel:"
                        f" output {dest}, inputs {src}, {unified_map[dest]}."
                        " Not mapping attribute to output, first slot wins."
                              )
                    continue

                # No collision, add it to the channel map

                unified_map[dest] = src

        debug.print(f"Unified material map for {blender_object}",
                    unified_map)

        attributes = blender_object.data.attributes

        # We're going to create our output attributes, along with a scratch
        # list in python where we can assemble the channels properly.
        dest_data = {}
        for dest_name, _ in unified_map:
            if dest_name in dest_data:
                continue

            # Make sure the destination property group exists, and has the
            # right storage type & domain.
            dest = attributes.get(dest_name)
            if dest and (dest.data_type != "FLOAT2" or dest.domain != "POINT"):
                # data-type mismatch; gozilla smash
                attributes.remove(dest)
            if not dest:
                dest = attributes.new(dest_name, type="FLOAT2", domain="POINT")

            # Save off the output attribute, and the python array we'll use to
            # piece together the various channels.
            dest_data[dest_name] = (dest, [0.0] * len(dest.data) * 2)

        # XXX NEED THE CHANNEL COUNT IN HERE TOO!!!

        # Alright, since we may be using multiple channels from each source,
        # we're going to read in the source data one time.
        # Maps attribute name => (channel_count, data)
        src_data = {}
        for src_name, _ in unified_map.values():
            if src_name in src_data:
                continue

            # Grab the source attribute
            src = attributes.get(src_name)
            if not src:
                print(f"Error: Attribute `{src_name}` not found on"
                    f" {blender_object}")
                continue

            # Read the data out into a python variable
            ch_count = CHANNEL_COUNT[src.data_type]
            data = [0.0] * len(src.data) * ch_count
            if src.data_type.endswith("COLOR"):
                src.data.foreach_get("color", data)
            else:
                src.data.foreach_get("vector", data)

            # save it off for use later.
            src_data[src_name] = (ch_count, data)


        # Now we copy the channels
        for (dest_name, dest_ch), (src_name, src_ch) in unified_map.items():
            src_stride, src = src_data[src_name]
            if not src:
                continue

            _, dest = dest_data[dest_name]
            if not dest:
                continue

            debug.print(f"COPY {src_name}.{src_ch} ({src_stride}) -> {dest_name}.{dest_ch}")

            dest[dest_ch::2] = src[src_ch::src_stride]

        debug.print(dest_data, src_data)

        # Then write the data back to the output channels
        for dest, data in dest_data.values():
            dest.data.foreach_set("vector", data)


    def gather_mesh_hook(self, gltf2_mesh, blender_mesh, blender_object, vertex_groups, modifiers, materials, export_settings):
        debug.print({
            "fn": "gather_mesh_hook",
            "materials": materials,
        })

        # Move/rename our custom attributes to the names needed by Godot.
        for src, dest in (('_CUSTOM0.RG', 'TEXCOORD_2'),
                          ('_CUSTOM0.BA', 'TEXCOORD_3'),
                          ('_CUSTOM1.RG', 'TEXCOORD_4'),
                          ('_CUSTOM1.BA', 'TEXCOORD_5'),
                          ('_CUSTOM2.RG', 'TEXCOORD_6'),
                          ('_CUSTOM2.BA', 'TEXCOORD_7'),):
            for prim in gltf2_mesh.primitives:
                attrs = prim.attributes
                attrs[dest] = attrs[src]
                del attrs[src]

    def animation_action_hook(
        self, gltf2_animation, blender_object, blender_action_data, export_settings
    ):
        debug.print("anim action hook")
        # For every animation, add an extra that tracks the asset_id of the
        # rig library collection.  This allows the godot importer to look up
        # and update track NodePaths during animation import.
        collection = get_object_asset_collection(blender_object)
        if collection and "asset_id" in collection:
            gltf2_animation.extras = {"rig_asset_ref": collection["asset_id"]}
        else:
            debug.print("ERROR: no asset_id in collection!!!")

        # Also note the loop mode
        gltf2_animation.extras["loop_mode"] = enum_get_label(
            export.ANIM_LOOP_MODES, self.export_props.anim_loop_mode
        )

    def gather_animation_channel_hook(
        self,
        animation_channel,
        channel,
        blender_object,
        bone,
        action_name,
        node_channel_is_animated,
        export_settings,
    ):
        if blender_object.type != "ARMATURE":
            return

        # We want to allow the riggers to hard-code the interpolation mode for
        # specific bones directly on the deformation bone.  This is in support
        # of mixing stop-motion with linear root motion animation.  This will
        # allow the riggers to mark the root bone as linear interpolation, but
        # set the rest of the bones to explicitly be stepped interpolation, or
        # use the fallback stepped animation.
        # Godot will force the channel interpolation using this data on import.
        bone = blender_object.data.bones[bone]
        if "godot_interpolation" in bone:
            if animation_channel.extras is None:
                animation_channel.extras = {}
            animation_channel.extras["interpolation"] = bone["godot_interpolation"]

    def _pre_process_collection(self, collection, log):
        """Pre-process a collection for GLTF export

        This function will perform the following actions on the collection:
        * Replace any collection instance with an empty referecing the asset
        * Replace any materials with an `asset_id` with a placeholder material that
          references the original material
            * limits the duplication of materials across all the exported files
            * prevents the export of images used in the material
        """
        # DOGWALK excludes all child collections from the view layer prior to
        # export; Add it here if encounter a need for it
        addon_prefs = self.addon_prefs

        # set of materials with an asset_id used by objects in the collection
        materials = set()

        # collision objects; we do a second pass on these
        collision_objects = set()

        # iterate through all objects in the collection, preparing them for
        # export
        debug.print("iterating on objects")
        for obj in collection.all_objects:
            debug.print(obj)
            # convert asset collection instances to empties with an asset_ref
            if obj.instance_type == "COLLECTION":
                if not obj.instance_collection:
                    continue
                asset_id = obj.instance_collection.get("asset_id", None)
                if asset_id is None:
                    continue

                # Convert the collection instance to an empty, with an
                # asset_ref custom property
                obj.instance_type = "NONE"
                obj["asset_ref"] = asset_id
                self._collection_instances.append(obj)
                continue

            # save off all materials that have an asset_id set
            for mat in [slot.material for slot in obj.material_slots]:
                if not mat:
                    continue
                if "asset_id" not in mat:
                    log.warning(f"No asset_id set for material `{mat.name}`")
                    continue
                debug.print("added material", mat)
                materials.add(mat)

            # Special handling for collision objects
            if obj.name.startswith(addon_prefs.collision_object_prefix):
                # Add the object to the set for later processing
                collision_objects.add(obj)

                # Apply the scaling to collision shapes before exporting the
                # collision shapes to ensure they have a uniform scale.
                # Note: this could be changed to detect non-uniform scale, and
                # only then apply scale.
                with bpy.context.temp_override(
                    active_object=obj, selected_editable_objects=[obj]
                ):
                    bpy.ops.object.transform_apply(
                        location=False, rotation=False, scale=True
                    )

                # if the object is disabled, enable it and add it to the list
                # to be disabled in the post_export_hook
                # NOTE: if we try to enable the object in the viewport, we can
                # get a crash in Blender 4.5.7 when we get the next element in
                # this collection.all_objects loop.  We're going to add them
                # to the viewport after we're done here.
                if obj.hide_viewport:
                    self._enabled_objects.append(obj)

            # if the object is not a collision object, strip any collision
            # shape custom property
            elif "collision_shape" in obj:
                del obj["collision_shape"]

        # Replace materials with placeholder references to real material in
        # godot.
        # NOTE: user_remap() does not work properly on Mesh objects that have
        # been linked from libraries, even with a overrides enabled.  I've seen
        # user_remap() only replace the material of one mesh in an animation
        # collection have it's material remapped during export.  This happens
        # because if you only Make Library Override for the Selected & Content
        # of a linked asset collection, that override doesn't traverse deep
        # enough to allow the setting of the Material on the Mesh within the
        # Object, within the Linked Collection:
        #
        #    Linked Collection -> Object -> Mesh -> Material
        #    ^-------------------------^    ^--------------^
        #       first Make Lib override     second Make lib override
        #
        # This workflow is absurd to ask of artists, so I'm going to clean up
        # the leaked material on the Godot side instead.
        debug.print("iterating on materials")
        for mat in materials:
            dummy_mat = bpy.data.materials.new(f"MATREF-{mat.name}")
            debug.print("replace material", mat, " with ", dummy_mat)
            mat.user_remap(dummy_mat)
            dummy_mat["asset_ref"] = mat["asset_id"]

            # Build the shader channel map now.  This allows us to say which
            # Attribute channels on a mesh should be mapped to the individual
            # channels on UV2, CUSTOM0-2.  If it's a non-empty map, set it on
            # the dummy material so we can use it during the gather hooks.
            channel_map = build_material_channel_map(mat)
            if channel_map:
                debug.print("CHANNEL MAP", channel_map)
                dummy_mat["_godot_channel_map"] = channel_map

            self._swapped_materials.append((mat, dummy_mat))

        # Enable all disabled collision objects in the collection, and generate
        # the depsgraph.
        debug.print("iterating on collision shape visibility")
        for obj in self._enabled_objects:
            obj.hide_viewport = False

        debug.print("evaluate depsgraph")
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in collision_objects:
            # don't bother for objects linked from an external library
            if obj.library or obj.override_library:
                debug.print(f"not setting collision shape on library obj {obj}")
                continue

            # get the collision shape object with modifiers applied
            eval_obj = obj.evaluated_get(depsgraph)
            debug.print(f"name {obj=} -> {eval_obj=}")
            obj["collision_shape"] = gather_collision_shape(eval_obj)
        debug.print("pre-process collection complete")

    def _post_process_collection(self, collection, log):
        """Revert changes made in pre_process_collection() after GLTF has been exported"""

        # Restore collection empties to collection instances
        for obj in self._collection_instances:
            obj.instance_type = "COLLECTION"

        # Replace placeholder materials with the original materials
        for mat, dummy_mat in self._swapped_materials:
            dummy_mat.user_remap(mat)
            bpy.data.materials.remove(dummy_mat)

        # Disable those objects we enabled in the prehook
        for obj in self._enabled_objects:
            obj.hide_viewport = True
