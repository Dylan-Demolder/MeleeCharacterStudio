"""Knockback, hitstun and KO percents, computed the way Melee does.

The formula is ``ftColl_80079EA8`` with the constants in PlCo.dat's ftCommonData
(weight x0.01, 1.4, 18, 0.1 and 0.05 per percent, cap 2500); the 361 angle is
``ftCo_Damage_CalcAngle`` (0 below 32 knockback on the ground, 44 from 32.1, 45
in the air); hitstun is knockback x 0.4 and tumble starts at 32 hitstun frames
(``ftCo_8008D8E8``). The launch is simulated as ``Fighter_procUpdate`` moves a
fighter: knockback velocity starts at knockback x 0.03 and loses 0.051 a frame
along its direction in the air, or the target's traction on the ground; gravity
pulls the fighter's own velocity down to its fall speed; position gains both.

KO percents assume the target stands at the centre of Final Destination, holds
nothing (no DI, no SDI, no crouch cancel) and is KO'd if it crosses a blast zone
while it is still in hitstun or still carried by knockback.
"""
import math

KB_CAP = 2500.0
HITSTUN_PER_KB = 0.4
TUMBLE_HITSTUN = 32
LAUNCH_SPEED_PER_KB = 0.03
AIR_DECAY = 0.051
SAKURAI = 361

# Final Destination: blast zones and the stage's edges.
FINAL_DESTINATION = {"left": -246.0, "right": 246.0, "top": 188.0, "bottom": -140.0, "edge": 85.5657}


def knockback(damage, percent, weight, kbg, bkb, wdsk=0):
    """Knockback of a hit dealing ``damage`` to a target at ``percent`` (before the hit)."""
    weight_factor = 2.0 - (2.0 * weight * 0.01) / (1.0 + weight * 0.01)   # = 200 / (100 + weight)
    if wdsk:
        inner = 10 * 0.1 + 0.05 * (10 * wdsk)
    else:
        p = int(percent) + damage
        inner = 0.1 * p + 0.05 * (damage * p)
    kb = 0.01 * kbg * (1.4 * (weight_factor * inner) + 18) + bkb
    return min(kb, KB_CAP)


def hitstun(kb):
    return int(kb * HITSTUN_PER_KB)


def tumbles(kb):
    return kb * HITSTUN_PER_KB >= TUMBLE_HITSTUN


def launch_angle(angle, kb, grounded=True):
    """The launch angle in degrees; resolves the 361 (Sakurai) angle."""
    if angle != SAKURAI:
        return float(angle)
    if not grounded:
        return 45.0
    if kb < 32.0:
        return 0.0
    return min(44.0, 44.0 * ((kb - 32.0) / (32.1 - 32.0)) + 1)


def flight(kb, angle, target, stage=FINAL_DESTINATION, x=0.0, y=0.0, grounded=True, frames=600, stick=None):
    """Where a launched target goes. ``target`` needs gravity, fall_speed and ground_friction.
    ``stick`` (x, y), held as hitlag ends, applies ASDI and DI.

    Returns {ko: "left"/"right"/"top"/"bottom"/None, frame, hitstun, path: [(x, y), ...]}.
    """
    a = math.radians(launch_angle(angle, kb, grounded))
    speed = kb * LAUNCH_SPEED_PER_KB
    kx, ky = speed * math.cos(a), speed * math.sin(a)
    if stick:
        x, y = asdi(x, y, *stick)
        kx, ky = apply_di(kx, ky, *stick)
    if abs(ky) < 1e-6:
        ky = 0.0
    on_ground = grounded and ky <= 0
    if on_ground:
        ky = 0.0
    stun = kb * HITSTUN_PER_KB
    gravity, fall, traction = target.get("gravity", 0.1), target.get("fall_speed", 2.0), target.get("ground_friction", 0.06)
    vy = 0.0
    path = [(x, y)]
    for f in range(1, frames + 1):
        if on_ground:
            if kx:
                s = abs(kx) - traction
                kx = math.copysign(s, kx) if s > 0 else 0.0
            x += kx
            if abs(x) > stage["edge"]:
                on_ground = False   # slid off: from here it falls
        else:
            if kx or ky:
                mag = math.hypot(kx, ky)
                if mag < AIR_DECAY:
                    kx = ky = 0.0
                else:
                    kx -= AIR_DECAY * kx / mag
                    ky -= AIR_DECAY * ky / mag
            vy = max(vy - gravity, -fall)
            x += kx
            y += ky + vy
            if y < 0 and abs(x) <= stage["edge"] and path[-1][1] >= 0:
                y = 0.0   # came back down onto the stage
                if ky < 0:
                    ky = 0.0
                vy = 0.0
                on_ground = True
        path.append((x, y))
        carried = f < stun or kx or ky
        side = ("left" if x < stage["left"] else "right" if x > stage["right"] else
                "top" if y > stage["top"] else "bottom" if y < stage["bottom"] else None)
        if side:
            return {"ko": side if carried else None, "frame": f, "hitstun": int(stun), "path": path}
        if not carried and (on_ground or vy <= -fall + 1e-9 and y > 0):
            break
    return {"ko": None, "frame": None, "hitstun": int(stun), "path": path}


