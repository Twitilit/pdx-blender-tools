# =============================================================================
# PDX Particle Bench - Clausewitz/HoI4 .asset particle preview inside Blender.
#
# Simulates a particle `.asset` and draws it attached to a real locator, so an
# effect can be judged against the mesh it will actually fire from.
#
# The simulation constants were measured against the game rather than guessed:
# velocity -> world units 1:1, planar force = acceleration in units/s^2, friction
# = exponential decay, size = quad size in world units, real time, and
# `{base spread}` random ranges are SYMMETRIC +/-. The one deviation found is
# ENGINE_EMISSION_MUL below. See README.md for how each was pinned down.
#
# Why in Blender: a mesh-less preview structurally cannot show
#   * attachment to a real locator,
#   * force directions in the parent BONE frame,
#   * local_space=yes/no (indistinguishable while the emitter is static),
# and size can only be judged as a RATIO against known geometry, which cancels
# the entity `scale` the game applies to both mesh and particles alike.
#
# Rendering goes through the `gpu` module rather than EEVEE materials, because
# that is the only way to get TRUE additive blending - the most common blend
# mode in these effects. Viewport-only by design: a measuring instrument, not a
# render path.
#
# Copyright (C) 2026 pdx-blender-tools contributors.
# SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================

bl_info = {
    "name": "PDX Particle Bench",
    "author": "pdx-blender-tools contributors",
    "version": (0, 4, 2),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > PDX Blender Tools",
    "description": "Preview Clausewitz/HoI4 .asset particle effects on a real locator",
    "category": "3D View",
}

import math
import os
import random
import re

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

# --- calibrated engine constant ---------------------------------------------
# The engine spawns ~3x more particles than a literal "emission = particles/sec"
# reading. Measured on a rapid-fire hull weapon effect, using its spark and smoke
# subsystems. Provisional: one effect's worth of evidence - re-measure on others.
ENGINE_EMISSION_MUL = 3.0

FIXED_DT = 1.0 / 120.0
MAX_STEPS_PER_UPDATE = 4000


# =============================================================================
# PDX .asset parser
# =============================================================================

_TOKEN_RE = re.compile(r'"([^"]*)"|([{}=])|([^\s{}=]+)')


def tokenize(text):
    text = re.sub(r"#[^\n]*", "", text)
    out = []
    for m in _TOKEN_RE.finditer(text):
        if m.group(1) is not None:
            out.append(("str", m.group(1)))
        elif m.group(2):
            out.append(("sym", m.group(2)))
        else:
            out.append(("atom", m.group(3)))
    return out


def _convert(tok):
    kind, val = tok
    if kind == "str":
        return val
    try:
        return float(val) if ("." in val or "e" in val.lower()) else int(val)
    except ValueError:
        return val


class _Parser:
    """Recursive-descent parser for the Paradox bracket format.

    Repeated keys (subsystem/animation/force) collect into a list, tracked
    separately from values that are genuinely lists (e.g. `velocity={ 20 15 }`).
    """

    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def parse(self):
        return self._body()

    def _body(self):
        obj = {}
        multi = set()
        while self.i < len(self.toks) and self.toks[self.i] != ("sym", "}"):
            key = self.toks[self.i]
            self.i += 1
            if key[0] == "sym":
                continue
            if self.i < len(self.toks) and self.toks[self.i] == ("sym", "="):
                self.i += 1
                val = self._value()
                name = key[1]
                if name in obj:
                    if name not in multi:
                        obj[name] = [obj[name]]
                        multi.add(name)
                    obj[name].append(val)
                else:
                    obj[name] = val
        if self.i < len(self.toks) and self.toks[self.i] == ("sym", "}"):
            self.i += 1
        obj["__multi__"] = multi
        return obj

    def _value(self):
        tok = self.toks[self.i]
        if tok == ("sym", "{"):
            self.i += 1
            # object (has a top-level '=') or plain list of atoms?
            depth = 0
            j = self.i
            is_obj = False
            while j < len(self.toks):
                t = self.toks[j]
                if t == ("sym", "{"):
                    depth += 1
                elif t == ("sym", "}"):
                    if depth == 0:
                        break
                    depth -= 1
                elif t == ("sym", "=") and depth == 0:
                    is_obj = True
                    break
                j += 1
            if is_obj:
                return self._body()
            arr = []
            while self.i < len(self.toks) and self.toks[self.i] != ("sym", "}"):
                arr.append(_convert(self.toks[self.i]))
                self.i += 1
            if self.i < len(self.toks):
                self.i += 1
            return arr
        self.i += 1
        return _convert(tok)


def many(obj, key):
    if not obj or key not in obj:
        return []
    val = obj[key]
    if key in obj.get("__multi__", set()):
        return val
    return [val]


# =============================================================================
# Field helpers
# =============================================================================


