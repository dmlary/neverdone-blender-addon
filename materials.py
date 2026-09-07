"""
Material helpers & panels
"""
import bpy

from . import debug
from .prefs import PREFIX, NPANEL_NAME

VERTEX_NODE_NAME = "Godot Vertex Builtins"

# Custom Property set on Material Group Node to denote this is the shader
# channel map node.
CHANNEL_MAP_PROP_NAME = "_neverdone_channel_map"

def _find_channel_map_node(material):
    """Find the Material Group Node that is our channel map, if present"""
    if material is None or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        debug.print(f"CHECK {node.name}, {node.type}, {node.get(CHANNEL_MAP_PROP_NAME)}, {node}")
        if node.type != "GROUP":
            continue
        if node.get(CHANNEL_MAP_PROP_NAME):
            return node
    debug.print(f"NOTE: No shader channel map node found in {material}")
    return None


_SEPARATE_CHANNEL_NAMES = {
    "Red": "Red",
    "Green": "Green",
    "Blue": "Blue",
    "Alpha": "Alpha",
    "X": "Red",
    "Y": "Green",
    "Z": "Blue",
}

def _resolve_socket_input_channel(socket):
    """
    Given a NodeSocket, walk up the inputs until we can determine the input
    Attribute & channel.
    """
    if not socket.is_linked:
        return None

    # Initialize the queue we'll walk.  By default, we take the red channel if
    # they hook a Color up to one of our sockets.
    queue = [(socket, 'Red')]

    while queue:
        sock, ch = queue.pop()
        if not sock.is_linked:
            continue

        # grab the incoming link; haven't seen multiple here yet, so this may
        # be good enough for the moment.
        link = sock.links[0]
        if link.is_muted:
            continue
        parent = link.from_socket.node

        # terminals: nothing upstream, this is the source
        if parent.type == "ATTRIBUTE":
            return (parent.attribute_name, ch)
        if parent.type == "VERTEX_COLOR":
            return (parent.layer_name, ch)

        # Handle the splitters by figuring out which channel we're using, and
        # adding the input node along with the channel to the queue.
        if parent.type in ("SEPARATE_COLOR", "SEPARATE_XYZ"):
            in_sock = parent.inputs[0]
            if not in_sock.is_linked:
                continue

            # Add the input socket from our parent, along with the channel
            # we're using from the input.
            queue.append((
                in_sock, 
                _SEPARATE_CHANNEL_NAMES[link.from_socket.name]
            ))

    return None 


def build_material_channel_map(material):
    """Return { '_CUSTOM0.RG': [r_src, g_src], '_CUSTOM0.BA': [b_src, a_src], ... }"""
    node = _find_channel_map_node(material)
    if node is None:
        return {}
    node_group = node.node_tree
    if node_group is None:
        # shouldn't happen
        return ()

    # Build a map of the inputs based on the socket name.  This will give us
    # { "UV2": node.inputs[n], "CUSTOM0.R": node.inputs[m], ...)
    sockets = [it for it in node_group.interface.items_tree
        if isinstance(it, bpy.types.NodeTreeInterfaceSocket)]
    inputs = {}
    for i, s in enumerate(sockets):
        p = s.parent
        if isinstance(p, bpy.types.NodeTreeInterfacePanel):
            name = f"{p.name}.{s.name}" if p.name else s.name
            inputs[name] = node.inputs[i]

    # debug.print("INPUT", inputs)

    # build the output map, key is the vec2 Attribute name we will write the
    # data to.
    out = {}

    # XXX _resolve_socket_input_channel() doesn't support UV Map node, and/or
    # Combine XYZ Node.
    out["_UV2"] = [None, None]

    for name in ("CUSTOM0", "CUSTOM1", "CUSTOM2"):
        panel = node_group.interface.items_tree.get(name)
        if not panel:
            debug.print(f"ERROR: Shader channel group {node} missing channel {name}")
            return
        out[f"_{name}.RG"] = [
            _resolve_socket_input_channel(inputs[f"{name}.Red"]),
            _resolve_socket_input_channel(inputs[f"{name}.Green"]),
        ]
        out[f"_{name}.BA"] = [
            _resolve_socket_input_channel(inputs[f"{name}.Blue"]),
            _resolve_socket_input_channel(inputs[f"{name}.Alpha"]),
        ]

    return out


class GW_OT_add_shader_channels_group(bpy.types.Operator):
    bl_idname = f"{PREFIX.lower()}.add_shader_channels_group"
    bl_label = "Add Godot Channels"
    bl_description = """
        Add a Material Group Node for connecting which attributes should map to
        the Godot shader channels (COLOR, UV2, CUSTOM0-2)
    """

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (
            space is not None
            and space.type == 'NODE_EDITOR'
            and context.object is not None
            and context.object.active_material is not None
        )

    def execute(self, context):

        mat = context.object.active_material
        if not mat.use_nodes:
            self.report(
                    {'ERROR'},
                    "Material does not use nodes; cannot add Godot group node"
            )
            return {"CANCELLED"}

        node_tree = mat.node_tree
        ng = self._make_material_group()
        if ng is None:
            self.report( {'ERROR'}, "Failed to create/find Godot group node")
            return {"CANCELLED"}

        node = node_tree.nodes.new('ShaderNodeGroup')
        node.node_tree = ng
        node.label = VERTEX_NODE_NAME

        # Add the flag to denote this is a shader channel map
        node[CHANNEL_MAP_PROP_NAME] = True

        return {"FINISHED"}

    def _make_material_group(self):
        """Get or create the Material Group Node for Godot shader channels."""
        # return the group if it already exists
        group = bpy.data.node_groups.get(VERTEX_NODE_NAME)
        if group:
            return group

        # Create the node group
        group = bpy.data.node_groups.new(VERTEX_NODE_NAME, 'ShaderNodeTree')

        # Explicitly create UV2 socket as Vector.  Combine XYZ can be used to
        # set each channel.
        s = group.interface.new_socket("UV2", socket_type="NodeSocketVector")
        s.description = "UV2 channel (vec2)"

        # For CUSTOM0-2, we need to fan out the RGBA channels, because the
        # Combine Color node only supports RGB.
        for name in ("CUSTOM0", "CUSTOM1", "CUSTOM2"):
            panel = group.interface.new_panel(
                    name,
                    description=f"""
                        Connect {name} built-in vertex data in Godot (vec4)
                    """,
                    default_closed=True)
            for channel in ("Red", "Green", "Blue", "Alpha"):
                group.interface.new_socket(
                        channel,
                        parent=panel,
                        socket_type="NodeSocketFloat",
                        description=f"{channel} channel of {name} (float)")

        return group


class GW_PT_material_npanel(bpy.types.Panel):
    bl_space_type = 'NODE_EDITOR'      # Shader Editor
    bl_region_type = 'UI'              # N-panel region
    bl_context = 'MATERIAL'
    bl_category = NPANEL_NAME
    bl_label = "Godot Shaders"


    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'

    def draw(self, context):
        layout = self.layout
        layout.operator(GW_OT_add_shader_channels_group.bl_idname)
        





classes = [
    GW_OT_add_shader_channels_group,
    GW_PT_material_npanel,
]


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)
