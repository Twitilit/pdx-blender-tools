# Changelog - PDX Particle Bench

Versioning is `MAJOR.MINOR.PATCH`, matching `bl_info["version"]` in `__init__.py`,
which is also shown at the bottom of the add-on's panel.

Staying on **0.x** deliberately: some `.asset` behaviour is still unverified or rests on
a single measurement (see *Known limitations*). 1.0.0 is reserved for the point where
every field either renders correctly or is explicitly documented as unsupported.

---

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

- `ENGINE_EMISSION_MUL` rests on one effect's worth of evidence and should be re-measured
  on others.
- The `position` and force `direction` axis mappings differ from each other. Each is backed
  by its own measurements, but the asymmetry is unexplained and deserves a dedicated probe.
- Texture atlases (`texture` `x`/`y` greater than 1) are not animated.
- Viewport only - this is a measuring instrument, not a render path.
