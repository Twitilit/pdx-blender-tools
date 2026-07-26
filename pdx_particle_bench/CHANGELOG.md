# Changelog - PDX Particle Bench

Versioning is `MAJOR.MINOR.PATCH`, matching `bl_info["version"]` in `__init__.py`.

Still on 0.x deliberately: some `.asset` behaviour is unverified or rests on a single
measurement (see *Known limitations*). 1.0.0 is when every field either renders correctly or
is documented as unsupported.

---

## 0.6.0 - the editor

The bench becomes an editor: load or create an effect, edit every part live on the real
locator, and export a valid `.asset` - the full load/create -> edit -> export loop.

- **Master-detail editor in the N-panel** (its own "Particle Bench" tab). Tabs:
  Subsystems / Forces / Animations / Settings. Editing any value re-simulates live.
- **Subsystems** - a scrollable list + a detail pane. Core fields always shown; a "+ Add field"
  menu reveals the tail (position, rotation, velocity direction, emitter shape, pulse, facing,
  3D spin, flags). Add / duplicate / delete subsystems.
- **Texture** - path + Browse (.dds, stored game-relative), additive-vs-alpha, atlas frames.
- **Forces** - a shared pool: edit type/amount/direction/position/local_force, add/delete,
  rename (repoints every subsystem), and link/unlink to subsystems.
