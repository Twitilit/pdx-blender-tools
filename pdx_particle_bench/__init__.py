# PDX Particle Bench - preview a HoI4 .asset particle effect on a real locator
# inside Blender, to judge it against the mesh it fires from. Behaviour is
# measured against the game; the change history is in CHANGELOG.md.
# Drawing goes through the gpu module (the only way to get true additive blend).
# Viewport-only: a measuring instrument, not a render path.
#
# Copyright (C) 2026 pdx-blender-tools contributors.
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "PDX Particle Bench",
    "author": "pdx-blender-tools contributors",
    "version": (0, 5, 2),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > PDX Blender Tools",
    "description": "Preview Clausewitz/HoI4 .asset particle effects on a real locator",
    "category": "3D View",
}

import json
import math
import os
import random
import re
import time

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

# `emission` is a literal particles/second - the engine applies no multiplier.
ENGINE_EMISSION_MUL = 1.0

# A HoI4 vortex is mostly a RADIAL push from its axis with a slight tangential
# lean. This is that lean as a fraction of the radial push (0.09 = ~5 degrees),
# read off the swarm; a single-particle trace is too insensitive to show it.
VORTEX_SWIRL = 0.09

# `spin`/orbit force: radians/sec of orbit per unit of `amount` (amount IS rad/s).
SPIN_RATE = 1.0

# Turbulence: each particle holds one push direction and swaps it for a fresh
# random one at random intervals. TURB_RATE = direction changes per second,
# matched against the game on the DISTRIBUTION of outcomes, not a single trace:
# some particles run off almost straight, others mill about without clearing ring 10.
TURB_RATE = 8.0

# Intervals are spread this far around the mean (0.25x to 1.75x) - what produces
# those two extremes: a long draw keeps accelerating one way, short draws stall.
TURB_INTERVAL_JITTER = 0.75

# Fraction of a particle's turbulence direction that is its OWN vs. the direction
# shared by every live particle. The shared part makes newborns leave together as
# a stream; this pulls them apart afterwards. Above ~0.15 the stream never forms.
TURB_MIX = 0.1

# constants.json overrides the defaults above so they can be tuned without editing
# code; missing or malformed entries silently keep the default.
_CONSTANTS = (
    ("measured", "emission_multiplier", "ENGINE_EMISSION_MUL"),
    ("fitted", "vortex_swirl", "VORTEX_SWIRL"),
    ("fitted", "turbulence_rate", "TURB_RATE"),
    ("fitted", "turbulence_interval_jitter", "TURB_INTERVAL_JITTER"),
    ("fitted", "turbulence_per_particle", "TURB_MIX"),
    ("fitted", "spin_rate", "SPIN_RATE"),
)