def as_range(v):
    """`{ base spread }` -> (base, spread). Scalars become (v, 0)."""
    if isinstance(v, list):
        b = v[0] if len(v) > 0 else 0
        s = v[1] if len(v) > 1 else 0
        return (float(b) if isinstance(b, (int, float)) else 0.0,
                float(s) if isinstance(s, (int, float)) else 0.0)
    if isinstance(v, (int, float)):
        return (float(v), 0.0)
    return (0.0, 0.0)


def size_of(v):
    """`size={ 0.75,muzzle_expand 0 }` -> (base, spread, curve_ref)."""
    if isinstance(v, list):
        first = v[0] if v else 0
        spread_v = float(v[1]) if len(v) > 1 and isinstance(v[1], (int, float)) else 0.0
        if isinstance(first, str) and "," in first:
            head, ref = first.split(",", 1)
            return (_float(head), spread_v, ref)
        return (_float(first), spread_v, None)
    if isinstance(v, str) and "," in v:
        head, ref = v.split(",", 1)
        return (_float(head), 0.0, ref)
    return (_float(v), 0.0, None)


def alpha_of(v):
    """`alpha=150,muzzle_fade` -> (base, curve_ref)."""
    if isinstance(v, str) and "," in v:
        head, ref = v.split(",", 1)
        return (_float(head), ref)
    return (_float(v), None)


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def sample_curve(pts, u):
    """Piecewise-linear `curve={ t0 v0 t1 v1 ... }` sampled at u in [0,1]."""
    if not pts or len(pts) < 4:
        return 1.0
    u = min(max(u, 0.0), 1.0)
    n = len(pts) // 2
    if u <= pts[0]:
        return float(pts[1])
    if u >= pts[(n - 1) * 2]:
        return float(pts[(n - 1) * 2 + 1])
    for i in range(n - 1):
        t0, v0 = float(pts[i * 2]), float(pts[i * 2 + 1])
        t1, v1 = float(pts[i * 2 + 2]), float(pts[i * 2 + 3])
        if t0 <= u <= t1:
            span = (t1 - t0) or 1.0
            return v0 + (v1 - v0) * ((u - t0) / span)
    return float(pts[(n - 1) * 2 + 1])


# The web Bench treated the .asset's (x, y, z) as (right, up, forward) - that
# convention is what got validated, so positions and force directions are mapped
# through the same basis here.
AXIS_PRESETS = {
    "Y_FWD_Z_UP": (Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))),
    "NEG_Y_FWD_Z_UP": (Vector((0, -1, 0)), Vector((0, 0, 1)), Vector((-1, 0, 0))),
    "X_FWD_Z_UP": (Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0))),
    "NEG_X_FWD_Z_UP": (Vector((-1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0))),
    "Z_FWD_Y_UP": (Vector((0, 0, 1)), Vector((0, 1, 0)), Vector((1, 0, 0))),
}


def basis(axis_key):
    return AXIS_PRESETS.get(axis_key, AXIS_PRESETS["Y_FWD_Z_UP"])


# =============================================================================
# Effect model
# =============================================================================


class Subsystem:
    def __init__(self, idx, raw):
        tex = raw.get("texture", {}) or {}
        col = raw.get("color", {}) or {}
        pos = raw.get("position", {}) or {}

        self.idx = idx
        self.name = raw.get("name", "sub%d" % idx)
        self.max_amount = int(_float(raw.get("max_amount", 0)))
        self.emission = _float(raw.get("emission", 0))
        self.start = _float(raw.get("start", 0))
        self.duration = _float(raw.get("duration", 0)) if "duration" in raw else 0.0

        self.life_b, self.life_s = as_range(raw.get("life"))
        self.eyaw_b, self.eyaw_s = as_range(raw.get("emitter_yaw"))
        self.epitch_b, self.epitch_s = as_range(raw.get("emitter_pitch"))
        self.vyaw_b, self.vyaw_s = as_range(raw.get("velocity_yaw"))
        self.vpitch_b, self.vpitch_s = as_range(raw.get("velocity_pitch"))
        self.vel_b, self.vel_s = as_range(raw.get("velocity"))
        self.rot_b, self.rot_s = as_range(raw.get("rotation"))
        self.rotspd_b, self.rotspd_s = as_range(raw.get("rotation_speed"))
        self.size_b, self.size_s, self.size_ref = size_of(raw.get("size"))
        self.alpha_b, self.alpha_ref = alpha_of(col.get("alpha"))
        # billboard=no quads are oriented by these instead of facing the camera
        self.pyaw = as_range(raw.get("particle_yaw"))[0]
        self.ppitch = as_range(raw.get("particle_pitch"))[0]

        self.offset = (_float(pos.get("x")), _float(pos.get("y")), _float(pos.get("z")))
        self.color = (
            min(max(_float(col.get("x", 255)), 0), 255) / 255.0,
            min(max(_float(col.get("y", 255)), 0), 255) / 255.0,
            min(max(_float(col.get("z", 255)), 0), 255) / 255.0,
        )
        self.additive = "additive" in str(tex.get("shader", "")).lower()
        self.tex_file = str(tex.get("file", "") or "")
        self.billboard = raw.get("billboard") != "no"
        self.local_space = raw.get("local_space") != "no"
        self.emitter_type = raw.get("emitter_type", "point")
        force_val = raw.get("force")
        self.forces = (
            [f.strip() for f in force_val.split(",") if f.strip()]
            if isinstance(force_val, str)
            else []
        )
        self.enabled = True
        self.live = 0


