# pdx-blender-tools

Blender tooling for Paradox / Clausewitz asset work - currently Hearts of Iron IV.

Two independent add-ons live here, each in its own folder - install whichever you need:

| | |
|---|---|
| [`io_pdx_mesh/`](io_pdx_mesh/) | a fork of ross-g's mesh/anim importer-exporter, fixed for Blender 4.x and extended with rigging helpers |
| [`pdx_particle_bench/`](pdx_particle_bench/) | preview a particle `.asset` attached to a real locator, with the engine's simulation constants |

**Scope: Blender only, Hearts of Iron IV only.** The fork drops upstream's Maya half
and its other game profiles rather than shipping code nobody here tests - see
[`io_pdx_mesh/CHANGES.md`](io_pdx_mesh/CHANGES.md) items 6 and 7 if you need them back.

---

## io_pdx_mesh fork

Upstream [io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh) has had no release
since **2024-09-23 (v0.91)** and does not run correctly on Blender 4.x. This fork
exists to keep it working.

**Fixed here:**

- **Import crashed on any multi-material mesh.** Blender 4.0 removed context-override
  dicts as operator arguments; upstream still uses one for `object.join`, so the
  import aborts with `ValueError: 1-2 args execution context is supported`.
- **Meshes imported completely flat-shaded.** A Blender 4.1 removal (`use_auto_smooth`)
  raised inside a `try` that also contained the `use_smooth` call, so face smoothing was
  silently skipped. Custom normals loaded but were never used - models looked faceted
  and did not match the game.
- **Static mesh pivots were destroyed on export.** `matrix_world` was baked into every
  exported mesh; correct for skinned meshes, wrong for standalone ones such as weapons
  attached to a bone.
- **The auto-updater was removed.** Upstream's Info panel offers a one-click *UPDATE*
  button pointing at the latest upstream release - which in a fork would replace this
  build with the very version whose bugs it fixes.

Plus four general rigging operators (orient bone to cursor, flip bone, align weapon by
two points, extract selection to a new `.blend`).

Full detail, including before/after measurements: [`io_pdx_mesh/CHANGES.md`](io_pdx_mesh/CHANGES.md).

## PDX Particle Bench

Particle `.asset` files are normally authored blind - edit, launch the game, look,
repeat. This add-on parses the `.asset`, simulates it, and draws it **attached to a
locator on the actual mesh**, so timing, size and direction can be judged without a
launch.

Its constants were **measured against the game**, not guessed. With every slider at
1.0 the simulation matched:

| Parameter | Finding |
|---|---|
| `velocity` | world units 1:1 |
| planar force | acceleration in units/s² |
| friction | exponential decay, `v *= exp(-amount·dt)` |
| `size` | quad size in world units |
| `{base spread}` | symmetric ±, not one-sided |
| `emission` | the engine spawns **~3×** a literal "particles/second" reading |
| entity `scale` | scales the **particles too**, not just the mesh |

The `.asset` axis conventions likewise had to be measured, and they are not uniform:
`position` maps `(x, y, z)` to *(forward, up, right)* with forward inverted, while force
`direction` maps to the locator's local *(X, Z, Y)*. `local_force=no` does **not** mean
world space - the bone's rest rotation applies either way.

It also warns about engine traps a preview cannot show by construction, such as mixing
`local_space=yes/no` with alpha-blended subsystems, which makes HoI4 silently drop them.

**Known limitation:** orientation of `billboard=no` quads does not match the game yet.
The plane selection is correct; the in-plane axis is not, so crossed-beam configurations
render wrongly. Do not author that geometry from the preview until it is fixed.

Rendering uses Blender's `gpu` module rather than EEVEE materials - the only way to get
true additive blending, which most of these effects rely on. It is viewport-only by
design: a measuring instrument, not a render path.

Version history and the full list of known limitations:
[`pdx_particle_bench/CHANGELOG.md`](pdx_particle_bench/CHANGELOG.md). The running
version is shown at the bottom of the add-on's panel — worth checking, since this
add-on tends to exist in several copies at once and editing one does not change what
Blender loaded.

---

## Install

Both are ordinary Blender add-ons (tested on **4.2**).

Each add-on is a self-contained folder. Zip the folder you want and install the zip via
`Edit ▸ Preferences ▸ Add-ons ▸ Install…`, or copy the folder straight into Blender's
`scripts/addons/`. Restart Blender afterwards. Installing one does not pull in the other.

- **`pdx_particle_bench/`** - after enabling it, set **Mod root** and **Vanilla root** in
  its add-on preferences. `.asset` texture paths are game-relative, so they are resolved
  mod-first then vanilla, exactly as the game resolves them; most particle textures live
  in the vanilla install rather than in a mod.
- **`io_pdx_mesh/`** - if you already have upstream installed, replace that folder with
  this one.

Both panels appear in the 3D view sidebar (<kbd>N</kbd>) under **PDX Blender Tools**.

## License

**GPL-3.0-or-later.**

`io_pdx_mesh/` is a modified copy of [io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh),
copyright © ross-g, used and redistributed under GPL-3.0-or-later. Modifications are
listed in [`io_pdx_mesh/CHANGES.md`](io_pdx_mesh/CHANGES.md) and marked inline with
`# FORK:`. The original license text is kept at `io_pdx_mesh/license.txt`.

Everything else here is GPL-3.0-or-later as well - Blender add-ons using `bpy` have to be.