- **Animations** - a real draggable curve widget (a hidden node's CurveMapping; vector handles
  match the game's linear interpolation), plus min/max/op/time/repeat; add/delete; link a curve
  to a field (size/alpha/colour/rotation/emission/velocity).
- **New** - start from a template (Blank / Smoke plume / Muzzle flash / Sparks).
- **Export .asset** - write the edited effect back out (canonical form; hand comments are not
  preserved; childsystems skipped).
- **Refire-aware lints** - a sub-frame one-shot that needs Refire to show, and Refire stacking a
  continuous emitter past its cap.
- Fixed a colour-flicker bug (per-particle colour was not carried through the draw batch).

## 0.5.2 - vanilla-coverage features (flipbook, box, forces)

Adds the mechanics a vanilla scan (~480 subsystems across 141 files) found missing, taking the
bench from ~85-90% to ~98% of vanilla effects.

- **Flipbook textures** - `texture x=N y=M` is an N*M frame grid; each particle plays through
  the frames over its `life` and emits that frame's UV sub-rect.
- **`sphere_emitter_*` is spherical coordinates** - three `{ base spread }` ranges (radius, yaw,
  pitch), not a solid ball. `sphere_emitter_pitch={ 0 0 }` (the common case) is a flat RING;
  `{ 0 40 }` a band around the equator, `{ 0 180 }` a full hollow shell.
- **Box emitter** - `emitter_type="box"` spawns uniformly inside the `box_emitter_x/y/z` volume
  (was treated as a point). `box_emitter_*` is a `{ base spread }` range, not a width.
- **Force types point / vortex / turbulence** now apply their own motion (were mis-applied as a
  constant planar push). `amount` is acceleration u/s^2 for all, like planar. Force
  `position`/`direction` use the FORCE axes (x=right, y=up, z=fwd). A vortex is mostly radial
  with a ~5deg lean; a TILTED vortex axis is not modelled and lints. Turbulence is a per-particle
  random wander with a shared component (reproduces character and spread, not the exact sequence).
- **`mass` divides force, not velocity** - `a = F/m`; an authored `velocity` is untouched.
  Applied to every force type including `friction` (decay rate `amount/mass`).
- **Colour channels** take the full `{ base[,curve] spread }` grammar; `r`/`g`/`b` alias
  `x`/`y`/`z`. Braced channels used to render BLACK (25 subsystems, including the explosion
  fireballs built from `G`/`B` curves).
- **Pulsed emission** (`emission_pulse_duration`/`_silence`) is honoured, but only works when
  BOTH are set - one alone emits continuously (vanilla sets one alone 9 times out of 13). Lints
  the half-pair.
- **`rotation_speed_yaw`/`_pitch`** spin the quad's 3D FACING (not the texture), on `billboard=no`
  quads. Needed per-particle orientation, enabled only where the facing actually spins.
- **Animation curves**: `minValue`/`maxValue` now remap the curve (`min + curve*(max-min)`); ~43
  vanilla anims (e.g. a shockwave `size` with `maxValue=20`) were under-scaled. Curves also
  honoured on `rotation` and on `emission` (over the emitter's timeline). A force `amount` curve
  is DEAD (always uses the base).
- **`velocity` curve** works on `time="spawn"` (scales spawn speed over the emitter timeline),
  dead on `time="system"`.
- **Animation `op`** - `MUL` and `ABS` are special, everything else (and a missing op) is ADD.
  Was falling back to MUL.
- **Format tail**: `time="life_abs"` and `repeat="yes"` complete the time bases;
  `rotation_speed_roll` folds into the in-plane roll; `time="system"+repeat="yes"` loops on the
  global clock; `type="spin"` force is a constant-angular-rate orbit (`amount` = rad/s).
- **childsystem** (sub-emitters) - a nested subsystem that emits from each parent particle's
  moving position (child `start`/`duration` are vs. the parent's age). Fixed a broad bug it
  exposed: `velocity_yaw`/`_pitch` bases were dropped (only the spread applied), so 189 vanilla
  subsystems with a non-zero `velocity_pitch` base previewed flat.
- **`trail=yes`** draws nothing in game, so the bench skips it (like `hide`) and labels it.
- **`hide=yes`** is honoured (skipped entirely) and labelled in the panel.
- **`alpha={ base,curve spread }`** (braced form) used to parse as fully transparent - 61 vanilla
  subsystems previewed invisible. Fixed.
- **Re-roll** button - draws a different random outcome while staying deterministic within a roll
  (scrubbing replays the same thing).
- **Constants** moved to `constants.json` (re-read on Restart/Load), split into measured vs fitted.

## 0.5.1 - finished the measuring layer

Every simulation constant re-checked in game against a calibration ruler (rings r=1..10). All
confirmed 1:1 except emission.

- **`emission` is a literal particles/second** - `ENGINE_EMISSION_MUL` corrected from 3 to 1 (the
  old 3 was event-spam from a rapid-fire weapon, not a per-subsystem rate). Affects density only.
- **Round-trip model for Bench** button - exports the selected model through io_pdx_mesh and
  re-imports it into a fresh file, the coordinate state the bench is calibrated for.
- **Scene-background** luminance slider - judge additive effects against grey instead of black.
  An approximation, not the engine's tonemap.
- **The emitter forward is `-fwd`** - HoI4's universal convention, not a mesh-pipeline quirk. The
  brief toggle was removed and it is hardcoded again.
- **Simpler panel** - calibration knobs (world/force/friction/emission/size, flip yaw/plume,
  spread mode) removed now that the ruler pinned them all.
- **Browse** button - opens the file dialog in the mod's `gfx/particles`, with a preference to
  start from vanilla instead.

## 0.5.0

- **`billboard=no` + `local_space=no` is oriented by WORLD axes** (the locator's rotation is
  dropped for facing) - right for world-fixed ground explosions; was wrongly applied everywhere.
- **`billboard=no` orientation rule** - quads placed by a real rotation (yaw about up, then pitch
  about the yawed side axis), so `particle_yaw` no longer vanishes at `particle_pitch=90`.
  `Flip plume` demoted to a test-only 180deg in-plane spin.
- **Per-subsystem mute and solo** - eye/solo per row. Draw-time only: the sim keeps stepping
  hidden subsystems, so the remaining stream stays bit-identical.

## 0.4.2

- Warns when Blender's view transform is not **Standard** (4.x defaults to AgX, which desaturates
  colour and misleads a measuring instrument). Does not change the setting.

## 0.4.1

- **Texture colour is multiplied by `color=`, not replaced** - `rgb = color.rgb * texture.rgb`,
  `alpha = color.alpha * texture.alpha`. A tinted texture now shifts the result.

## 0.4.0

> Superseded by 0.5.0: the rule below is right only for `particle_yaw=0`, the one value at which
> the missing yaw term makes no difference.

- **`billboard=no` orientation** - in-plane axis at `rotation=0` is the emitter's side axis
  projected into the quad plane (muzzle direction when side is degenerate).

## 0.3.0

Orientation and axis conventions, mostly corrections found against the running game.

- **`billboard=no` quads are oriented** instead of camera-facing (normal from
  `particle_yaw`/`particle_pitch`).
- **`position` axis mapping** corrected to `(x,y,z) -> (forward,up,right)`, forward inverted.
- **Force `direction`** uses a different mapping - locator local `(X,Z,Y)`.
- **`local_force=no` is not world space** - the bone rest rotation applies either way.
- **Emitter direction is `-forward`** - a `yaw=0` emitter on `+forward` sprayed out the back.
- **Yaw negated by default** (io_pdx_mesh's `SPACE_MATRIX` is a mirror). `Flip yaw` restores it.
- Added `Flip plume`; renamed identifiers/panel/add-on for general use (no longer mod-specific).

## 0.2.0

Textures and configuration.

- **Mod root / Vanilla root preferences** - `.asset` texture paths resolve mod-first then vanilla,
  as the game does (most textures live in vanilla).
- **Real `.dds` textures** (Blender reads DDS natively); falls back to a procedural sprite when a
  texture cannot be resolved.
- **Axis preset derived** from io_pdx_mesh's `SPACE_MATRIX` rather than guessed.

## 0.1.0

Initial Blender port of a simulation validated in a browser prototype.

- Parser for the Paradox bracket format (subsystems, animation curves, forces).
- Simulation with constants measured against the game (velocity 1:1, planar force as
  acceleration, exponential friction, size in world units, symmetric ranges).
- **Drawn through the `gpu` module** rather than EEVEE - the only way to get true additive blend.
- Effects ride a chosen locator (so `local_space=yes/no` is distinguishable).
- Timeline-driven with deterministic re-simulation; per-subsystem toggles; lints for engine traps
  (e.g. mixing `local_space=yes/no` with alpha-blended subsystems).

---

## Known limitations

- **Dead fields, not implemented** (authored but inert in game): `invert` (only ever inside
  comments), `slave_particles` (0 on all 468 uses, tested), `division` (always 16, tested), and a
  force `amount` curve (tested).
- **Not curve-driven yet**: `emitter_yaw`/`_pitch` (1 each, `spawn`, likely live) and
  `particle_yaw` (1, the searchlight sweep, `life`) still use their static base.
- **`time="system"`** (non-repeat) does nothing observable; the one `system` alpha and one
  `system` velocity are treated as static. `repeat="yes"` loops correctly.
- **Pre-scaled `rotate` curves** - a few are authored already scaled to `maxValue`, so the remap
  scales them twice and they over-spin. Cosmetic (they only read as "spins fast").
- **Tilted `vortex` axis** is not modelled correctly (lints). An upright axis is fine.
- **One `particle={}` block per file** is loaded - no picker, so keep one particle per file.
- **`rotation_speed_yaw`/`_pitch`** axis handedness matches the game only up to the quad's rest
  facing. Right for every vanilla use (searchlights yaw-only; snow/mud symmetric).
- **Turbulence** reproduces the right character and reach, not the engine's actual random sequence.
- **`sort` modes** are not distinguished - the bench always depth-sorts (irrelevant for additive,
  which is order-independent).
- **Load throttling / LOD** - every preview is one effect in isolation. `max_amount` is honoured,
  but any additional in-game throttling under load is not modelled.
- **Viewport only** - a measuring instrument, not a render path.
- **Coloured-texture halo** can look more saturated on the bench's black background than over the
  game's bright terrain (`bg_luminance` mitigates) - presentation, not a colour-maths error.