class Force:
    def __init__(self, raw):
        self.name = raw.get("name", "")
        self.type = raw.get("type", "planar")
        self.amount = _float(raw.get("amount"))
        d = raw.get("direction", [0, 1, 0])
        if not isinstance(d, list) or len(d) < 3:
            d = [0, 1, 0]
        self.dir_raw = (_float(d[0]), _float(d[1]), _float(d[2]))
        self.local = raw.get("local_force") != "no"


class Effect:
    def __init__(self, text):
        root = _Parser(tokenize(text)).parse()
        particle = root.get("particle")
        if not particle:
            raise ValueError("no particle={...} block found")
        self.name = particle.get("name", "(unnamed)")
        self.subs = [Subsystem(i, s) for i, s in enumerate(many(particle, "subsystem"))]
        if not self.subs:
            raise ValueError("particle block has no subsystem={...}")
        self.anims = {}
        for a in many(particle, "animation"):
            curve = a.get("curve")
            self.anims[a.get("name", "")] = {
                "pts": curve if isinstance(curve, list) else [],
                "time": "spawn" if a.get("time") == "spawn" else "life",
            }
        self.forces = {}
        for f in many(particle, "force"):
            force = Force(f)
            self.forces[force.name] = force

    def window(self):
        w = 0.0
        for s in self.subs:
            d = 2.0 if s.duration < 0 else s.duration
            w = max(w, s.start + d + s.life_b + abs(s.life_s))
        return w or 1.0

    def lints(self):
        out = []
        for s in self.subs:
            if s.duration == 0:
                out.append("%s: duration=0 spawns ZERO particles" % s.name)
        spaces = {s.local_space for s in self.subs}
        alpha_subs = [s.name for s in self.subs if not s.additive]
        if len(spaces) > 1 and alpha_subs:
            out.append(
                "mixed local_space + alpha-blend (%s): HoI4 may silently drop them"
                % ", ".join(alpha_subs)
            )
        return out


# =============================================================================
# Simulation (1:1 port of the validated web Bench model)
# =============================================================================


class Particle:
    __slots__ = ("si", "pos", "vel", "age", "life", "rot", "rotspd",
                 "size0", "spawn_frac", "local", "mat")

    def __init__(self):
        self.si = 0
        self.pos = Vector((0.0, 0.0, 0.0))
        self.vel = Vector((0.0, 0.0, 0.0))
        self.age = 0.0
        self.life = 1.0
        self.rot = 0.0
        self.rotspd = 0.0
        self.size0 = 1.0
        self.spawn_frac = 0.0
        self.local = True
        self.mat = None  # emitter matrix captured at spawn (world-space particles)


