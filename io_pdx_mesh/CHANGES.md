# Changes made by this fork

This directory is a **modified copy** of [io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh)
v0.91 by **ross-g**, licensed GPL-3.0-or-later. Upstream has seen no release since
2024-09-23, and v0.91 does not run correctly on Blender 4.x.

Per GPL-3.0 section 5(a), the modifications are listed below. Every change in the source
is also marked inline with a `# FORK:` comment or an explanatory block comment.

## 1. Blender 4.x - import crashed on multi-material meshes

`pdx_blender/blender_import_export.py`, `import_meshfile()`

Blender 4.0 removed passing a context-override dict as a positional argument to an
operator. Upstream calls:

```python
ctx = bpy.context.copy()
ctx["active_object"] = created[0]
ctx["selected_editable_objects"] = created
bpy.ops.object.join(ctx)
```

On Blender 4.x this raises `ValueError: 1-2 args execution context is supported` and
the import aborts. Because the join only runs when `join_materials` is on and a node
produced more than one object, **any mesh with several materials fails to import** -
which for vehicles is nearly all of them.

Replaced with `bpy.context.temp_override(...)`, guarded by `hasattr` so Blender
before 3.2 still takes the original path.

## 2. Blender 4.1 - meshes imported fully flat-shaded

`pdx_blender/blender_import_export.py`, `create_mesh()`

Upstream:

```python
new_mesh.normals_split_custom_set_from_vertices(normals)
try:  # Blender < 4.1
    new_mesh.use_auto_smooth = True
    new_mesh.polygons.foreach_set("use_smooth", [True] * len(new_mesh.polygons))
except AttributeError:
    pass
```

On Blender 4.1+ the first line inside the `try` raises (`use_auto_smooth` was
removed), so the `use_smooth` line **never executes**. Custom split normals load
correctly but every face stays flat, so the normals are effectively ignored and
models import faceted - not matching how they look in game.

`use_smooth` moved out of the `try` and applied *before* the custom normals; the
`try` now guards only `use_auto_smooth` for Blender < 4.1.

Measured on a multi-part tank mesh (Blender 4.2): the hull went from
`smooth = 0/363` to `363/363` and a gun barrel from `0/106` to `106/106`, with
`has_custom_normals = True` in both cases.

## 3. Static mesh pivot fix

`pdx_blender/blender_import_export.py`, `get_mesh_info()`

Upstream always baked `matrix_world` into the exported vertices. That is correct for
skinned meshes, which must line up with the exported skeleton, but it **destroys the
local pivot/origin of static meshes** - e.g. a separate weapon mesh attached to a
body bone, which then anchors at the wrong point in game.

Now `mesh.transform(matrix_world)` is applied only when the object has an armature
modifier (`get_rig_from_mesh()` returns non-None).

## 4. Added rigging helpers

New file `pdx_blender/rigging_tools.py`, plus its background helper
`pdx_blender/extract_script.py`, registered from `pdx_blender/__init__.py`
(3 added lines, marked `# FORK:`).

General-purpose operators, not specific to any mod:

| Operator | What it does |
|---|---|
| Orient bone to 3D cursor | rotates the active bone so its tail points at the 3D cursor; head, roll and length preserved |
| Flip bone (swap head/tail) | swaps head and tail on selected bones, preserving roll; connected children are disconnected so they don't jump |
| Align weapon by 2 points | midpoint of two selected points to the origin, front point to +Y |
| Extract selected to new .blend | moves the selection into a separate .blend via a background Blender process, then removes it from the current scene |

UI: **Rigging tools** panel in the 3D view sidebar (N), category *PDX Blender Tools*.

## 5. Removed the upstream auto-updater

Deleted `updater.py` and its use in `pdx_blender/blender_ui.py`.

Upstream shows an **UPDATE - vX** button in the Info panel that links to the latest
upstream release. In a fork that is a trap: one click replaces this build with
v0.91 - the version whose Blender 4.x bugs items 1 and 2 above exist to fix. It also
removed a network call made while drawing a panel.

The Info panel now shows the version from the manifest and keeps upstream's donate
link.

## 6. Removed Maya support

Deleted `pdx_maya/` and the Maya launch branch in `__init__.py`; dropped
`maya_support_min` and the (already non-existent) `external/numpy_maya/` path from
`blender_manifest.toml`.

This fork is Blender-only and the Maya half was never exercised or tested here.
Keeping unmaintained code for a host nobody here runs is worse than not shipping it -
it would still appear to be supported. Upstream remains the place to get Maya support.

## 7. Trimmed game profiles to Hearts of Iron IV

`clausewitz.json` shipped material definitions for seven engines
(`standard_previewer`, EU4, Stellaris, HoI4, Imperator, CK3, Victoria 3). Only
`hearts_of_iron_4` is kept.

The file feeds the engine dropdown in the UI directly, so the list is now a single
entry and HoI4 is the default rather than `standard_previewer`. Anyone needing another
title can restore its block from upstream's `clausewitz.json` - the format is unchanged.

## 8. Textures resolved across the whole models/ tree

`pdx_blender/blender_import_export.py`, `create_node_texture()` + new `_resolve_texture_in_models()`

Upstream looks for each texture at exactly `texture_dir/<filename>` - the directory of
the imported `.mesh`. HoI4 meshes bake in texture filenames that often live in a SHARED
model subfolder (e.g. one `specular.dds` reused by many vehicles), not next to the mesh,
so the literal path misses and upstream just shows a red placeholder node.

Added a fallback: when the exact path is missing, search by filename under the nearest
`models` ancestor of the path. The tree is indexed once per models root and cached, so
many textures cost a single `os.walk`, not one per texture (the Kaurava mod's ~335-file
models tree indexes in ~2 ms). Existing paths and non-`models` paths pass through
unchanged.