def load_constants():
    """Re-read constants.json over the built-in defaults. Called on register and whenever an
    effect is loaded or restarted, so tuning a value only needs a click, not a script reload."""
    path = os.path.join(os.path.dirname(__file__), "constants.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:
        print("[pdx_bench] constants.json unreadable, keeping built-in values: %s" % exc)
        return
    g = globals()
    for group, key, name in _CONSTANTS:
        val = (data.get(group) or {}).get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            g[name] = float(val)
        elif val is not None:
            print("[pdx_bench] constants.json: %s.%s is not a number, ignored" % (group, key))


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
    """Recursive-descent parser for the Paradox bracket format. Repeated keys
    (subsystem/animation/force) collect into a list, tracked separately from
    genuine value lists like `velocity={ 20 15 }`."""

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
    """`alpha=150,muzzle_fade` or `alpha={ 10,smoke_fade 10 }` -> (base, curve_ref).
    The braced form is a `{ base spread }` range; the spread is parsed but dropped
    (alpha is not randomised per particle anywhere)."""
    if isinstance(v, list):
        v = v[0] if v else 0
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


def sample_anim(anim, u):
    """Sample an animation and remap it into its authored range: min + curve(u)*(max-min).
    This is the value op=MUL multiplies the field by; for a 0..1 anim it is the raw curve."""
    lo = anim.get("min", 0.0)
    hi = anim.get("max", 1.0)
    return lo + sample_curve(anim["pts"], u) * (hi - lo)


def anim_phase(anim, p, u_life):
    """Where to sample a per-particle curve, honouring `time` and `repeat`:
    life -> age/life; life_abs -> age/duration (absolute seconds); spawn -> frozen
    at birth. repeat=yes wraps the phase into 0..1 instead of clamping."""
    tm = anim["time"]
    if tm == "spawn":
        ph = p.spawn_frac
    elif tm == "life_abs":
        ph = (p.age / anim["dur"]) if anim["dur"] > 0 else u_life
    elif tm == "system" and anim["repeat"]:
        # system = the global clock, shared by all particles. Only meaningful with
        # repeat=yes (a continuous loop); the sim clock stands in for the game clock.
        # system+repeat=no is dead, so it falls through to `life`.
        ph = (SIM.t if SIM is not None else 0.0) / anim["dur"]
    else:  # life (and system without repeat)
        ph = u_life
    if anim["repeat"]:
        return ph - math.floor(ph)
    return 0.0 if ph < 0.0 else (1.0 if ph > 1.0 else ph)


def apply_anim(base, anim, u):
    """Combine an animation with a field's base, honouring `op`. The engine recognises
    two special ops and treats everything else (the default) as ADD:
      MUL = base * value   ABS = value replaces base   ADD/other = base + value"""
    v = sample_anim(anim, u)
    op = anim.get("op", "ADD")
    if op == "MUL":
        return base * v
    if op == "ABS":
        return v
    return base + v


# The .asset (x, y, z) is read as (right, up, forward) - the basis the web Bench
# validated - so positions and force directions map through it.
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
        # A few foreign mods write a key twice (two `texture` blocks); the parser
        # makes that a list. Take the first so `.get` still works.
        def _one(v):
            return (v[0] if v else {}) if isinstance(v, list) else v
        tex = _one(raw.get("texture", {})) or {}
        col = _one(raw.get("color", {})) or {}
        pos = _one(raw.get("position", {})) or {}

        self.idx = idx
        self.name = raw.get("name", "sub%d" % idx)
        # childsystem: a sub-emitter. parent_idx/child_idxs are filled in by Effect.
        # A child emits from each PARENT particle's moving position, not the locator;
        # its start/duration are measured against the parent particle's age.
        self.parent_idx = None
        self.child_idxs = []
        self.max_amount = int(_float(raw.get("max_amount", 0)))
        # emission can carry a curve (`emission=200,fire_emission`, unbraced). It plays
        # over the EMITTER timeline: rate(t) = base * anim((t-start)/anim_duration).
        self.emission, self.emission_s, self.emission_ref = size_of(raw.get("emission", 0))
        self.start = _float(raw.get("start", 0))
        self.duration = _float(raw.get("duration", 0)) if "duration" in raw else 0.0

        self.life_b, self.life_s = as_range(raw.get("life"))
        # emitter_yaw/pitch can carry a spawn-time curve that sweeps the emission
        # direction over the emitter timeline (size_of recovers base+ref).
        self.eyaw_b, self.eyaw_s, self.eyaw_ref = size_of(raw.get("emitter_yaw"))
        self.epitch_b, self.epitch_s, self.epitch_ref = size_of(raw.get("emitter_pitch"))
        self.vyaw_b, self.vyaw_s = as_range(raw.get("velocity_yaw"))
        self.vpitch_b, self.vpitch_s = as_range(raw.get("velocity_pitch"))
        # velocity can carry a curve; parse like size for base+spread+ref. The ref is
        # left unapplied (its one vanilla use is the dead time="system" clock).
        self.vel_b, self.vel_s, self.vel_ref = size_of(raw.get("velocity"))
        # `rotation` can carry a curve: drawn angle is (base+spread) * anim(t), so a
        # base of 0 still spins via the spread. Curve and rotation_speed never coexist.
        self.rot_b, self.rot_s, self.rot_ref = size_of(raw.get("rotation"))
        self.rotspd_b, self.rotspd_s = as_range(raw.get("rotation_speed"))
        self.size_b, self.size_s, self.size_ref = size_of(raw.get("size"))
        self.alpha_b, self.alpha_ref = alpha_of(col.get("alpha"))
        # billboard=no quads are oriented by particle_yaw/pitch: the base aims the whole
        # subsystem, the spread gives each particle its own facing. particle_yaw can also
        # carry a life-time curve that sweeps the facing (searchlights).
        self.pyaw_b, self.pyaw_s, self.pyaw_ref = size_of(raw.get("particle_yaw"))
        self.ppitch_b, self.ppitch_s = as_range(raw.get("particle_pitch"))
        self.pyaw = self.pyaw_b      # base, for the per-subsystem fast path
        self.ppitch = self.ppitch_b
        # rotation_speed_yaw/pitch spin the 3D facing over life (deg/sec), distinct
        # from rotation_speed which rolls the quad within its own plane.
        self.rsyaw_b, self.rsyaw_s = as_range(raw.get("rotation_speed_yaw"))
        self.rspitch_b, self.rspitch_s = as_range(raw.get("rotation_speed_pitch"))
        # rotation_speed_roll is in-plane spin like rotation_speed, so it folds into the
        # roll rate below. Always 0 in the wild, so this reading is unverified.
        self.rsroll_b, self.rsroll_s = as_range(raw.get("rotation_speed_roll"))

        self.offset = (_float(pos.get("x")), _float(pos.get("y")), _float(pos.get("z")))
        # A colour channel has the same grammar as size/alpha - `{ base[,curve] spread }` -
        # so parse via size_of: bare `x=220`, a per-particle spread, or a life curve
        # (fireballs decay a white flash to red this way). r/g/b alias x/y/z; a missing
        # channel defaults to 255 (white).
        self.chan = []
        for xyz, rgb in (("x", "r"), ("y", "g"), ("z", "b")):
            v = col.get(xyz, col.get(rgb, 255))
            base, spread, ref = size_of(v)
            self.chan.append((base / 255.0, spread / 255.0, ref))
        self.color = tuple(min(max(c[0], 0.0), 1.0) for c in self.chan)
        self.col_spread = any(c[1] for c in self.chan)
        self.col_ref = any(c[2] for c in self.chan)
        self.additive = "additive" in str(tex.get("shader", "")).lower()
        self.tex_file = str(tex.get("file", "") or "")
        self.billboard = raw.get("billboard") != "no"
        self.local_space = raw.get("local_space") != "no"
        # Go per-particle whenever an oriented quad's facing can differ between
        # particles: it spins (rotation_speed_yaw/pitch), animates (a particle_yaw
        # curve), or scatters from a particle_yaw/_pitch spread.
        self.orient_per_particle = (not self.billboard) and bool(
            self.rsyaw_b or self.rsyaw_s or self.rspitch_b or self.rspitch_s
            or self.pyaw_ref
            or self.pyaw_s or self.ppitch_s
        )
        self.emitter_type = raw.get("emitter_type", "point")
        # texture atlas / flipbook: x,y = frame grid (1,1 = single static image)
        self.atlas = (
            max(1, int(_float(tex.get("x", 1)))),
            max(1, int(_float(tex.get("y", 1)))),
        )
        # A "sphere" emitter is spherical COORDINATES, each a `{ base spread }` range:
        # radius, yaw around the locator, pitch off the horizontal. So pitch={ 0 0 } is
        # a flat RING, not a ball, and { 0 40 } is a band around the equator.
        self.sphere_r = as_range(raw.get("sphere_emitter_radius"))
        if not self.sphere_r[0] and not self.sphere_r[1]:
            self.sphere_r = (0.06, 0.0)          # no radius authored: a token jitter
        self.sphere_yaw = as_range(raw.get("sphere_emitter_yaw"))
        self.sphere_pitch = as_range(raw.get("sphere_emitter_pitch"))
        # Pulsed emission: emit for _duration seconds, silent for _silence, repeat.
        # The two ONLY work as a pair - one alone emits continuously.
        self.pulse_dur = as_range(raw.get("emission_pulse_duration"))
        self.pulse_sil = as_range(raw.get("emission_pulse_silence"))
        self.pulsed = (
            "emission_pulse_duration" in raw and "emission_pulse_silence" in raw
        )
        self.pulse_half = (
            not self.pulsed
            and ("emission_pulse_duration" in raw or "emission_pulse_silence" in raw)
        )
        # `mass` divides the acceleration a force imparts (a = F/m); it leaves an
        # authored `velocity` alone. Guarded against 0.
        self.mass = _float(raw.get("mass", 1.0)) or 1.0
        # box_emitter_* are `{ base spread }` ranges, not plain widths: a bare
        # `box_emitter_x=10` means base 10, spread 0 (every particle at x=10).
        self.box = (
            as_range(raw.get("box_emitter_x")),
            as_range(raw.get("box_emitter_y")),
            as_range(raw.get("box_emitter_z")),
        )
        force_val = raw.get("force")
        self.forces = (
            [f.strip() for f in force_val.split(",") if f.strip()]
            if isinstance(force_val, str)
            else []
        )
        # `hide=yes` suppresses the subsystem (in vanilla, an off-switch for abandoned
        # content). Confirmed in game.
        self.hide = raw.get("hide") == "yes"
        # `trail=yes` draws nothing at all in this build - not even the particles - so it
        # is treated as unsupported. Confirmed in game; used on one vanilla subsystem.
        self.trail = raw.get("trail") == "yes"
        self.enabled = not (self.hide or self.trail)
        self.live = 0


class Force:
    def __init__(self, raw):
        self.name = raw.get("name", "")
        self.type = raw.get("type", "planar")
        # `amount` can carry a curve (`amount=6,drag_anim`, unbraced), but the curve is
        # DEAD - the force always uses the base. Parsed only to recover that base.
        self.amount, self.amount_s, self.amount_ref = size_of(raw.get("amount"))
        d = raw.get("direction", [0, 1, 0])
        if not isinstance(d, list) or len(d) < 3:
            d = [0, 1, 0]
        self.dir_raw = (_float(d[0]), _float(d[1]), _float(d[2]))
        pv = raw.get("position", [0, 0, 0])
        if not isinstance(pv, list) or len(pv) < 3:
            pv = [0, 0, 0]
        self.pos_raw = (_float(pv[0]), _float(pv[1]), _float(pv[2]))
        # Stable per-force offset so two turbulence fields never drift in lockstep;
        # derived from the name to stay reproducible across reloads. (`division` is
        # inert, so it is not read.)
        self.hash_off = float(sum(ord(c) for c in self.name) % 97)
        self.local = raw.get("local_force") != "no"


class Effect:
    def __init__(self, text):
        root = _Parser(tokenize(text)).parse()
        particle = root.get("particle")
        if isinstance(particle, list):
            # a few foreign mods pack several particle={} blocks per file; preview the first.
            particle = particle[0] if particle else None
        if not particle:
            raise ValueError("no particle={...} block found")
        self.name = particle.get("name", "(unnamed)")
        # Flatten the subsystem tree (top-level + nested childsystems) into one indexed
        # list, each remembering its parent. Children emit from parent particles, not the
        # locator (see Instance.step).
        self.subs = []

        def _add_sub(raw, parent_idx):
            idx = len(self.subs)
            sub = Subsystem(idx, raw)
            sub.parent_idx = parent_idx
            self.subs.append(sub)
            for craw in many(raw, "childsystem"):
                _add_sub(craw, idx)

        for sraw in many(particle, "subsystem"):
            _add_sub(sraw, None)
        for sub in self.subs:
            if sub.parent_idx is not None:
                self.subs[sub.parent_idx].child_idxs.append(sub.idx)
        if not self.subs:
            raise ValueError("particle block has no subsystem={...}")
        self.anims = {}
        for a in many(particle, "animation"):
            curve = a.get("curve")
            # minValue/maxValue remap the normalised 0..1 curve: value = min + curve*(max-min),
            # then op combines it with the field. Most anims are 0..1 (remap is identity).
            self.anims[a.get("name", "")] = {
                "pts": curve if isinstance(curve, list) else [],
                "time": a.get("time", "life"),  # life / life_abs / spawn / system
                "min": _float(a.get("minValue", 0.0)),
                "max": _float(a.get("maxValue", 1.0)) if "maxValue" in a else 1.0,
                "op": a.get("op", "MUL"),  # MUL (vanilla), ADD, or ABS
                # seconds the curve spans: 1 for life anims, real seconds for emission
                # anims (which play over the emitter timeline, not a particle's).
                "dur": _float(a.get("duration", 1.0)) or 1.0,
                "repeat": a.get("repeat") == "yes",
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
            if s.pulse_half:
                # 9 of vanilla's 13 pulse users set only one half and so pulse nothing -
                # easy to copy from a vanilla file and believe.
                out.append(
                    "%s: emission_pulse_duration and _silence only work as a PAIR - one alone "
                    "does nothing, emission stays continuous" % s.name
                )
        spaces = {s.local_space for s in self.subs}
        alpha_subs = [s.name for s in self.subs if not s.additive]
        if len(spaces) > 1 and alpha_subs:
            out.append(
                "mixed local_space + alpha-blend (%s): HoI4 may silently drop them"
                % ", ".join(alpha_subs)
            )
        # `vortex` is only modelled for an UPRIGHT axis; a tilted axis sends particles up
        # at an angle no simple force law reproduces. Rare (3 subsystems, the only
        # non-zero one upright), so it is flagged rather than chased.
        for f in self.forces.values():
            if f.type != "vortex":
                continue
            tilted = abs(f.dir_raw[0]) > 1e-6 or abs(f.dir_raw[2]) > 1e-6
            if tilted and f.amount:
                out.append(
                    "%s: vortex with a TILTED axis is not modelled correctly - the preview "
                    "will be wrong (upright axes are fine)" % f.name
                )
        return out


# =============================================================================
# Simulation (1:1 port of the validated web Bench model)
# =============================================================================


class Particle:
    __slots__ = ("si", "pos", "vel", "age", "life", "rot", "rotspd",
                 "size0", "spawn_frac", "local", "seed", "col",
                 "oyaw", "opitch", "osyaw", "ospitch",
                 "turb_dir", "turb_next", "turb_n", "mat", "child_budget")

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
        self.seed = 0.0  # per-particle noise seed (turbulence force)
        self.col = None  # per-particle colour, only when a channel has a spread
        self.oyaw = 0.0    # oriented-quad facing at spawn (deg), only when orient_per_particle
        self.opitch = 0.0
        self.osyaw = 0.0   # facing spin rate (deg/sec): rotation_speed_yaw / _pitch
        self.ospitch = 0.0
        self.turb_dir = Vector((0.0, 0.0, 0.0))  # current turbulence push direction
        self.turb_next = 0.0                     # age at which it is re-rolled
        self.turb_n = 0                          # how many times it has been re-rolled
        self.mat = None  # emitter matrix captured at spawn (world-space particles)
        self.child_budget = None  # per-child emission accumulator, only on parent particles


class Instance:
    """One fired effect (one HoI4 event)."""

    def __init__(self, effect, start):
        self.effect = effect
        self.start = start
        self.parts = []
        # Pulse timings, rolled once per fired effect and then fixed - see step().
        self.pulse = [None] * len(effect.subs)
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

        # --- emission (locator subsystems only; children emit from parents, see below) ---
        for si, s in enumerate(eff.subs):
            if s.parent_idx is not None:
                continue
            if not s.enabled or s.duration == 0:
                continue
            in_window = t_local >= s.start and (
                s.duration < 0 or t_local < s.start + s.duration
            )
            if not in_window:
                continue
            if s.pulsed:
                if self.pulse[si] is None:
                    # _silence is a range, drawn ONCE per effect (not per cycle): a range
                    # varies the gap between firings, not the rhythm within one.
                    self.pulse[si] = (
                        max(0.0, s.pulse_dur[0] + _spread(s.pulse_dur[1], cfg["spread"], rng)),
                        max(0.0, s.pulse_sil[0] + _spread(s.pulse_sil[1], cfg["spread"], rng)),
                    )
                pdur, psil = self.pulse[si]
                cycle = pdur + psil
                if cycle > 0 and (t_local - s.start) % cycle >= pdur:
                    continue  # silent half: nothing emitted and no budget carried over
            rate = s.emission
            if s.emission_ref:
                anim = eff.anims.get(s.emission_ref)
                if anim:
                    # curve plays over the emitter timeline, sampled at the elapsed
                    # fraction (not frozen per particle). min/max still remap.
                    phase = (t_local - s.start) / anim["dur"]
                    rate = apply_anim(s.emission, anim, min(max(phase, 0.0), 1.0))
            self.budget[si] += rate * ENGINE_EMISSION_MUL * cfg["emission"] * dt
            while self.budget[si] >= 1.0:
                self.budget[si] -= 1.0
                if self.count[si] >= s.max_amount:
                    break
                self._spawn(si, s, t_local, cfg, rng, emitter_mat, fwd, up, right)

        # --- integrate ---
        # A force `amount` CURVE is dead (all 7 vanilla uses are the inert time="system"
        # clock), so the force always uses the plain base.
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
                    # friction is a real force, divided by mass like the rest, so the
                    # decay rate is amount/mass.
                    p.vel *= math.exp(-f.amount * cfg["friction"] * dt / s.mass)
                elif f.type == "spin":
                    # `type="spin"` orbit force: rotate the position about the axis at a
                    # constant angular rate (radius preserved). amount = rad/s, CCW about
                    # +axis. No vanilla file uses it.
                    center = right * f.pos_raw[0] + up * f.pos_raw[1] + fwd * f.pos_raw[2]
                    axis = right * f.dir_raw[0] + up * f.dir_raw[1] + fwd * f.dir_raw[2]
                    if rot3 is not None and not p.local:
                        # world-space particle: axis is world, centre stays at the emitter.
                        center = emitter_mat @ center
                    ax = axis.normalized() if axis.length > 1e-9 else up
                    ang = f.amount * SPIN_RATE * cfg["force"] * dt
                    rel = Matrix.Rotation(ang, 4, ax) @ (p.pos - center)
                    p.pos = center + rel
                elif f.type in ("point", "vortex"):
                    # point/vortex: `amount` is acceleration in u/s^2, like planar.
                    # `position` uses the FORCE axes (x=right, y=up, z=fwd). For a
                    # local_space=no particle the axis is WORLD but the centre stays
                    # emitter-relative (the particle still spawns at the emitter).
                    center = right * f.pos_raw[0] + up * f.pos_raw[1] + fwd * f.pos_raw[2]
                    axis = right * f.dir_raw[0] + up * f.dir_raw[1] + fwd * f.dir_raw[2]
                    if rot3 is not None and not p.local:
                        center = emitter_mat @ center
                    to_p = p.pos - center
                    if f.type == "point":
                        # negative `amount` pulls TOWARD the point, positive pushes away.
                        d = to_p.normalized() if to_p.length > 1e-9 else Vector((0.0, 0.0, 0.0))
                    else:
                        # mostly a radial push (magnitude `amount`) plus a slight swirl.
                        ax = axis.normalized() if axis.length > 1e-9 else up
                        radial = to_p - ax * to_p.dot(ax)
                        if radial.length > 1e-9:
                            d = radial.normalized() + ax.cross(radial).normalized() * VORTEX_SWIRL
                        else:
                            d = Vector((0.0, 0.0, 0.0))
                    if d.length > 1e-9:
                        p.vel += d * (f.amount * cfg["force"] * dt / s.mass)
                elif f.type == "turbulence":
                    # Turbulence in two parts: a SHARED direction every live particle gets
                    # (makes newborns, all on the emitter, leave together as a stream) and a
                    # PER-PARTICLE one re-rolled at random intervals (pulls them apart). All
                    # derived from p.seed / the force hash, so scrubbing replays identically.
                    if p.age >= p.turb_next:
                        p.turb_n += 1
                        p.turb_dir = _rand_dir(p.seed * 7.13 + p.turb_n * 13.7 + f.hash_off)
                        mean = 1.0 / TURB_RATE
                        jitter = 1.0 + TURB_INTERVAL_JITTER * (
                            _hash01(p.seed * 3.3 + p.turb_n * 5.1) * 2.0 - 1.0
                        )
                        p.turb_next = p.age + mean * jitter
                    shared = _rand_dir(
                        math.floor(t_global * TURB_RATE) * 2.7 + f.hash_off + 0.5
                    )
                    d = shared * (1.0 - TURB_MIX) + p.turb_dir * TURB_MIX
                    if d.length > 1e-9:
                        p.vel += d.normalized() * (f.amount * cfg["force"] * dt / s.mass)
                else:
                    # planar. direction maps .asset (x, y, z) -> locator local (X, Z, Y),
                    # the same Y<->Z swap as io_pdx_mesh's SPACE_MATRIX. For a
                    # local_space=no particle the direction is WORLD-referenced (the
                    # locator rotation is NOT applied); local_space=yes rides the emitter
                    # via the draw transform.
                    d = right * f.dir_raw[0] + up * f.dir_raw[1] + fwd * f.dir_raw[2]
                    p.vel += d * (f.amount * cfg["force"] * dt / s.mass)
            p.pos += p.vel * (cfg["world"] * dt)
            p.rot += p.rotspd * dt

        # --- childsystem emission: each parent particle emits its children from its own
        # moving position, so the burst rides the parent. Child start/duration are vs. the
        # parent's age; children live independently once spawned. Snapshot the parents
        # first - _spawn appends the new children to self.parts.
        for p in list(self.parts):
            if p.age >= p.life:
                continue
            ps = eff.subs[p.si]
            if not ps.child_idxs:
                continue
            if p.child_budget is None:
                p.child_budget = {}
            for ci in ps.child_idxs:
                cs = eff.subs[ci]
                if not cs.enabled or cs.duration == 0:
                    continue
                in_win = p.age >= cs.start and (
                    cs.duration < 0 or p.age < cs.start + cs.duration
                )
                if not in_win:
                    continue
                b = p.child_budget.get(ci, 0.0) + (
                    cs.emission * ENGINE_EMISSION_MUL * cfg["emission"] * dt
                )
                while b >= 1.0:
                    b -= 1.0
                    if self.count[ci] >= cs.max_amount:
                        break
                    self._spawn(ci, cs, t_local, cfg, rng, emitter_mat,
                                fwd, up, right, base_pos=p.pos)
                p.child_budget[ci] = b

        self.parts = [p for p in self.parts if p.age < p.life]
        if t_local > eff.window() and sum(self.count) <= 0:
            self.done = True

    def _spawn(self, si, s, t_local, cfg, rng, emitter_mat, fwd, up, right, base_pos=None):
        mode = cfg["spread"]
        # position along the emitter timeline, 0..1 - drives every spawn-time curve.
        sfrac = (
            min(max((t_local - s.start) / s.duration, 0.0), 1.0) if s.duration > 0 else 0.0
        )

        def _emit_base(base, ref):
            # A spawn-time curve sweeps the emission DIRECTION over the emitter timeline.
            if ref:
                anim = self.effect.anims.get(ref)
                if anim and anim["time"] == "spawn":
                    return apply_anim(base, anim, sfrac)
            return base

        # emitter_yaw/pitch aims the emitter; velocity_yaw/pitch adds to it. Both bases
        # count (plus their spreads) - a base velocity_pitch is what stands rising smoke up.
        yaw = math.radians(
            _emit_base(s.eyaw_b, s.eyaw_ref) + s.vyaw_b
            + _spread(s.eyaw_s, mode, rng) + _spread(s.vyaw_s, mode, rng)
        )
        # io_pdx_mesh's SPACE_MATRIX is a mirror (Y/Z swap, det -1), so yaw handedness
        # arrives inverted and is negated back. Effects with yaw base 0 are unaffected.
        if not cfg["flip_yaw"]:
            yaw = -yaw
        pitch = math.radians(
            _emit_base(s.epitch_b, s.epitch_ref) + s.vpitch_b
            + _spread(s.epitch_s, mode, rng) + _spread(s.vpitch_s, mode, rng)
        )
        speed = s.vel_b + _spread(s.vel_s, mode, rng)

        # HoI4 fires emitter_yaw=0 along the locator's local -Y (-fwd), NOT +Y - a fixed
        # engine convention. Position/orientation ride the mesh matrix and need no flip.
        muzzle = -fwd
        direction = (
            muzzle * (math.cos(pitch) * math.cos(yaw))
            + right * (math.cos(pitch) * math.sin(yaw))
            + up * math.sin(pitch)
        )

        # .asset position axes: x = forward, y = up, z = right (pinned down in Blender).
        pos = fwd * s.offset[0] + up * s.offset[1] + right * s.offset[2]
        if s.emitter_type == "sphere":
            # spherical coordinates (see sphere_r). yaw=0 sits along -fwd (the muzzle
            # axis), the same convention as emitter_yaw, NOT +fwd.
            sr = s.sphere_r[0] + _spread(s.sphere_r[1], mode, rng)
            sy = math.radians(s.sphere_yaw[0] + _spread(s.sphere_yaw[1], mode, rng))
            sp = math.radians(s.sphere_pitch[0] + _spread(s.sphere_pitch[1], mode, rng))
            cp = math.cos(sp)
            pos = pos + (
                -fwd * (cp * math.cos(sy)) + right * (cp * math.sin(sy)) + up * math.sin(sp)
            ) * sr
        elif s.emitter_type == "box":
            pos = pos + (
                fwd * (s.box[0][0] + _spread(s.box[0][1], mode, rng))
                + up * (s.box[1][0] + _spread(s.box[1][1], mode, rng))
                + right * (s.box[2][0] + _spread(s.box[2][1], mode, rng))
            )

        p = Particle()
        p.si = si
        p.life = max(0.01, s.life_b + _spread(s.life_s, mode, rng))
        p.size0 = max(0.0, s.size_b + _spread(s.size_s, mode, rng))
        p.rot = s.rot_b + _spread(s.rot_s, mode, rng)
        p.rotspd = (s.rotspd_b + s.rsroll_b) + _spread(s.rotspd_s, mode, rng)
        if s.orient_per_particle:
            # Facing and spin are both rolled once here; the draw advances the facing by
            # spin * age each frame, the same way p.rot advances by p.rotspd.
            p.oyaw = s.pyaw_b + _spread(s.pyaw_s, mode, rng)
            p.opitch = s.ppitch_b + _spread(s.ppitch_s, mode, rng)
            p.osyaw = s.rsyaw_b + _spread(s.rsyaw_s, mode, rng)
            p.ospitch = s.rspitch_b + _spread(s.rspitch_s, mode, rng)
        p.spawn_frac = sfrac
        if s.vel_ref:
            # animating velocity requires time="spawn" (frozen per particle at spawn_frac);
            # a time="system" velocity curve is dead. So only spawn is honoured.
            anim = self.effect.anims.get(s.vel_ref)
            if anim and anim["time"] == "spawn":
                speed = apply_anim(speed, anim, p.spawn_frac)
        p.local = s.local_space
        p.seed = rng.uniform(0.0, 1000.0)
        if s.col_spread:
            # rolled once at spawn like life/size: a channel spread tints each particle.
            p.col = tuple(
                min(max(c[0] + _spread(c[1], mode, rng), 0.0), 1.0) for c in s.chan
            )

        if base_pos is not None:
            # childsystem spawn: origin is the parent particle's position, not the locator;
            # offset/velocity are placed relative to it, rotated into world by the emitter.
            r3 = emitter_mat.to_3x3() if emitter_mat is not None else None
            off = (r3 @ pos) if r3 is not None else pos
            vel = (r3 @ (direction * speed)) if r3 is not None else direction * speed
            p.pos = base_pos + off
            p.vel = vel
            p.mat = None
        elif s.local_space or emitter_mat is None:
            # local frame - rides the locator.
            p.pos = pos
            p.vel = direction * speed
            p.mat = None
        else:
            # local_space=no: baked into world at spawn, does not follow the locator.
            p.pos = emitter_mat @ pos
            p.vel = emitter_mat.to_3x3() @ (direction * speed)
            p.mat = None

        self.parts.append(p)
        self.count[si] += 1


def _hash01(x):
    """Deterministic pseudo-random in [0,1), stable across reloads."""
    v = math.sin(x * 12.9898) * 43758.5453
    return v - math.floor(v)


def _rand_dir(seed):
    """Evenly distributed unit vector from a seed (z uniform, then a ring around it)."""
    a = _hash01(seed) * 2.0 * math.pi
    z = _hash01(seed + 7.77) * 2.0 - 1.0
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return Vector((math.cos(a) * r, math.sin(a) * r, z))


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
        # Subsystems hidden from the viewport. Draw-time only: the sim still steps
        # them, so toggling is instant and cannot disturb the shared particle stream.
        self.muted = set()

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
# Texture resolution - .asset paths are game-relative, looked up in the mod root
# first then vanilla (like HoI4; most textures live in vanilla). .dds reads natively.
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
            img.colorspace_settings.name = "Non-Color"  # raw texels, no sRGB transform
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
    /* Procedural radial falloff stands in for the .dds; quad SIZE is what matters. */
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
    /* The engine MULTIPLIES color= by the texture (a tinted texture shifts the
       result); shape comes from the alpha channel. */
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


VERT_BG = """
in vec2 pos;
/* z just inside the far plane (some drivers clip exactly 1.0), behind the mesh. */
void main() { gl_Position = vec4(pos, 0.9999, 1.0); }
"""

FRAG_BG = """
uniform vec4 color;
out vec4 fragColor;
void main() { fragColor = color; }
"""

_shader_bg = None


def get_bg_shader():
    global _shader_bg
    if _shader_bg is None:
        _shader_bg = gpu.types.GPUShader(VERT_BG, FRAG_BG)
    return _shader_bg


def _draw_background(lum):
    """Fill the empty background (not the mesh) with flat grey, so ADDITIVE effects can
    be judged against a bright scene instead of black. Far plane, LESS_EQUAL, no depth
    write, so the mesh stays visible. A linear stand-in, not the engine's tonemap."""
    shader = get_bg_shader()
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": [(-1, -1), (1, -1), (1, 1), (-1, 1)]})
    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    shader.bind()
    shader.uniform_float("color", (lum, lum, lum, 1.0))
    batch.draw(shader)


def _visual(p, s, effect):
    """size and alpha after the animation curves, exactly as the web Bench."""
    u = min(max(p.age / p.life, 0.0), 1.0)
    size = p.size0
    if s.size_ref:
        anim = effect.anims.get(s.size_ref)
        if anim:
            size = apply_anim(size, anim, anim_phase(anim, p, u))
    # alpha and colour combine in 0..255 space (so ADD/ABS read in native units), then
    # normalise.
    alpha = s.alpha_b
    if s.alpha_ref:
        anim = effect.anims.get(s.alpha_ref)
        if anim:
            alpha = apply_anim(alpha, anim, anim_phase(anim, p, u))
    alpha /= 255.0
    col = p.col or s.color
    if s.col_ref:
        out = []
        for i, (base, _spr, ref) in enumerate(s.chan):
            c = col[i] * 255.0
            if ref:
                anim = effect.anims.get(ref)
                if anim:
                    c = apply_anim(c, anim, anim_phase(anim, p, u))
            out.append(min(max(c / 255.0, 0.0), 1.0))
        col = tuple(out)
    rot = p.rot
    if s.rot_ref:
        # angle animated over life: (base+spread, baked into p.rot at spawn) * anim(t).
        # A subsystem with a curve carries no rotation_speed, so p.rot has not accumulated.
        anim = effect.anims.get(s.rot_ref)
        if anim:
            rot = apply_anim(p.rot, anim, anim_phase(anim, p, u))
    return size, min(max(alpha, 0.0), 1.0), col, rot


def oriented_quad_axes(s, axis_key, flip_yaw, flip_plume, rot3):
    """In-plane axes for a `billboard=no` quad, locked to the emitter not the camera.
    The quad is placed by a real rotation: yaw about up, then pitch about the yawed
    side axis. The plane normal is the rotated forward axis, so `particle_yaw` steers
    the streak inside the plane as well as the plane itself.
      pitch=90 -> quad lies flat    pitch=0 -> quad stands up
      yaw=pitch=0 -> faces down the barrel"""
    return _oriented_axes(s.pyaw, s.ppitch, axis_key, flip_yaw, flip_plume, rot3)


def _oriented_axes(pyaw_deg, ppitch_deg, axis_key, flip_yaw, flip_plume, rot3):
    """Body of oriented_quad_axes with an explicit facing, so it can be called per
    particle (scatter / rotation_speed_yaw/pitch) or per subsystem (fixed facing)."""
    fwd, up, right = basis(axis_key)
    yaw = math.radians(pyaw_deg if flip_yaw else -pyaw_deg)
    pitch = math.radians(ppitch_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    # normal is yawed_fwd*cp + up*sp, recovered as U x V rather than built separately.
    yawed_fwd = fwd * cy + right * sy
    u = -fwd * sy + right * cy          # yawed side axis - also the pitch axis
    v = -yawed_fwd * sp + up * cp

    # the plume texture is asymmetric, so U's direction is visible; a test knob.
    if flip_plume:
        u = -u
        v = -v

    u.normalize()
    v.normalize()

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
    if props.bg_luminance > 0.0:
        _draw_background(props.bg_luminance)
    view_mat = region_3d.view_matrix
    cam_right = Vector((view_mat[0][0], view_mat[0][1], view_mat[0][2]))
    cam_up = Vector((view_mat[1][0], view_mat[1][1], view_mat[1][2]))

    emitter_mat = props.target.matrix_world if props.target else None
    emitter_rot = emitter_mat.to_3x3() if emitter_mat else None
    size_gain = 1.0  # size is world units 1:1 (calibrated); no user knob

    # Bucket by subsystem so each can use its own blend mode.
    buckets = {}
    for inst in SIM.instances:
        for p in inst.parts:
            s = effect.subs[p.si]
            if not s.enabled:
                continue
            size, alpha, pcol, prot = _visual(p, s, effect)
            if alpha <= 0.003 or size <= 0.0:
                continue
            world_pos = (emitter_mat @ p.pos) if (p.local and emitter_mat) else p.pos
            u_life = min(p.age / p.life, 1.0) if p.life > 0 else 0.0
            oaxes = None
            if s.orient_per_particle:
                # this particle faces its own way (and may spin), so build its quad axes
                # from its current facing, not the shared per-subsystem pair.
                rot_for_orient = emitter_rot if s.local_space else None
                yaw_now = p.oyaw
                if s.pyaw_ref:
                    # particle_yaw curve sweeps the facing over life (searchlights).
                    anim = effect.anims.get(s.pyaw_ref)
                    if anim:
                        yaw_now = apply_anim(p.oyaw, anim, anim_phase(anim, p, u_life))
                oaxes = _oriented_axes(
                    yaw_now + p.osyaw * p.age, p.opitch + p.ospitch * p.age,
                    props.axis_preset, False, False, rot_for_orient,
                )
            buckets.setdefault(p.si, []).append(
                (world_pos, size, alpha, prot, u_life, oaxes)
            )

    if not buckets:
        return

    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)

    for si, items in buckets.items():
        if si in SIM.muted:
            continue
        s = effect.subs[si]
        # Painter's order for alpha-blended smoke; additive is order-independent.
        if not s.additive:
            eye = Vector(region_3d.view_matrix.inverted().translation)
            items.sort(key=lambda it: -(it[0] - eye).length_squared)

        # billboard=yes faces the camera. billboard=no is oriented by the emitter, but
        # ONLY when local_space=yes; a local_space=no quad is oriented by WORLD axes (the
        # locator sets position, not facing).
        if s.billboard:
            ax_u, ax_v = cam_right, cam_up
        else:
            rot_for_orient = emitter_rot if s.local_space else None
            ax_u, ax_v = oriented_quad_axes(
                s, props.axis_preset, False, False, rot_for_orient
            )

        nx, ny = s.atlas
        nframes = nx * ny
        coords, uvs, cols, indices = [], [], [], []
        for n, (wp, size, alpha, rot, u_life, oaxes) in enumerate(items):
            half = size * size_gain * 0.5
            au, av = oaxes if oaxes is not None else (ax_u, ax_v)
            ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            rx = (au * ca + av * sa) * half
            ry = (av * ca - au * sa) * half
            coords.extend([wp - rx - ry, wp + rx - ry, wp + rx + ry, wp - rx + ry])
            if nframes > 1:
                # flipbook: pick the frame from the life fraction, emit its UV sub-rect.
                fr = min(int(u_life * nframes), nframes - 1)
                cx, cy = fr % nx, fr // nx
                u0, u1 = cx / nx, (cx + 1) / nx
                # texture row 0 is the TOP; GPU uv v=0 is the BOTTOM, so flip the rows.
                v1, v0 = 1.0 - cy / ny, 1.0 - (cy + 1) / ny
                uvs.extend([(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
            else:
                uvs.extend([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
            rgba = (pcol[0], pcol[1], pcol[2], alpha)
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
    # world/force/friction/emission are calibrated 1:1, spread symmetric, yaw negated,
    # forward -fwd - all measured, so fixed here rather than exposed as knobs.
    return {
        "world": 1.0,
        "force": 1.0,
        "friction": 1.0,
        "emission": 1.0,
        "spread": "SYM",
        "refire": props.refire_frames,
        "axis": props.axis_preset,
        "flip_yaw": False,
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


def _on_display_change(self, context):
    # Pure viewport change - no need to re-simulate, just repaint.
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
    browse_vanilla: bpy.props.BoolProperty(
        name="Browse from vanilla particles",
        description="Open the .asset Browse dialog in the VANILLA game's gfx/particles instead "
                    "of the mod's. Texture resolution (mod first, then vanilla) is unaffected",
        default=False,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "mod_root")
        col.prop(self, "vanilla_root")
        col.prop(self, "browse_vanilla")
        col.label(
            text="Texture paths in .asset resolve against the mod first, then vanilla.",
            icon="INFO",
        )


class PPB_Props(bpy.types.PropertyGroup):
    asset_path: bpy.props.StringProperty(
        name="Asset", description="HoI4 particle .asset file (use Browse, or paste a path)"
    )
    target: bpy.props.PointerProperty(
        name="Locator",
        description="Empty/object the effect is attached to - a locator from the imported mesh",
        type=bpy.types.Object,
    )
    enabled: bpy.props.BoolProperty(name="Show", default=True)

    axis_preset: bpy.props.EnumProperty(
        name="Axes",
        description="Which local axis is your mesh's forward/up. The emitter direction and "
                    "billboard=no quads are oriented relative to it. Default is +Y fwd",
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
    refire_frames: bpy.props.IntProperty(
        name="Refire", description="Re-fire every N frames (0 = single shot)",
        default=0, min=0, max=120, update=_on_knob_change,
    )



    bg_luminance: bpy.props.FloatProperty(
        name="Scene background",
        description="Fill the empty background with a flat grey of this luminance to judge how "
                    "ADDITIVE effects read against the game's scene instead of a black viewport. "
                    "0 = dark (every faint additive layer shows); ~0.3-0.4 approximates a typical "
                    "mid scene; 0.6+ reads near-white. A linear approximation, not the engine's "
                    "exact tonemap - it will not match the game pixel for pixel.",
        default=0.0, min=0.0, max=1.0, update=_on_display_change,
    )


class PPB_OT_browse(bpy.types.Operator):
    bl_idname = "pdx_pb.browse"
    bl_label = "Browse"
    bl_description = (
        "Pick a particle .asset and load it. The dialog opens in the mod's gfx/particles "
        "(or the vanilla game's, if 'Browse from vanilla particles' is ticked in Preferences)"
    )
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.asset", options={"HIDDEN"})

    def invoke(self, context, event):
        prefs = get_prefs()
        if prefs:
            base = prefs.vanilla_root if prefs.browse_vanilla else prefs.mod_root
            if base:
                start = os.path.join(bpy.path.abspath(base), "gfx", "particles")
                if os.path.isdir(start):
                    self.filepath = start + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.pdx_pb.asset_path = self.filepath
        return bpy.ops.pdx_pb.load()


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
        SIM.muted = set()
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
        load_constants()
        _last_frame[0] = None
        update_sim(context.scene, force_reset=True)
        _tag_redraw()
        return {"FINISHED"}


class PPB_OT_reroll(bpy.types.Operator):
    bl_idname = "pdx_pb.reroll"
    bl_label = "Re-roll"
    bl_description = (
        "Draw a different random outcome - new spawn directions, lifetimes, spreads and "
        "turbulence timings. The preview stays deterministic WITHIN a roll, so scrubbing the "
        "timeline replays exactly the same thing; this is how to see the variety the game "
        "shows every time an effect restarts, instead of judging a single fixed outcome"
    )

    def execute(self, context):
        # same generator, different stream. Deterministic within a roll keeps a scrub
        # from flickering; judging a random effect off one outcome is the trap this avoids.
        SIM.seed = (SIM.seed * 1103515245 + 12345) & 0x7FFFFFFF
        _last_frame[0] = None
        update_sim(context.scene, force_reset=True)
        _tag_redraw()
        return {"FINISHED"}


class PPB_OT_mute_sub(bpy.types.Operator):
    """Hide one subsystem so the rest can be read on its own - an effect stacks
    subsystems, and a big camera-facing fire mass buries thin quads underneath."""

    bl_idname = "pdx_pb.mute_sub"
    bl_label = "Mute Subsystem"
    bl_description = "Hide this subsystem in the viewport (simulation keeps running)"

    index: bpy.props.IntProperty()

    def execute(self, context):
        SIM.muted.symmetric_difference_update({self.index})
        _tag_redraw()
        return {"FINISHED"}


class PPB_OT_solo_sub(bpy.types.Operator):
    bl_idname = "pdx_pb.solo_sub"
    bl_label = "Solo Subsystem"
    bl_description = "Show only this subsystem. Click again to show all"

    index: bpy.props.IntProperty()

    def execute(self, context):
        total = len(SIM.effect.subs) if SIM.effect else 0
        others = set(range(total)) - {self.index}
        # Already soloed -> second click restores everything.
        SIM.muted = set() if SIM.muted == others else others
        _tag_redraw()
        return {"FINISHED"}


class PPB_OT_show_all_subs(bpy.types.Operator):
    bl_idname = "pdx_pb.show_all_subs"
    bl_label = "Show All"
    bl_description = "Unmute every subsystem"

    def execute(self, context):
        SIM.muted = set()
        _tag_redraw()
        return {"FINISHED"}


def _find_pdx():
    # io_pdx_mesh may be a legacy add-on or a Blender 4.2+ extension (different module
    # names), so find the already-loaded modules by name-tail. Returns (bie, top),
    # either may be None.
    import sys

    bie = top = None
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if name.endswith("pdx_blender.blender_import_export") and hasattr(mod, "import_meshfile"):
            bie = mod
        if (name == "io_pdx_mesh" or name.endswith(".io_pdx_mesh")) and hasattr(mod, "IO_PDX_SETTINGS"):
            top = mod
    return bie, top


def _import_in_view3d(meshpath, import_meshfile):
    # import_meshfile runs bpy.ops that need a VIEW_3D context (mode_set, join); a bare
    # timer has none, so run under a temp_override onto the first VIEW_3D area.
    override = None
    for win in bpy.context.window_manager.windows:
        area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
        if area is not None:
            override = {"window": win, "area": area}
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                override["region"] = region
            break
    try:
        if override is not None:
            with bpy.context.temp_override(**override):
                import_meshfile(meshpath)
        else:
            import_meshfile(meshpath)
    except Exception as exc:  # no operator context here - log to console
        print("[PDX Particle Bench] round-trip import failed:", exc)
    return None


class PPB_OT_roundtrip(bpy.types.Operator):
    bl_idname = "pdx_pb.roundtrip"
    bl_label = "Round-trip model for Bench"
    bl_description = (
        "Open io_pdx_mesh's own export dialog (its selected / skeleton / locators options), "
        "then re-import the exported .mesh into a NEW empty file - the exact coordinate "
        "state the Bench is calibrated for, so you can author particles on a model you just "
        "built. REPLACES the session with the import; your original .blend on disk is left "
        "untouched, so save it (Ctrl+S) first"
    )

    def invoke(self, context, event):
        bie, top = _find_pdx()
        if bie is None or top is None:
            self.report({"ERROR"}, "io_pdx_mesh add-on not found - is it enabled?")
            return {"CANCELLED"}
        # require the .blend saved and clean - round-trip replaces the session, so
        # anything a .mesh export cannot carry (modifiers, extra objects) would be lost.
        if not bpy.data.filepath or bpy.data.is_dirty:
            self.report(
                {"ERROR"},
                "Save your .blend first (Ctrl+S) - round-trip replaces this session with the import.",
            )
            return {"CANCELLED"}

        settings = top.IO_PDX_SETTINGS
        import_meshfile = bie.import_meshfile
        prev = getattr(settings, "last_export_mesh", "") or ""
        prev_mtime = os.path.getmtime(prev) if prev and os.path.isfile(prev) else -1.0
        t0 = time.time()

        # Hand off to io_pdx_mesh's export dialog. It gives no completion callback, so
        # watch last_export_mesh; once it points to a freshly written file, import it.
        bpy.ops.io_pdx_mesh.export_mesh("INVOKE_DEFAULT")

        def _await_export():
            if time.time() - t0 > 300.0:
                return None  # user likely cancelled the dialog - stop watching
            cur = getattr(settings, "last_export_mesh", "") or ""
            if cur and os.path.isfile(cur):
                try:
                    mtime = os.path.getmtime(cur)
                except OSError:
                    return 0.5
                if mtime >= t0 - 2.0 and (cur != prev or mtime > prev_mtime):
                    path = cur
                    bpy.ops.wm.read_homefile(use_empty=True)
                    # import on the next tick, under a VIEW_3D override (see _import_in_view3d).
                    bpy.app.timers.register(
                        lambda: _import_in_view3d(path, import_meshfile),
                        first_interval=0.1,
                    )
                    return None
            return 0.5

        bpy.app.timers.register(_await_export, first_interval=0.5)
        self.report(
            {"INFO"}, "Export dialog open - a fresh file with the import follows once you export."
        )
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

        # Blender 4.x defaults to the AgX view transform, which desaturates saturated
        # colour; this is a measuring instrument, so warn to switch to Standard.
        if context.scene.view_settings.view_transform != "Standard":
            layout.label(text="Colour is tone-mapped by view transform", icon="INFO")
            layout.label(text="Render > Color Management > Standard to compare")

        row = layout.row(align=True)
        row.operator("pdx_pb.roundtrip", icon="IMPORT")
        layout.separator()

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "asset_path", text="")
        row.operator("pdx_pb.browse", text="", icon="FILEBROWSER")
        col.prop(props, "target", text="Locator")
        row = layout.row(align=True)
        row.operator("pdx_pb.load", icon="FILE_REFRESH")
        row.operator("pdx_pb.reset", icon="LOOP_BACK")
        row.operator("pdx_pb.reroll", icon="MOD_NOISE")
        layout.prop(props, "enabled")

        effect = SIM.effect
        if effect is None:
            layout.label(text="No effect loaded", icon="INFO")
            return

        box = layout.box()
        head = box.row(align=True)
        head.label(text=effect.name, icon="PARTICLES")
        if SIM.muted:
            head.operator("pdx_pb.show_all_subs", text="Show All", icon="HIDE_OFF")
        for i, s in enumerate(effect.subs):
            row = box.row(align=True)
            hidden = i in SIM.muted
            row.operator(
                "pdx_pb.mute_sub",
                text="",
                icon="HIDE_ON" if hidden else "HIDE_OFF",
                depress=hidden,
            ).index = i
            row.operator("pdx_pb.solo_sub", text="", icon="RADIOBUT_ON").index = i
            sub = row.row(align=True)
            sub.active = not hidden and s.enabled
            # spell out an asset-level hide/trail, else the row just reads 0/max and
            # looks like an unexplained "nothing spawning".
            if s.hide:
                state = "hide=yes (off in game)"
            elif s.trail:
                state = "trail=yes (drops it in game)"
            else:
                state = "%d/%d" % (s.live, s.max_amount)
            sub.label(
                text="%s  %s  %s"
                % (s.name, state, "ADD" if s.additive else "ALPHA")
            )

        for msg in effect.lints():
            layout.label(text=msg, icon="ERROR")

        box = layout.box()
        box.prop(props, "axis_preset")
        box.prop(props, "refire_frames")

        box = layout.box()
        box.label(text="Display (preview only, not simulated)")
        box.prop(props, "bg_luminance", slider=True)
        note = box.column(align=True)
        note.scale_y = 0.72
        note.label(text="Approximation, not an exact game match", icon="INFO")
        note.label(text="(different, older engine + render pipeline).")

        # which build is running - this add-on tends to exist in several copies at once
        # (repo, Blender's addons dir, a mod's tools folder).
        layout.separator()
        row = layout.row()
        row.alignment = "RIGHT"
        row.label(text="v{}.{}.{}".format(*bl_info["version"]))


CLASSES = (
    PPB_Prefs,
    PPB_Props,
    PPB_OT_browse,
    PPB_OT_load,
    PPB_OT_reset,
    PPB_OT_reroll,
    PPB_OT_mute_sub,
    PPB_OT_solo_sub,
    PPB_OT_show_all_subs,
    PPB_OT_roundtrip,
    PPB_PT_panel,
)


def register():
    global _draw_handle
    load_constants()
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