class Instance:
    """One fired effect (one HoI4 event)."""

    def __init__(self, effect, start):
        self.effect = effect
        self.start = start
        self.parts = []
        self.budget = [0.0] * len(effect.subs)
        self.count = [0] * len(effect.subs)
        self.done = False

    def step(self, dt, t_global, cfg, rng, emitter_mat):
        t_local = t_global - self.start
        if t_local < 0:
            return
        eff = self.effect
        fwd, up, right = basis(cfg["axis"])
        rot3 = emitter_mat.to_3x3() if emitter_mat else None

        # --- emission ---
        for si, s in enumerate(eff.subs):
            if not s.enabled or s.duration == 0:
                continue
            in_window = t_local >= s.start and (
                s.duration < 0 or t_local < s.start + s.duration
            )
            if not in_window:
                continue
            self.budget[si] += s.emission * ENGINE_EMISSION_MUL * cfg["emission"] * dt
            while self.budget[si] >= 1.0:
                self.budget[si] -= 1.0
                if self.count[si] >= s.max_amount:
                    break
                self._spawn(si, s, t_local, cfg, rng, emitter_mat, fwd, up, right)

        # --- integrate ---
        for p in self.parts:
            if p.age >= p.life:
                continue
            p.age += dt
            if p.age >= p.life:
                self.count[p.si] -= 1
                continue
            s = eff.subs[p.si]
            for fname in s.forces:
                f = eff.forces.get(fname)
                if not f:
                    continue
                if f.type == "friction":
                    k = math.exp(-f.amount * cfg["friction"] * dt)
                    p.vel *= k
                else:
                    # Force direction uses a DIFFERENT mapping than position:
                    # .asset (x, y, z) -> the locator's local (X, Z, Y), i.e. the same
                    # Y<->Z swap as io_pdx_mesh's SPACE_MATRIX. Confirmed against two
                    # independently recorded in-game findings on one vehicle: at its
                    # turret-mounted flamer the locator's local X points world-down and
                    # DOWN is {1,0,0}, while at a hull-mounted one local Y points
                    # world-down and DOWN is {0,0,1}. Position keeps (forward, up,
                    # right) - that one was confirmed visually by a muzzle flash sitting
                    # off to the side. The asymmetry is suspicious and deserves a probe.
                    d = right * f.dir_raw[0] + up * f.dir_raw[1] + fwd * f.dir_raw[2]
                    # ALWAYS the locator's local frame: local_force=no does NOT mean
                    # world space - the bone's rest rotation still applies either way.
                    # Only convert when the PARTICLE itself lives in world space.
                    if rot3 is not None and not p.local:
                        d = rot3 @ d
                    p.vel += d * (f.amount * cfg["force"] * dt)
            p.pos += p.vel * (cfg["world"] * dt)
            p.rot += p.rotspd * dt

        self.parts = [p for p in self.parts if p.age < p.life]
        if t_local > eff.window() and sum(self.count) <= 0:
            self.done = True

    def _spawn(self, si, s, t_local, cfg, rng, emitter_mat, fwd, up, right):
        mode = cfg["spread"]
        yaw = math.radians(
            s.eyaw_b + _spread(s.eyaw_s, mode, rng) + _spread(s.vyaw_s, mode, rng)
        )
        # io_pdx_mesh's SPACE_MATRIX is a Y/Z swap with determinant -1 - a MIRROR, not
        # a rotation - so yaw handedness arrives inverted and must be negated back.
        # Measured on a mirrored pair of engine-exhaust effects: the left-hand file
        # (emitter_yaw=+90) blew to world right and the right-hand one (-90) to world
        # left, both mirrored the same way. Effects whose yaw base is 0 - muzzle
        # flashes, flame streams - are unaffected either way.
        if not cfg["flip_yaw"]:
            yaw = -yaw
        pitch = math.radians(
            s.epitch_b + _spread(s.epitch_s, mode, rng) + _spread(s.vpitch_s, mode, rng)
        )
        speed = s.vel_b + _spread(s.vel_s, mode, rng)

        # emitter_yaw/pitch are measured from the direction the weapon FIRES, which is
        # -fwd: on body-mounted locators the local forward runs tank-BACKWARD, so a
        # yaw=0 emitter built on +fwd sprays out of the vehicle's rear. This is the
        # same -fwd the billboard=no plume uses, so flame and quad now agree.
        muzzle = -fwd
        direction = (
            muzzle * (math.cos(pitch) * math.cos(yaw))
            + right * (math.cos(pitch) * math.sin(yaw))
            + up * math.sin(pitch)
        )

        # .asset position axes: x = forward, y = up, z = right.
        # y=up was validated in the web Bench (upforce lifting smoke); x-vs-z was
        # NOT distinguishable there - with no mesh, a sideways offset looks like a
        # forward one. Pinned down in Blender: the muzzle flash sat off to the side.
        pos = fwd * s.offset[0] + up * s.offset[1] + right * s.offset[2]
        if s.emitter_type == "sphere":
            pos = pos + Vector((rng.uniform(-0.06, 0.06),
                                rng.uniform(-0.06, 0.06),
                                rng.uniform(-0.06, 0.06)))

        p = Particle()
        p.si = si
        p.life = max(0.01, s.life_b + _spread(s.life_s, mode, rng))
        p.size0 = max(0.0, s.size_b + _spread(s.size_s, mode, rng))
        p.rot = s.rot_b + _spread(s.rot_s, mode, rng)
        p.rotspd = s.rotspd_b + _spread(s.rotspd_s, mode, rng)
        p.spawn_frac = (
            min(max((t_local - s.start) / s.duration, 0.0), 1.0) if s.duration > 0 else 0.0
        )
        p.local = s.local_space

        if s.local_space or emitter_mat is None:
            # Simulated in the emitter's local frame; it rides the locator.
            p.pos = pos
            p.vel = direction * speed
            p.mat = None
        else:
            # local_space=no: baked into world at spawn, does NOT follow the locator.
            p.pos = emitter_mat @ pos
            p.vel = emitter_mat.to_3x3() @ (direction * speed)
            p.mat = None

        self.parts.append(p)
        self.count[si] += 1


def _spread(amp, mode, rng):
    if amp == 0:
        return 0.0
    return rng.uniform(-amp, amp) if mode == "SYM" else rng.uniform(0.0, amp)


