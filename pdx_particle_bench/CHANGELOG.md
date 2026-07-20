# Changelog — PDX Particle Bench

Versioning is `MAJOR.MINOR.PATCH`, matching `bl_info["version"]` in `__init__.py`,
which is also shown at the bottom of the add-on's panel.

Staying on **0.x** deliberately: `billboard=no` quad orientation still does not match
the game (see *Known limitations*), so the simulation is not yet fully faithful.
1.0.0 is reserved for the point where every `.asset` field either renders correctly or
is explicitly documented as unsupported.

All versions below were developed in a single session on **2026-07-18**, before the
split into this repository — the numbers mark functional stages, not separate releases.

---

## 0.3.0

Orientation and axis conventions, mostly corrections found by comparing against the
running game.

- **`billboard=no` quads are now oriented instead of facing the camera.** The quad's
  normal is built from `particle_yaw`/`particle_pitch`; the texture's long axis follows
  the emitter's forward direction projected into that plane.
- **Corrected `position` axis mapping** to `(x, y, z)` → *(forward, up, right)*, forward
  inverted. `y = up` had been verified earlier, but *x* versus *z* is indistinguishable
  without a mesh in frame — a sideways offset and a forward one look identical. Attaching
  to a real locator exposed it immediately.
- **Force `direction` uses a different mapping** — the locator's local *(X, Z, Y)*. Derived
  from two independently recorded in-game observations about which local axis points
  world-down at different mounts on one vehicle.
- **`local_force=no` no longer treated as world space.** The bone's rest rotation applies
  either way; the previous world→local conversion threw forces sideways.
- **Emitter direction is measured from `-forward`.** Body-mounted locators run their local
  forward toward the vehicle's rear, so a `yaw=0` emitter built on `+forward` sprayed out
  the back.
- **Yaw is negated by default.** io_pdx_mesh's `SPACE_MATRIX` is a Y↔Z swap with
  determinant −1 — a mirror, not a rotation — so yaw handedness arrives inverted.
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
  Intensity is taken as `luminance × alpha`, which works whether a texture carries its
  shape in the alpha channel or in the luminance.
- Falls back to a procedural radial sprite when a texture cannot be resolved, rather than
  failing to draw.
- **Axis preset derived rather than guessed** — read out of io_pdx_mesh's `SPACE_MATRIX`
  instead of being picked by trial.

## 0.1.0

Initial Blender port of a simulation previously validated in a browser prototype.

- Parser for the Paradox bracket format: subsystems, animation curves, forces.
- Simulation with constants measured against the game: velocity in world units 1:1,
  planar force as acceleration, exponential friction, size in world units, symmetric ±
  random ranges, and `ENGINE_EMISSION_MUL = 3`.
- **Drawn through Blender's `gpu` module rather than EEVEE materials** — the only way to
  get true additive blending, which most of these effects depend on.
- Effects ride a chosen locator, so `local_space=yes` follows the emitter while `=no`
  bakes into world space at spawn — a distinction a mesh-less preview cannot show.
- Driven from the timeline, with deterministic re-simulation when scrubbing backwards.
- Per-subsystem toggles and live particle counts.
- Lints for engine traps that a preview cannot reveal by construction, such as mixing
  `local_space=yes/no` with alpha-blended subsystems, which makes the engine silently
  drop them.

---

## Known limitations

- **`billboard=no` orientation does not match the game.** Plane selection is correct; the
  in-plane axis is not, so crossed-beam configurations render wrongly. Verified by direct
  comparison against the running game. Do not author that geometry from the preview.
- `ENGINE_EMISSION_MUL` rests on one effect's worth of evidence and should be re-measured
  on others.
- Texture atlases (`texture` `x`/`y` greater than 1) are not animated.
- Viewport only — this is a measuring instrument, not a render path.
