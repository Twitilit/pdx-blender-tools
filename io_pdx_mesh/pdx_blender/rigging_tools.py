"""
Rigging helpers added to the io_pdx_mesh addon by this fork - general operators for
preparing Paradox/Clausewitz assets, kept in their own module to stay easy to diff
against upstream. Shown in the "Rigging tools" sidebar panel (N).

    Orient bone to 3D cursor      point a bone's tail at the 3D cursor
    Flip bone (swap head/tail)    swap head/tail, preserving roll
    Align weapon by 2 points      midpoint -> origin, front point -> +Y
    Extract selected to .blend    split selection into a separate .blend

Part of io_pdx_mesh, GPL-3.0-or-later. Upstream addon (C) ross-g.
"""

import os
import subprocess

import bmesh  # type: ignore
import bpy  # type: ignore
from bpy.props import StringProperty  # type: ignore
from bpy.types import Operator, Panel  # type: ignore
from bpy_extras.io_utils import ExportHelper  # type: ignore
from mathutils import Matrix, Vector  # type: ignore

EPSILON = 1e-6

# Ceiling for the background extract subprocess (typical run is 2-5s).
EXTRACT_SUBPROCESS_TIMEOUT_SEC = 60


""" ====================================================================================================================
    Operators.
========================================================================================================================
"""


class IOPDX_OT_orient_bone_to_cursor(Operator):
    """Point the active bone's tail at the 3D cursor; head and length unchanged."""

    bl_idname = "io_pdx_mesh.orient_bone_to_cursor"
    bl_label = "Orient bone to 3D cursor"
    bl_description = (
        "Rotate the active bone so it points from its head toward the 3D cursor. "
        "Bone length is preserved; roll is unchanged. "
        "Must be run in Edit Mode on an armature with one active bone."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == "ARMATURE"
            and context.mode == "EDIT_ARMATURE"
            and context.active_bone is not None
        )

    def execute(self, context):
        armature_obj = context.active_object
        edit_bone = context.active_bone  # EditBone
        cursor_world = context.scene.cursor.location

        # 3D cursor lives in world space; bone head/tail live in armature-local
        # space. Convert before doing any math.
        cursor_local = armature_obj.matrix_world.inverted() @ cursor_world

        # Preserve current bone length so the visual size doesn't change.
        current_length = (edit_bone.tail - edit_bone.head).length
        if current_length < EPSILON:
            self.report({"ERROR"}, "Bone has zero length; cannot orient.")
            return {"CANCELLED"}

        direction = cursor_local - edit_bone.head
        if direction.length < EPSILON:
            self.report({"ERROR"}, "3D cursor is at the bone head; no direction to orient toward.")
            return {"CANCELLED"}

        direction.normalize()
        edit_bone.tail = edit_bone.head + direction * current_length

        self.report(
            {"INFO"},
            "Bone '{0}' oriented toward 3D cursor (length preserved, roll unchanged).".format(edit_bone.name),
        )
        return {"FINISHED"}


class IOPDX_OT_flip_bone_head_tail(Operator):
    """Swap head and tail of selected bones, preserving roll - to migrate to the
    vanilla bone convention without rebuilding armatures. Connected children are
    auto-disconnected first (world position kept) so they don't snap."""

    bl_idname = "io_pdx_mesh.flip_bone_head_tail"
    bl_label = "Flip bone (swap head/tail)"
    bl_description = (
        "Swap head and tail on each selected bone, preserving roll. "
        "Connected children are auto-disconnected (position preserved) so they don't snap. "
        "Must be run in Edit Mode on an armature with at least one selected bone."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == "ARMATURE"
            and context.mode == "EDIT_ARMATURE"
            and len(context.selected_editable_bones or []) > 0
        )

    def execute(self, context):
        selected = list(context.selected_editable_bones)
        flipped = 0
        disconnected_children = 0

        for bone in selected:
            # Disconnect connected children first so Blender doesn't drag them when
            # we move the parent's tail; they keep world position and parent link.
            for child in bone.children:
                if child.use_connect:
                    child.use_connect = False
                    disconnected_children += 1

            # Swap head and tail. Roll is left as-is per the user spec.
            old_head = bone.head.copy()
            old_tail = bone.tail.copy()
            bone.head = old_tail
            bone.tail = old_head
            flipped += 1

        msg = "Flipped {0} bone(s)".format(flipped)
        if disconnected_children:
            msg += "; auto-disconnected {0} connected child(ren)".format(disconnected_children)
        self.report({"INFO"}, msg + ".")
        return {"FINISHED"}


