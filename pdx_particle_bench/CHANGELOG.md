# Changelog - PDX Particle Bench

Versioning is `MAJOR.MINOR.PATCH`, matching `bl_info["version"]` in `__init__.py`,
which is also shown at the bottom of the add-on's panel.

Staying on **0.x** deliberately: some `.asset` behaviour is still unverified or rests on
a single measurement (see *Known limitations*). 1.0.0 is reserved for the point where
every field either renders correctly or is explicitly documented as unsupported.

---

## 0.5.1 - finished the measuring layer

Every simulation constant was re-checked in game against a purpose-built **calibration
ruler** (concentric rings r=1..10, particles fired from a centre locator, read against the
rings). All confirmed 1:1 except emission, corrected
below. velocity, planar force (accel u/s^2), friction (exp decay, terminal v/amount), size
(world units), and symmetric `{ base spread }` all read exactly as the bench predicts.

### Round-trip a model into a bench-ready file (button)

New **Round-trip model for Bench** button at the top of the panel. It exports the selected
model through io_pdx_mesh and re-imports it into a fresh empty file - the exact coordinate
state the bench is calibrated for - so you can author particles on a model you just built
without hand-running export then import each time. Because the bench's conventions (emitter
`-fwd`, the yaw mirror, `matrix_world` for position/orientation) were all measured against
the io_pdx_mesh-imported state, replaying the real pipeline is correct by construction,
rather than re-deriving those conventions for Blender's authoring space.

It REPLACES the current session with the import, so it refuses to run unless the `.blend` is
saved and clean - the original file on disk is left untouched. Rather than a bespoke file
picker it hands off to **io_pdx_mesh's own export dialog** (with its selected / skeleton /
locators checkboxes); since another operator's modal dialog gives no completion callback, it
watches io_pdx_mesh's recorded `last_export_mesh` and, once that points to a file freshly
written, swaps to a fresh empty file and imports it. io_pdx_mesh (its settings +
`import_meshfile`) is located by scanning `sys.modules`, so it works whether io_pdx_mesh is a
legacy add-on (`io_pdx_mesh`) or a Blender 4.2+ extension (`bl_ext.*.io_pdx_mesh`) - a
hard-coded `import io_pdx_mesh` fails for the latter. The file swap runs from a timer so it
cannot invalidate the operator's context, and the import itself runs on the next tick under a
VIEW_3D `temp_override`: `import_meshfile`'s internal ops (`mode_set` to add bones, `join` for
multi-material meshes) silently no-op from a bare timer context, which otherwise left an empty
rig and no mesh.

### emission: `ENGINE_EMISSION_MUL` is 1, not 3

**`emission` is a literal particles/second - no engine multiplier.** The old value of 3
was `ENGINE_EMISSION_MUL`'s single-effect measurement, taken on a rapid-fire weapon whose
asset SPAMS many events per second; that event-spam was the "3x", not any per-subsystem
rate. The ruler isolates it: one continuous emitter at emission=1, life=2 held ~2 particles
in game (= rate x life x 1). So the bench had been spawning 3x too many particles per
subsystem; it now matches the game. Reach/size/direction were never affected - only density.

### Scene-background preview

A **Scene background** luminance slider (Display section). At 0 the viewport stays dark, so
every faint additive layer is visible - good for authoring. Raise it and ADDITIVE effects
are judged against a grey base, the way the game composites them over terrain: a dim
additive layer that dominates on black nearly vanishes on grey. The mod's terrain is a
fairly grey city, so ~0.3-0.4 fits it; 0.6+ already reads near-white.

Explicitly an APPROXIMATION - the panel and README now say so. The bench cannot match HoI4
pixel for pixel (a different, older engine with its own blend/tonemap), and does not try to;
it is calibrated for behaviour and proportion, not colour-exact reproduction.

This closes the gap the fire_explosion episode exposed. The Basilisk ground fireball (dim
brown, 160,110,70) read as a giant fireball on the dark viewport yet is nearly invisible in
game, washed out by the bright scene - nothing wrong with the effect or the sim, the viewport
background was lying. Deleting or brightening such a layer is now a decision you can make in
the bench instead of only in game.

Implementation: a full-screen grey quad at the far depth plane, LESS_EQUAL with no depth
write, so it fills only the empty background and leaves the emitter mesh visible. A LINEAR
approximation, not the engine's exact tonemap - enough to judge relative prominence, not to
colour-match.