class Sim:
    def __init__(self):
        self.effect = None
        self.instances = []
        self.t = 0.0
        self.seed = 20260718
        self.max_t = 2.0
        self.rng = random.Random(self.seed)
        self._next_fire = 0.0
        self._fired_single = False

    def configure(self, cfg):
        if not self.effect:
            return
        self.max_t = self.effect.window() + (1.3 if cfg["refire"] > 0 else 0.35)

    def reset(self, cfg):
        self.rng = random.Random(self.seed)
        self.instances = []
        self.t = 0.0
        self._next_fire = 0.0
        self._fired_single = False
        self.configure(cfg)
        for s in (self.effect.subs if self.effect else []):
            s.live = 0
        self._fires(cfg)

    def _fires(self, cfg):
        if not self.effect:
            return
        if cfg["refire"] <= 0:
            if not self._fired_single:
                self.instances.append(Instance(self.effect, 0.0))
                self._fired_single = True
            return
        interval = cfg["refire"] / 24.0
        while self._next_fire <= self.t + 1e-6 and self._next_fire < self.max_t:
            self.instances.append(Instance(self.effect, self._next_fire))
            self._next_fire += interval

    def advance_to(self, target, cfg, emitter_mat):
        """Step forward to `target` seconds. Caller resets first when seeking back."""
        if not self.effect:
            return
        steps = 0
        while self.t < target - 1e-9 and steps < MAX_STEPS_PER_UPDATE:
            self.t += FIXED_DT
            self._fires(cfg)
            for inst in self.instances:
                inst.step(FIXED_DT, self.t, cfg, self.rng, emitter_mat)
            self.instances = [i for i in self.instances if not i.done]
            steps += 1
        for s in self.effect.subs:
            s.live = 0
        for inst in self.instances:
            for si, c in enumerate(inst.count):
                self.effect.subs[si].live += max(0, c)


SIM = Sim()


# =============================================================================
# Texture resolution - .asset paths are game-relative ("gfx/particles/glow.dds"),
# so they are looked up in the mod root first, then vanilla, exactly like HoI4
# resolves them. Most particle textures live in VANILLA, not the mod.
# Blender reads .dds natively, so no conversion step is needed.
# =============================================================================

_tex_cache = {}
_missing_reported = set()


def get_prefs():
    try:
        return bpy.context.preferences.addons[__name__].preferences
    except (KeyError, AttributeError):
        return None


def resolve_texture_path(rel):
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    prefs = get_prefs()
    if not prefs:
        return None
    for root in (prefs.mod_root, prefs.vanilla_root):
        if not root:
            continue
        candidate = os.path.join(bpy.path.abspath(root), *rel.split("/"))
        if os.path.isfile(candidate):
            return candidate
    return None


def get_texture(rel):
    """GPUTexture for a .asset texture reference, or None to fall back to procedural."""
    if rel in _tex_cache:
        return _tex_cache[rel]
    path = resolve_texture_path(rel)
    tex = None
    if path:
        try:
            img = bpy.data.images.load(path, check_existing=True)
            # Raw texel values (no sRGB transform), matching how the calibrated
            # web Bench treated these textures.
            img.colorspace_settings.name = "Non-Color"
            tex = gpu.texture.from_image(img)
        except Exception as exc:  # noqa: BLE001
            if rel not in _missing_reported:
                print("[pdx_bench] failed to load '%s': %s" % (rel, exc))
    if tex is None and rel not in _missing_reported:
        _missing_reported.add(rel)
        print("[pdx_bench] texture unresolved (check mod/vanilla roots): %s" % rel)
    _tex_cache[rel] = tex
    return tex


def clear_texture_cache():
    _tex_cache.clear()
    _missing_reported.clear()


# =============================================================================
# GPU drawing - true additive via the gpu module (EEVEE materials can't do it)
# =============================================================================