def ko_percent(hit, target, stage=FINAL_DESTINATION, limit=999):
    """Lowest percent (before the hit) at which ``hit`` KOs ``target`` from the centre of the stage.

    ``hit``: damage, angle, knockback_growth, base_knockback, weight_set_knockback.
    ``target``: weight, gravity, fall_speed, ground_friction. None if it never KOs by ``limit``.
    """
    def kos(p):
        kb = knockback(hit["damage"], p, target["weight"], hit["knockback_growth"], hit["base_knockback"],
                       hit.get("weight_set_knockback", 0))
        return flight(kb, hit["angle"], target, stage)["ko"] is not None
    if hit.get("weight_set_knockback"):
        return 0 if kos(0) else None   # set knockback ignores percent
    if not kos(limit):
        return None
    lo, hi = 0, limit
    if kos(lo):
        return 0
    while hi - lo > 1:   # knockback only grows with percent
        mid = (lo + hi) // 2
        if kos(mid):
            hi = mid
        else:
            lo = mid
    return hi


def tumble_percent(hit, target, limit=999):
    """Lowest percent (before the hit) at which ``hit`` puts ``target`` in tumble, or None."""
    kb = lambda p: knockback(hit["damage"], p, target["weight"], hit["knockback_growth"], hit["base_knockback"],
                             hit.get("weight_set_knockback", 0))
    for p in range(0, limit + 1):
        if tumbles(kb(p)):
            return p
        if hit.get("weight_set_knockback"):
            return None
    return None


HIT_KEYS = ("damage", "angle", "knockback_growth", "base_knockback", "weight_set_knockback")


def strongest_hit(hitboxes):
    """The hitbox that launches hardest (knockback on a 100-weight target at 100%), as a plain dict."""
    best = None; best_kb = -1.0
    for b in hitboxes:
        if b.get("damage", 0) <= 0:
            continue
        kb = knockback(b["damage"], 100, 100.0, b["knockback_growth"], b["base_knockback"], b.get("weight_set_knockback", 0))
        if kb > best_kb:
            best, best_kb = b, kb
    return {k: best.get(k, 0) for k in HIT_KEYS} if best else None


def tune_hit(hit, tuning):
    """``hit`` with a move's hitbox tuning applied, as the build applies it (``fighter_moves._tune_script``)."""
    if not hit or not tuning:
        return hit
    out = dict(hit)
    if tuning.get("damage") is not None:
        out["damage"] = max(1, round(float(tuning["damage"])))
    elif tuning.get("damage_scale") is not None:
        out["damage"] = max(1, round(hit["damage"] * float(tuning["damage_scale"])))
    for key in ("angle", "knockback_growth", "base_knockback", "weight_set_knockback"):
        if tuning.get(key) is not None:
            out[key] = int(tuning[key])
    kb_scale = float(tuning.get("knockback_scale", 1.0))
    if kb_scale != 1.0:
        for key in ("knockback_growth", "base_knockback"):
            out[key] = max(0, min(511, round(out[key] * kb_scale)))
    return out


DI_DEGREES = 18.0
ASDI_MIN = 0.7
ASDI_DISTANCE = 3.0


def apply_di(kx, ky, sx, sy):
    """Launch velocity after DI with the stick at (sx, sy) (``ftCo_8008E5A4``): the launch turns by
    18 degrees x (the stick's part across it)^2, towards the stick's side."""
    if not (sx or sy):
        return kx, ky
    mag2 = kx * kx + ky * ky
    if mag2 < 0.00001:
        return kx, ky
    across = ky * sx - kx * sy
    turn = across * across / mag2
    if kx * sy - ky * sx < 0:
        turn = -turn
    a = math.atan2(ky, kx) + math.radians(DI_DEGREES) * turn
    m = math.sqrt(mag2)
    return m * math.cos(a), m * math.sin(a)


def asdi(x, y, sx, sy):
    """Position after automatic smash DI when hitlag ends (``ftCo_Damage_OnExitHitlag``)."""
    if sx * sx + sy * sy >= ASDI_MIN * ASDI_MIN:
        return x + sx * ASDI_DISTANCE, y + sy * ASDI_DISTANCE
    return x, y