### Simpler panel - calibration knobs removed

The `world scale`, `force`, `friction`, `emission`, and `size gain` multipliers, the
`flip yaw` / `flip plume` toggles, and the `spread` mode are gone from the UI. They existed
to DISCOVER the engine's conventions; now that the ruler pass pinned all of them (all 1:1,
spread symmetric, yaw negated), they are fixed in code and would only confuse a user. What
remains is what a user actually sets: the .asset, the locator, the mesh's axis convention,
optional refire, and the scene-background preview.

### The emitter forward is -fwd - it is HoI4's convention, not our pipeline

Mid-cycle the hardcoded `-fwd` emitter forward was reframed as a Kaurava-pipeline quirk (every
mesh node rotated 180deg about Z) and moved behind a **Mesh nodes rotated 180deg about Z**
preference, defaulting to `+fwd`. An in-game test refuted that: an `emitter_yaw=0` stream fired
from a muzzle node whose 180deg-Z rotation had been REMOVED still sprayed backward, into the
tank. So HoI4 fires `emitter_yaw=0` along the locator's local -Y (= `-fwd`) universally - it is
the engine's convention, independent of how the nodes were built.

The preference is removed and `-fwd` is hardcoded again. Position and orientation ride the
mesh's real `matrix_world` and never needed a flip; the 180deg-Z rotation only ever affected
those, and matrix_world already carries it. So a normal (non-rotated) mesh works with the same
`-fwd` - nothing to toggle.

### Convenience: Browse opens in the right folder

A **Browse** button next to the asset field opens the file dialog straight in the mod's
`gfx/particles` (from the Mod root preference), instead of wherever the OS last left it -
and loads the pick immediately. A **Browse from vanilla particles** preference switches the
starting folder to the vanilla game's `gfx/particles` (texture resolution, mod-first then
vanilla, is unchanged). The asset field is still a plain text box, so a path can be pasted.

## 0.5.0

### local_space=no orientation is world-referenced

**A `billboard=no` quad with `local_space=no` is oriented by WORLD axes; the locator's
rotation is dropped for facing.** The code used to apply the locator rotation to every
oriented quad unconditionally. That is right for `local_space=yes` (beams, muzzle flashes
- they ride the weapon) and wrong for `local_space=no` (ground explosions - world-fixed).

Caught on the Basilisk ground shockwave. Its `big_boom` locator is baked 180deg-rotated,
so the two models disagree by exactly the plane: applying the locator rotation put the
flat ring at `pitch=0`, dropping it puts the flat ring at `pitch=90`. A temporal pitch
sweep in game - one ring fired at 0/45/90/135 in sequence, same spot, so no left/right
labelling was needed - settled it: **pitch=90 lies flat, pitch=0 stands on edge.** Only
the world-referenced model reproduces that.

This one cost three wrong turns. The trap was verifying against the mesh's imported
locator matrix, which the earlier orientation work had always fed through the locator
rotation and which the game does not use for `local_space=no`. Lesson banked: when the
preview disagrees with the game, the locator frame is a suspect, not an axiom.

Only affects `billboard=no` + `local_space=no` subsystems (shockwave rings, and any
directional `local_space=no` quad - the flamer flames are in this set and may want
`local_space=yes` instead; unverified).

### Orientation rule

**`billboard=no` quads are placed by a real rotation, so `particle_yaw` no longer
disappears at `particle_pitch=90`.**

The previous rule derived only the quad's NORMAL from yaw/pitch, then guessed the
in-plane axis as "the emitter's side axis, or the muzzle axis when side is degenerate".
It agreed with every measurement taken on beams, and it was still wrong. The normal
formula contains `cos(pitch)`, which is zero at `pitch=90`, so on a flat quad the yaw
term vanished entirely and `particle_yaw=-90` became a token the preview silently
ignored. The "degenerate fallback" branch existed only to paper over the information
that had just been thrown away.

Caught on the Chimera hull bolter: its flat plume runs ALONG the barrel in game at
`rotation=0`, while the old model insisted it ran across.

Yaw about the up axis, then pitch about the yawed side axis, and take the in-plane
axes from the same rotation. At `pitch=90` the long axis is then
`-fwd*sin(yaw) + right*cos(yaw)`, which is the side axis at `yaw=0` and the muzzle axis
at `yaw=-90`. One rule, no special case, and it reproduces every orientation fact on
record - the multilaser cross genuinely needing `rotation=90`, the bolter being right
at `rotation=0`, both `flash_secondary` planes, the muzzle ring, and the ground
shockwave.