VERT_SRC = """
uniform mat4 ModelViewProjectionMatrix;
in vec3 pos;
in vec2 uv;
in vec4 col;
out vec2 v_uv;
out vec4 v_col;
void main()
{
    v_uv = uv;
    v_col = col;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

FRAG_SRC = """
in vec2 v_uv;
in vec4 v_col;
out vec4 fragColor;
void main()
{
    /* Procedural radial falloff stands in for the .dds. Size of the quad is what
       matters for measuring against the mesh; real textures come later. */
    vec2 d = v_uv * 2.0 - 1.0;
    float r = length(d);
    float a = clamp(1.0 - r, 0.0, 1.0);
    a *= a;
    fragColor = vec4(v_col.rgb, v_col.a * a);
}
"""

FRAG_TEX_SRC = """
uniform sampler2D image;
in vec2 v_uv;
in vec4 v_col;
out vec4 fragColor;
void main()
{
    vec4 t = texture(image, v_uv);
    /* The engine MULTIPLIES the subsystem's `color=` by the texture rather than
       replacing it, so a tinted texture shifts the result: a yellow beam texture
       under a cyan color= comes out green, because yellow carries no blue.
       Shape comes from the alpha channel, which is where every texture checked so
       far keeps it. */
    fragColor = vec4(v_col.rgb * t.rgb, v_col.a * t.a);
}
"""

_shader_proc = None
_shader_tex = None
_draw_handle = None


def get_shader(textured):
    global _shader_proc, _shader_tex
    if textured:
        if _shader_tex is None:
            _shader_tex = gpu.types.GPUShader(VERT_SRC, FRAG_TEX_SRC)
        return _shader_tex
    if _shader_proc is None:
        _shader_proc = gpu.types.GPUShader(VERT_SRC, FRAG_SRC)
    return _shader_proc


def _visual(p, s, effect):
    """size and alpha after the animation curves, exactly as the web Bench."""
    u = min(max(p.age / p.life, 0.0), 1.0)
    size = p.size0
    if s.size_ref:
        anim = effect.anims.get(s.size_ref)
        if anim:
            size *= sample_curve(
                anim["pts"], p.spawn_frac if anim["time"] == "spawn" else u
            )
    alpha = s.alpha_b / 255.0
    if s.alpha_ref:
        anim = effect.anims.get(s.alpha_ref)
        if anim:
            alpha *= sample_curve(anim["pts"], u)
    return size, min(max(alpha, 0.0), 1.0)


def oriented_quad_axes(s, axis_key, flip_yaw, flip_plume, rot3):
    """In-plane axes for a `billboard=no` quad - locked to the emitter, not the camera.

    The quad's NORMAL is built from particle_yaw/particle_pitch with the same
    formula as a velocity direction. That interpretation reproduces all of the
    in-game behaviour recorded in the .asset comments:
      pitch=90 -> normal is up   -> quad lies flat  (flash_secondary_h)
      pitch=0  -> normal is side -> quad stands up  (flash_secondary_v)
      yaw=pitch=0 -> normal is forward -> muzzle_ring faces along the shot
    The in-plane axis at `rotation=0` is the emitter's SIDE axis projected into the
    quad plane, falling back to the muzzle direction when side lies along the normal.
    That single rule reproduces both measured cases:
      normal = side (pitch=0)  -> side is degenerate -> U falls back to along the shot
      normal = up   (pitch=90) -> U is the side axis -> across the shot at rotation=0

    Measured in game with a four-value rotation sweep in the pitch=90 plane: only
    `rotation=90` ran along the shot, `rotation=0` ran across. An earlier model used
    the muzzle direction as the default in-plane axis, which got the pitch=0 plane
    right and the pitch=90 plane exactly 90 degrees wrong.
    """
    fwd, up, right = basis(axis_key)
    yaw = math.radians(s.pyaw if flip_yaw else -s.pyaw)
    pitch = math.radians(s.ppitch)
    normal = (
        fwd * (math.cos(pitch) * math.cos(yaw))
        + right * (math.cos(pitch) * math.sin(yaw))
        + up * math.sin(pitch)
    )
    if normal.length < 1e-6:
        normal = fwd.copy()
    normal.normalize()

    # Default in-plane axis is the SIDE axis projected into the plane.
    u = right - normal * right.dot(normal)
    if u.length < 1e-4:
        # Side lies along the normal, so it gives no in-plane direction. Fall back to
        # the muzzle direction: on body-mounted locators the local forward runs
        # tank-BACKWARD, so out of the barrel is -fwd.
        long_axis = fwd if flip_plume else -fwd
        u = long_axis - normal * long_axis.dot(normal)
    u.normalize()
    v = normal.cross(u)

    if rot3 is not None:
        u = rot3 @ u
        v = rot3 @ v
    return u, v


def draw_callback():
    effect = SIM.effect
    if effect is None:
        return
    ctx = bpy.context
    scene = ctx.scene
    props = scene.pdx_pb
    if not props.enabled:
        return

    region_3d = ctx.region_data
    if region_3d is None:
        return
    view_mat = region_3d.view_matrix
    cam_right = Vector((view_mat[0][0], view_mat[0][1], view_mat[0][2]))
    cam_up = Vector((view_mat[1][0], view_mat[1][1], view_mat[1][2]))

    emitter_mat = props.target.matrix_world if props.target else None
    emitter_rot = emitter_mat.to_3x3() if emitter_mat else None
    size_gain = props.size_gain

    # Bucket by subsystem so each can use its own blend mode.
    buckets = {}
    for inst in SIM.instances:
        for p in inst.parts:
            s = effect.subs[p.si]
            if not s.enabled:
                continue
            size, alpha = _visual(p, s, effect)
            if alpha <= 0.003 or size <= 0.0:
                continue
            world_pos = (emitter_mat @ p.pos) if (p.local and emitter_mat) else p.pos
            buckets.setdefault(p.si, []).append((world_pos, size, alpha, p.rot))

    if not buckets:
        return

    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)

    for si, items in buckets.items():
        s = effect.subs[si]
        # Painter's order for alpha-blended smoke; additive is order-independent.
        if not s.additive:
            eye = Vector(region_3d.view_matrix.inverted().translation)
            items.sort(key=lambda it: -(it[0] - eye).length_squared)

        # billboard=yes faces the camera; billboard=no is locked to the emitter.
        if s.billboard:
            ax_u, ax_v = cam_right, cam_up
        else:
            ax_u, ax_v = oriented_quad_axes(
                s, props.axis_preset, props.flip_yaw, props.flip_plume, emitter_rot
            )

        coords, uvs, cols, indices = [], [], [], []
        for n, (wp, size, alpha, rot) in enumerate(items):
            half = size * size_gain * 0.5
            ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            rx = (ax_u * ca + ax_v * sa) * half
            ry = (ax_v * ca - ax_u * sa) * half
            coords.extend([wp - rx - ry, wp + rx - ry, wp + rx + ry, wp - rx + ry])
            uvs.extend([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
            rgba = (s.color[0], s.color[1], s.color[2], alpha)
            cols.extend([rgba] * 4)
            b = n * 4
            indices.extend([(b, b + 1, b + 2), (b, b + 2, b + 3)])

        tex = get_texture(s.tex_file) if s.tex_file else None
        shader = get_shader(tex is not None)
        gpu.state.blend_set("ADDITIVE" if s.additive else "ALPHA")
        batch = batch_for_shader(
            shader, "TRIS", {"pos": coords, "uv": uvs, "col": cols}, indices=indices
        )
        shader.bind()
        if tex is not None:
            shader.uniform_sampler("image", tex)
        batch.draw(shader)

    gpu.state.blend_set("NONE")
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")


# =============================================================================
# Frame driving
# =============================================================================

_last_frame = [None]


def _cfg(props):
    return {
        "world": props.world_scale,
        "force": props.force_scale,
        "friction": props.friction_scale,
        "emission": props.emission_scale,
        "spread": props.spread_mode,
        "refire": props.refire_frames,
        "axis": props.axis_preset,
        "flip_yaw": props.flip_yaw,
    }


def update_sim(scene, force_reset=False):
    if SIM.effect is None:
        return
    props = scene.pdx_pb
    if not props.enabled:
        return
    cfg = _cfg(props)
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    target = (scene.frame_current - scene.frame_start) / max(fps, 1e-6)

    emitter_mat = props.target.matrix_world.copy() if props.target else None

    # Deterministic: stepping backwards or jumping re-sims from zero.
    if force_reset or _last_frame[0] is None or target < SIM.t - 1e-9:
        SIM.reset(cfg)
    _last_frame[0] = scene.frame_current
    SIM.advance_to(target, cfg, emitter_mat)


@persistent
def frame_change_handler(scene, _depsgraph=None):
    update_sim(scene)
    _tag_redraw()


def _tag_redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# =============================================================================
# Blender properties / operators / UI
# =============================================================================


def _on_knob_change(self, context):
    update_sim(context.scene, force_reset=True)
    _tag_redraw()


def _on_root_change(self, context):
    clear_texture_cache()


class PPB_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    mod_root: bpy.props.StringProperty(
        name="Mod root",
        description="Folder containing the mod's gfx/ - checked FIRST, like HoI4's override order",
        subtype="DIR_PATH",
        update=_on_root_change,
    )
    vanilla_root: bpy.props.StringProperty(
        name="Vanilla root",
        description="Hearts of Iron IV install folder. Most particle textures live here, not in the mod",
        subtype="DIR_PATH",
        update=_on_root_change,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "mod_root")
        col.prop(self, "vanilla_root")
        col.label(
            text="Texture paths in .asset resolve against the mod first, then vanilla.",
            icon="INFO",
        )


class PPB_Props(bpy.types.PropertyGroup):
    asset_path: bpy.props.StringProperty(
        name="Asset", description="HoI4 particle .asset file", subtype="FILE_PATH"
    )
    target: bpy.props.PointerProperty(
        name="Locator",
        description="Empty/object the effect is attached to - a locator from the imported mesh",
        type=bpy.types.Object,
    )
    enabled: bpy.props.BoolProperty(name="Show", default=True)

    axis_preset: bpy.props.EnumProperty(
        name="Axes",
        description="Which local axis the emitter fires along (flip until the jet points right)",
        items=[
            ("Y_FWD_Z_UP", "+Y fwd, +Z up", ""),
            ("NEG_Y_FWD_Z_UP", "-Y fwd, +Z up", ""),
            ("X_FWD_Z_UP", "+X fwd, +Z up", ""),
            ("NEG_X_FWD_Z_UP", "-X fwd, +Z up", ""),
            ("Z_FWD_Y_UP", "+Z fwd, +Y up", ""),
        ],
        default="Y_FWD_Z_UP",
        update=_on_knob_change,
    )
    flip_yaw: bpy.props.BoolProperty(
        name="Flip yaw",
        description="Yaw is negated by default to undo the mirror in io_pdx_mesh's "
                    "SPACE_MATRIX (determinant -1). Tick to restore the raw sign if a "
                    "mount disagrees",
        default=False,
        update=_on_knob_change,
    )
    flip_plume: bpy.props.BoolProperty(
        name="Flip plume",
        description="Long axis of a billboard=no quad points out of the barrel (-forward). "
                    "Tick if an elongated flame ends up pointing the wrong way",
        default=False,
        update=_on_knob_change,
    )
    spread_mode: bpy.props.EnumProperty(
        name="Spread",
        items=[("SYM", "symmetric +/-", ""), ("ONE", "one-sided 0..+", "")],
        default="SYM",
        update=_on_knob_change,
    )
    refire_frames: bpy.props.IntProperty(
        name="Refire", description="Re-fire every N frames (0 = single shot)",
        default=0, min=0, max=120, update=_on_knob_change,
    )

    world_scale: bpy.props.FloatProperty(name="World scale", default=1.0, min=0.0, soft_max=3.0, update=_on_knob_change)
    force_scale: bpy.props.FloatProperty(name="Force", default=1.0, min=0.0, soft_max=3.0, update=_on_knob_change)
    friction_scale: bpy.props.FloatProperty(name="Friction", default=1.0, min=0.0, soft_max=3.0, update=_on_knob_change)
    emission_scale: bpy.props.FloatProperty(name="Emission", default=1.0, min=0.0, soft_max=6.0, update=_on_knob_change)
    size_gain: bpy.props.FloatProperty(name="Size gain", default=1.0, min=0.01, soft_max=4.0)


class PPB_OT_load(bpy.types.Operator):
    bl_idname = "pdx_pb.load"
    bl_label = "Load .asset"
    bl_description = "Parse the .asset and restart the simulation"

    def execute(self, context):
        props = context.scene.pdx_pb
        path = bpy.path.abspath(props.asset_path)
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a valid .asset file")
            return {"CANCELLED"}
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                effect = Effect(fh.read())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.report({"ERROR"}, "Parse failed: %s" % exc)
            return {"CANCELLED"}
        SIM.effect = effect
        _last_frame[0] = None
        update_sim(context.scene, force_reset=True)
        _tag_redraw()
        for msg in effect.lints():
            self.report({"WARNING"}, msg)
        self.report({"INFO"}, "%s: %d subsystems" % (effect.name, len(effect.subs)))
        return {"FINISHED"}


class PPB_OT_reset(bpy.types.Operator):
    bl_idname = "pdx_pb.reset"
    bl_label = "Restart"
    bl_description = "Restart the simulation from frame start"

    def execute(self, context):
        _last_frame[0] = None
        update_sim(context.scene, force_reset=True)
        _tag_redraw()
        return {"FINISHED"}


class PPB_PT_panel(bpy.types.Panel):
    bl_label = "Particle Bench"
    bl_idname = "PPB_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PDX Blender Tools"

    def draw(self, context):
        layout = self.layout
        props = context.scene.pdx_pb

        prefs = get_prefs()
        if not prefs or not (prefs.mod_root or prefs.vanilla_root):
            layout.label(text="Set mod/vanilla roots in Add-on Preferences", icon="ERROR")

        # Blender 4.x defaults to the AgX view transform, which deliberately
        # desaturates saturated colour as it brightens. Harmless for artwork, but
        # this add-on is a measuring instrument: under AgX a red or magenta effect
        # reads noticeably duller here than the same effect does in game.
        if context.scene.view_settings.view_transform != "Standard":
            layout.label(text="Colour is tone-mapped by view transform", icon="INFO")
            layout.label(text="Render > Color Management > Standard to compare")

        col = layout.column(align=True)
        col.prop(props, "asset_path", text="")
        col.prop(props, "target", text="Locator")
        row = layout.row(align=True)
        row.operator("pdx_pb.load", icon="FILE_REFRESH")
        row.operator("pdx_pb.reset", icon="LOOP_BACK")
        layout.prop(props, "enabled")

        effect = SIM.effect
        if effect is None:
            layout.label(text="No effect loaded", icon="INFO")
            return

        box = layout.box()
        box.label(text=effect.name, icon="PARTICLES")
        for s in effect.subs:
            row = box.row(align=True)
            row.label(
                text="%s  %d/%d  %s"
                % (s.name, s.live, s.max_amount, "ADD" if s.additive else "ALPHA")
            )

        for msg in effect.lints():
            layout.label(text=msg, icon="ERROR")

        box = layout.box()
        box.label(text="Engine constants (calibrated =1)")
        box.prop(props, "world_scale")
        box.prop(props, "force_scale")
        box.prop(props, "friction_scale")
        box.prop(props, "emission_scale")
        box.prop(props, "size_gain")
        box.prop(props, "spread_mode")
        box.prop(props, "axis_preset")
        box.prop(props, "flip_yaw")
        box.prop(props, "flip_plume")
        box.prop(props, "refire_frames")

        # Which build is actually running. This add-on tends to exist in several
        # copies at once (repo, Blender's addons dir, a mod's tools folder), and
        # editing one does not change what Blender loaded.
        layout.separator()
        row = layout.row()
        row.alignment = "RIGHT"
        row.label(text="v{}.{}.{}".format(*bl_info["version"]))


CLASSES = (PPB_Prefs, PPB_Props, PPB_OT_load, PPB_OT_reset, PPB_PT_panel)


def register():
    global _draw_handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pdx_pb = bpy.props.PointerProperty(type=PPB_Props)
    if frame_change_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(frame_change_handler)
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_callback, (), "WINDOW", "POST_VIEW"
        )


def unregister():
    global _draw_handle, _shader
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    if frame_change_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(frame_change_handler)
    del bpy.types.Scene.pdx_pb
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _shader = None


if __name__ == "__main__":
    register()
