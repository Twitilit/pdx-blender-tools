# Changes made by this fork

This directory is a **modified copy** of [io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh)
v0.91 by **ross-g**, licensed GPL-3.0-or-later. Upstream has seen no release since 2024-09-23, and
v0.91 does not run correctly on Blender 4.x.

Per GPL-3.0 section 5(a), the modifications are listed below. Every change in the source is also
marked inline with a `# FORK:` comment.

1. **Blender 4.x: import crashed on multi-material meshes.**
   `blender_import_export.py`, `import_meshfile()`. Blender 4.0 dropped context-override dicts as
   operator arguments, so `object.join` raised `ValueError` and aborted the import of any
   multi-material mesh. Replaced with `bpy.context.temp_override(...)`, guarded for Blender < 3.2.

2. **Blender 4.1: meshes imported flat-shaded.**
   `blender_import_export.py`, `create_mesh()`. A 4.1 removal (`use_auto_smooth`) raised inside a
   `try` that also held the `use_smooth` call, so face smoothing was skipped and the custom
   normals went unused. `use_smooth` moved out of the `try` and applied before the custom normals.

3. **Static mesh pivots destroyed on export.**
   `blender_import_export.py`, `get_mesh_info()`. `matrix_world` was baked into every exported
   mesh - correct for skinned meshes, wrong for standalone ones (e.g. a weapon on a bone). Now
   applied only when the object has an armature modifier.

4. **Added rigging helpers.**
   New `pdx_blender/rigging_tools.py` (+ background helper `extract_script.py`), registered from
   `pdx_blender/__init__.py`. Four general operators: orient bone to 3D cursor, flip bone (swap
   head/tail), align weapon by two points, extract selection to a new `.blend`. UI: **Rigging
   tools** panel (sidebar N, *PDX Blender Tools*).

5. **Removed the upstream auto-updater.**
   Deleted `updater.py` and its use in `blender_ui.py`. Its *UPDATE* button would replace this
   fork with v0.91 - the version whose bugs items 1-2 fix - and it made a network call on panel
   draw. The Info panel keeps the version label and the donate link.

6. **Removed Maya support.**
   Deleted `pdx_maya/`, the Maya launch branch in `__init__.py`, and `maya_support_min` /
   `numpy_maya` from the manifest. This fork is Blender-only; upstream remains the place for Maya.

7. **Trimmed game profiles to Hearts of Iron IV.**
   `clausewitz.json` shipped seven engines; only `hearts_of_iron_4` is kept (now the default).
   Restore any other title's block from upstream if needed - the format is unchanged.

8. **Textures resolved across the whole `models/` tree.**
   `blender_import_export.py`, `create_node_texture()` + new `_resolve_texture_in_models()`.
   Upstream looks only next to the `.mesh`, but HoI4 often bakes filenames that live in a shared
   model subfolder. Added a cached filename-search fallback under the nearest `models` ancestor.