Consequence worth stating plainly: a sweep run under the old rule accused about forty
subsystems across the mod of being written wrong. Almost all of them were correct and
the preview was not. Under the corrected rule the only geometric outliers left are the
lasgun and hellgun beam pairs, and those reference a pre-rotated texture that may well
cancel the difference on screen.

`Flip plume` survives as a plain 180-degree in-plane spin for testing the opposite
convention; it is no longer load-bearing.

### Per-subsystem mute and solo

**Mute and solo.** The feature list already claimed this existed; it did
not. The panel only printed each subsystem's name and live count, with no way to take a
layer away.

That gap made a whole class of question unanswerable. An effect is a stack of subsystems
drawn over each other, and a big `billboard=yes` fire mass will bury a thin oriented quad
completely. Looking at an artillery burst, "the shockwave ring is not lying in the ground
plane" and "the ring is fine and you are looking at the fireball parked in front of it"
produce the same picture. Only removing layers separates them.

Each row now carries an eye toggle and a solo button; solo a second time, or use
*Show All*, to bring everything back. Loading an effect clears the state.

Muting is **draw-time only** - the simulation keeps stepping hidden subsystems. Toggling
is therefore instant, and it cannot shift the shared deterministic particle stream, so
what is left on screen is bit-identical to how it looked with the others visible. A mute
that skipped the sim would silently re-roll the survivors and make the instrument lie.

## 0.4.2

Warns when Blender's view transform is not **Standard**.

Blender 4.x defaults to **AgX**, a filmic tone mapper that deliberately desaturates
colour as it brightens. A sensible default for artwork and a bad one for a measuring
instrument: a saturated red or magenta effect reads noticeably duller in the viewport
than the same effect does in game, and that difference is easy to misread as a fault in
the effect itself.

The panel now says so and points at `Render > Color Management > Standard`. It does not
change the setting - that is a scene-wide choice belonging to the user.

Found while comparing a colour-coded probe against a screenshot: after 0.4.1 the hues
matched, the brightness did not.

## 0.4.1

**Texture colour is multiplied by `color=`, not replaced by it.**

The shader previously reduced the texture to a scalar intensity and drew the
subsystem's `color=` on top, discarding the texture's own hue. The engine multiplies
the two, so a tinted texture shifts the result - a yellow beam texture under a cyan
`color=` renders green, because yellow carries no blue channel.

Now `rgb = color.rgb * texture.rgb`, `alpha = color.alpha * texture.alpha`.

Spotted because a rotation-sweep probe used colour coding to label its quads: in
Blender the labels came out as authored, in game two of the five collapsed into each
other. Checked before changing: on every texture examined so far the shape lives in
the **alpha** channel, so taking shape from alpha and hue from RGB is safe for both
blend modes. For additive rendering the result is arithmetically identical to the old
path on a greyscale texture, and only differs where a texture is actually tinted.

## 0.4.0

> **Superseded by 0.5.0.** The rule below is right for every case it was tested on and
> wrong in general: all of its probes used `particle_yaw=0`, the one value at which the
> missing yaw term makes no difference. Keep the measurements, discard the model. The
> "consequence for existing effects" section below is likewise void - `pitch=90
> rotation=0` is correct whenever `particle_yaw=-90`.

**`billboard=no` orientation now matches the game.**

The in-plane axis at `rotation=0` is the emitter's **side** axis projected into the
quad plane, not the muzzle direction as previously assumed. When side lies along the
plane normal it is degenerate and the muzzle direction is used instead.

That single rule reproduces both planes. The old model happened to be right in the
`pitch=0` plane - where side *is* degenerate - and was exactly 90 degrees wrong in the
`pitch=90` plane, which is why crossed-beam configurations rendered as a "T".

Established by a rotation sweep run in game: five static quads at one muzzle, four of
them in the `pitch=90` plane at `rotation` 0 / 45 / 90 / 135 plus a reference quad in
the known-good main config. Only `rotation=90` ran along the shot; `rotation=0` ran
across; 45 and 135 sat diagonally, confirming `rotation` really is an in-plane roll.

Verified against every case measured so far: main-plane beams along the shot,
`pitch=90` at rotation 0 across and at 90 along.

### Consequence for existing effects