class IOPDX_OT_align_weapon_by_two_points(Operator):
    """Align a weapon mesh by two selected verts: midpoint -> object origin,
    back->front (active vert = front) along +Y, then reset the transform to identity."""

    bl_idname = "io_pdx_mesh.align_weapon_by_two_points"
    bl_label = "Align weapon by 2 points (midpoint->origin, front->+Y)"
    bl_description = (
        "In Edit Mode on a mesh, select exactly 2 vertices. The ACTIVE one is the "
        "muzzle (front); the other is the back. The mesh is translated/rotated so "
        "the midpoint is at the object origin and the back->front axis is +Y. "
        "The object's transform is reset to identity (world origin, no rotation)."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == "MESH"
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        selected_verts = [v for v in bm.verts if v.select]
        if len(selected_verts) != 2:
            self.report(
                {"ERROR"},
                "Select exactly 2 vertices (active = muzzle/front, other = back). "
                "Currently selected: {0}.".format(len(selected_verts)),
            )
            return {"CANCELLED"}

        # Active vertex = last entry in select_history that is a selected BMVert.
        # This is the one Blender highlights white in the viewport.
        active = None
        for elem in reversed(bm.select_history):
            if isinstance(elem, bmesh.types.BMVert) and elem.select:
                active = elem
                break
        if active is None:
            self.report(
                {"ERROR"},
                "No active vertex in selection history. Click the muzzle vertex last "
                "(shift-click) so it becomes active, then retry.",
            )
            return {"CANCELLED"}

        front = active.co.copy()
        back = next(v.co for v in selected_verts if v != active).copy()

        direction = front - back
        if direction.length < EPSILON:
            self.report({"ERROR"}, "Front and back vertices coincide; no direction to align.")
            return {"CANCELLED"}
        direction.normalize()

        midpoint = (front + back) * 0.5
        y_axis = Vector((0.0, 1.0, 0.0))

        # Rotation that takes the current direction onto +Y.
        rotation = direction.rotation_difference(y_axis).to_matrix().to_4x4()
        # Translate so the midpoint ends up at origin, then rotate.
        # Order matters: translate first (in pre-rotation frame), then rotate.
        transform = rotation @ Matrix.Translation(-midpoint)

        for v in bm.verts:
            v.co = transform @ v.co

        bmesh.update_edit_mesh(obj.data)

        # Reset object transform so local == world: weapon sits at world (0,0,0) facing +Y.
        obj.location = (0.0, 0.0, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        obj.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)

        self.report(
            {"INFO"},
            "Weapon aligned: midpoint at origin, back->front along +Y. Object transform reset.",
        )
        return {"FINISHED"}


class IOPDX_OT_extract_selected_to_blend(Operator, ExportHelper):
    """Write selected objects to a new .blend and remove them from the current scene
    (e.g. splitting a multi-weapon soldier mesh). A background Blender process builds
    the clean file; this session is not auto-saved afterwards (Ctrl+S to persist)."""

    bl_idname = "io_pdx_mesh.extract_selected_to_blend"
    bl_label = "Extract selected to new .blend"
    bl_description = (
        "Write selected objects to a new .blend file and remove them from the current scene. "
        "Requires the current file to be saved first (no unsaved changes). "
        "Uses a background Blender process to produce a clean, openable .blend. "
        "Current scene is not auto-saved; press Ctrl+S afterwards to persist the deletion."
    )
    bl_options = {"REGISTER"}  # no UNDO: file I/O is not undo-safe

    filename_ext = ".blend"
    filter_glob: StringProperty(default="*.blend", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and len(context.selected_objects) > 0
        )

    def invoke(self, context, event):
        # Require a saved file up front so user doesn't open a save dialog
        # only to get an error.
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the current file to disk first (Ctrl+S), then retry.")
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            self.report(
                {"ERROR"},
                "Current file has unsaved changes. Press Ctrl+S to save, then retry.",
            )
            return {"CANCELLED"}

        # Default filename = active object's name (or first selected if no active)
        default_name = "extracted"
        if context.active_object and context.active_object in context.selected_objects:
            default_name = context.active_object.name
        elif context.selected_objects:
            default_name = context.selected_objects[0].name

        self.filepath = default_name + self.filename_ext
        return super().invoke(context, event)

    def execute(self, context):
        selected = list(context.selected_objects)
        if not selected:
            self.report({"ERROR"}, "No objects selected.")
            return {"CANCELLED"}

        # Re-check save state; invoke() may be skipped when called from scripts.
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the current file to disk first.")
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            self.report({"ERROR"}, "Current file has unsaved changes; save it first.")
            return {"CANCELLED"}

        # Normalize target path
        new_path = self.filepath
        if not new_path.lower().endswith(".blend"):
            new_path += ".blend"

        original_path = bpy.data.filepath
        if os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(original_path)):
            self.report({"ERROR"}, "Cannot extract to the currently open file.")
            return {"CANCELLED"}

        selected_names = [o.name for o in selected]

        # Locate the helper script that runs in the background Blender process.
        script_path = os.path.join(os.path.dirname(__file__), "extract_script.py")
        if not os.path.isfile(script_path):
            self.report({"ERROR"}, "Extraction helper script not found at: {0}".format(script_path))
            return {"CANCELLED"}

        blender_exe = bpy.app.binary_path
        if not blender_exe or not os.path.isfile(blender_exe):
            self.report({"ERROR"}, "Could not locate Blender executable (bpy.app.binary_path).")
            return {"CANCELLED"}

        # Build and run the subprocess.
        # Args after "--" are forwarded to the script via sys.argv.
        cmd = [
            blender_exe,
            "--background",
            original_path,
            "--python", script_path,
            "--",
            new_path,
        ] + selected_names

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=EXTRACT_SUBPROCESS_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            self.report(
                {"ERROR"},
                "Background Blender process timed out after {0}s.".format(EXTRACT_SUBPROCESS_TIMEOUT_SEC),
            )
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, "Failed to launch background Blender: {0}".format(exc))
            return {"CANCELLED"}

        if result.returncode != 0:
            # Surface last few lines of stderr so the user can see what went wrong.
            stderr_tail = "\n".join(result.stderr.strip().splitlines()[-10:]) or "(no stderr)"
            self.report(
                {"ERROR"},
                "Extraction failed (exit {0}). Last stderr:\n{1}".format(result.returncode, stderr_tail),
            )
            return {"CANCELLED"}

        if not os.path.isfile(new_path):
            self.report(
                {"ERROR"},
                "Subprocess exited OK but new file was not created at: {0}".format(new_path),
            )
            return {"CANCELLED"}

        # Subprocess succeeded. Now remove the selected objects from this session
        # so the user is left with the "leftover" scene and can Ctrl+S to persist.
        removed_count = 0
        for name in selected_names:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            for coll in list(obj.users_collection):
                coll.objects.unlink(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            removed_count += 1

        self.report(
            {"INFO"},
            "Extracted {0} object(s) to '{1}'. Current scene modified - Ctrl+S to persist.".format(
                removed_count, new_path
            ),
        )
        return {"FINISHED"}


""" ====================================================================================================================
    UI Panel.
========================================================================================================================
"""


class RiggingToolsUI(object):
    bl_category = "PDX Blender Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class IOPDX_PT_rigging_tools(RiggingToolsUI, Panel):
    bl_label = "Rigging tools"
    panel_order = 10  # appear after existing panels (0..5)

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.label(text="Bone setup:")
        col.operator(
            "io_pdx_mesh.orient_bone_to_cursor",
            text="Orient bone to 3D cursor",
            icon="CURSOR",
        )
        col.operator(
            "io_pdx_mesh.flip_bone_head_tail",
            text="Flip bone (swap head/tail)",
            icon="ARROW_LEFTRIGHT",
        )

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Weapon alignment:")
        col.operator(
            "io_pdx_mesh.align_weapon_by_two_points",
            text="Align by 2 verts (mid->0, front->+Y)",
            icon="EMPTY_AXIS",
        )

        layout.separator()

        col = layout.column(align=True)
        col.label(text="File management:")
        col.operator(
            "io_pdx_mesh.extract_selected_to_blend",
            text="Extract selected to new .blend",
            icon="EXPORT",
        )
