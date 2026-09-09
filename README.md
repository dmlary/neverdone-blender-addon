# Neverdone: Godot Addon

Neverdone - a workflow for using Blender with Godot seamlessly.

NOTE: This is an early publication of this workflow.
I haven't been able to wrap up the docs and demo project in a reasonable amount of
time.  I'm sharing this so others can still use the workflow while I continue to
chip away at it.

## Overview
To use the workflow you need:
* Blender
  * https://github.com/dmlary/neverdone-blender-addon - addon for configuring exports & extending exported metadata
* Godot
  * https://github.com/dmlary/neverdone-godot-addon - addon for importing GLTF files with extended metadata
  * https://github.com/dmlary/godot-logrr - logging addon (sorry, but needed real logging for debug 🤷‍♂️)

## Talk
[![Neverdone talk at GodotCon Amsterdam 2026](https://img.youtube.com/vi/EcNXqGfFEQw/0.jpg)](https://www.youtube.com/watch?v=EcNXqGfFEQw)

## Features
* Map attribute channels to CUSTOM0/1/2 channels for Godot Shaders

### Materials
#### Godot shader built-in (CUSTOM0/1/2) wiring support
In Blender, you can map mesh attribute channels to be written to specific Godot
shader built-in (CUSTOM0/1/2) channels.  To do this:
* In the Material graph editor, open the side-panel
* Select the Neverdone tab on the graph editor side-panel
* Click the Add Godot Channels button
  * This will create a new node group in your graph
* Add Attribute nodes, along with Separate Color/XYZ nodes
* Wire the individual channels to the CUSTOM0/1/2 channels in the Godot channel
  group

During export, the attribute data will be copied to the appropriate channels.

Attributes will be created on your mesh during export that can be used for
debugging.  The attributes will be named `_CUSTOM<N>.<CH>`.  It is safe to
delete these, but they will be recreated & overwritten on every export.  They
remain on the object for debugging purposes.
