"""
Background Blender script for IOPDX_OT_extract_selected_to_blend - runs in a throwaway
--background subprocess, never imported by the addon. Invoked as:
    blender.exe --background <original.blend> --python <this_script> -- \\
        <new_blend_path> <obj_name_1> <obj_name_2> ...
The original scene is already loaded when we arrive; we strip everything except the
named objects, purge orphans, and save-as to the new path.
"""

import sys

import bpy  # type: ignore


def parse_args():
    # sys.argv for a --background --python run looks like:
    #   ['blender', '--background', '<path>', '--python', '<script>', '--', ...args]
    # We only care about args after the "--" sentinel.
    if "--" not in sys.argv:
        raise SystemExit("No '--' separator found in sys.argv; no arguments passed to script.")
    argv = sys.argv[sys.argv.index("--") + 1:]
    if len(argv) < 2:
        raise SystemExit(
            "Expected at least 2 arguments after '--': <new_blend_path> <obj_name> [<obj_name>...]"
        )
    new_path = argv[0]
    keep_names = set(argv[1:])
    return new_path, keep_names


def main():
    new_path, keep_names = parse_args()

    print("[pdx_tools] target file:", new_path)
    print("[pdx_tools] keeping objects:", sorted(keep_names))

    # Sanity: at least one of the requested objects must exist.
    existing = {o.name for o in bpy.data.objects}
    missing = keep_names - existing
    if missing:
        print(
            "[pdx_tools] WARNING: requested objects not present in source file:",
            sorted(missing),
        )
    found = keep_names & existing
    if not found:
        raise SystemExit(
            "None of the requested objects exist in the source file. Aborting."
        )

    # Clear parent links on kept objects that point to objects about to be deleted.
    # Preserve world matrix so visual placement doesn't change.
    for obj in bpy.data.objects:
        if obj.name not in keep_names:
            continue
        if obj.parent is not None and obj.parent.name not in keep_names:
            world_mat = obj.matrix_world.copy()
            print(
                "[pdx_tools] clearing parent of '{0}' (was '{1}')".format(
                    obj.name, obj.parent.name
                )
            )
            obj.parent = None
            obj.matrix_world = world_mat

    # Delete every object not in the keep set.
    # Use list() because we're mutating bpy.data.objects during iteration.
    to_delete = [o for o in bpy.data.objects if o.name not in keep_names]
    print("[pdx_tools] deleting {0} objects".format(len(to_delete)))
    for obj in to_delete:
        bpy.data.objects.remove(obj, do_unlink=True)

    # Purge orphan data blocks: meshes, materials, armatures, etc. whose only
    # users were the now-deleted objects. Recursive to chase dependencies.
    try:
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except Exception as exc:
        print("[pdx_tools] orphan purge failed (non-fatal):", exc)

    # Save the stripped scene as the new .blend.
    print("[pdx_tools] saving to:", new_path)
    bpy.ops.wm.save_as_mainfile(filepath=new_path, compress=True)

    print("[pdx_tools] done.")


if __name__ == "__main__":
    main()
