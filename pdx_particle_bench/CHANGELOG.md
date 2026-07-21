# Changelog - PDX Particle Bench

Versioning is `MAJOR.MINOR.PATCH`, matching `bl_info["version"]` in `__init__.py`,
which is also shown at the bottom of the add-on's panel.

Staying on **0.x** deliberately: some `.asset` behaviour is still unverified or rests on
a single measurement (see *Known limitations*). 1.0.0 is reserved for the point where
every field either renders correctly or is explicitly documented as unsupported.

---

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

- `ENGINE_EMISSION_MUL` rests on one effect's worth of evidence and should be re-measured
  on others.
- The `position` and force `direction` axis mappings differ from each other - position
  `(x,y,z)->(forward,up,right)`, force `->(right,up,forward)`, an X/Z swap. CONFIRMED real
  in game (2026-07-20) by a side-by-side probe: a static position marker and a force-driven
  trail on the same asset axis came out perpendicular, with the up axis agreeing as a
  control. So the bench is right to map them differently; the mechanism is most likely the
  io_pdx_mesh SPACE_MATRIX Y/Z swap. Not a bug, just a documented quirk.
- Texture atlases (`texture` `x`/`y` greater than 1) are not animated.
- Viewport only - this is a measuring instrument, not a render path.