Any `pitch=90` quad meant to run along the shot needs `rotation={ 90 0 }`. Two
implications worth checking in your own assets:

- Cross planes on beams: `particle_yaw=0 particle_pitch=90 rotation={ 90 0 }` is
  correct after all - despite `rotation=0` looking more intuitive.
- An elongated muzzle-flame quad at `pitch=90 rotation=0` is rendered **across** the
  barrel by the engine. It is easy to miss on a short, wide plume, unlike on a beam.

## 0.3.0

Orientation and axis conventions, mostly corrections found by comparing against the
running game.

- **`billboard=no` quads are now oriented instead of facing the camera.** The quad's
  normal is built from `particle_yaw`/`particle_pitch`; the texture's long axis follows
  the emitter's forward direction projected into that plane.
- **Corrected `position` axis mapping** to `(x, y, z)` -> *(forward, up, right)*, forward
  inverted. `y = up` had been verified earlier, but *x* versus *z* is indistinguishable
  without a mesh in frame - a sideways offset and a forward one look identical. Attaching
  to a real locator exposed it immediately.
- **Force `direction` uses a different mapping** - the locator's local *(X, Z, Y)*. Derived
  from two independently recorded in-game observations about which local axis points
  world-down at different mounts on one vehicle.
- **`local_force=no` no longer treated as world space.** The bone's rest rotation applies
  either way; the previous world->local conversion threw forces sideways.
- **Emitter direction is measured from `-forward`.** Body-mounted locators run their local
  forward toward the vehicle's rear, so a `yaw=0` emitter built on `+forward` sprayed out
  the back.
- **Yaw is negated by default.** io_pdx_mesh's `SPACE_MATRIX` is a Y<->Z swap with
  determinant -1 - a mirror, not a rotation - so yaw handedness arrives inverted.
  Confirmed on a mirrored pair of exhaust effects. `Flip yaw` restores the raw sign.
- Added `Flip plume` for the rare mount where an elongated quad points the wrong way.
- Renamed for general use: identifiers, panel category and add-on name no longer refer
  to any particular mod.

## 0.2.0

Textures and configuration.

- **Add-on preferences: Mod root and Vanilla root.** `.asset` texture paths are
  game-relative, so they are resolved mod-first then vanilla, exactly as the game does.
  Most particle textures live in the vanilla install rather than in a mod.
- **Real `.dds` textures.** Blender reads DDS natively, so no conversion step is needed.
  Intensity is taken as `luminance * alpha`, which works whether a texture carries its
  shape in the alpha channel or in the luminance.
- Falls back to a procedural radial sprite when a texture cannot be resolved, rather than
  failing to draw.
- **Axis preset derived rather than guessed** - read out of io_pdx_mesh's `SPACE_MATRIX`
  instead of being picked by trial.

## 0.1.0

Initial Blender port of a simulation previously validated in a browser prototype.

- Parser for the Paradox bracket format: subsystems, animation curves, forces.
- Simulation with constants measured against the game: velocity in world units 1:1,
  planar force as acceleration, exponential friction, size in world units, symmetric +/-
  random ranges, and `ENGINE_EMISSION_MUL = 3`.
- **Drawn through Blender's `gpu` module rather than EEVEE materials** - the only way to
  get true additive blending, which most of these effects depend on.
- Effects ride a chosen locator, so `local_space=yes` follows the emitter while `=no`
  bakes into world space at spawn - a distinction a mesh-less preview cannot show.
- Driven from the timeline, with deterministic re-simulation when scrubbing backwards.
- Per-subsystem toggles and live particle counts.
- Lints for engine traps that a preview cannot reveal by construction, such as mixing
  `local_space=yes/no` with alpha-blended subsystems, which makes the engine silently
  drop them.

---

## Known limitations

- The `position` and force `direction` axis mappings differ from each other - position
  `(x,y,z)->(forward,up,right)`, force `->(right,up,forward)`, an X/Z swap. CONFIRMED real
  in game (2026-07-20) by a side-by-side probe: a static position marker and a force-driven
  trail on the same asset axis came out perpendicular, with the up axis agreeing as a
  control. So the bench is right to map them differently; the mechanism is most likely the
  io_pdx_mesh SPACE_MATRIX Y/Z swap. Not a bug, just a documented quirk.
- Texture atlases (`texture` `x`/`y` greater than 1) are not animated.
- Viewport only - this is a measuring instrument, not a render path.
