// Knockback and KO percents, the way Melee computes them. A mirror of knockback.py (see there for
// the sources); tests/test_knockback.py checks both against a launch recorded in game.

export const FINAL_DESTINATION = { left: -246, right: 246, top: 188, bottom: -140, edge: 85.5657 };

export function knockback(damage, percent, weight, kbg, bkb, wdsk = 0) {
  const wf = 2 - (2 * weight * 0.01) / (1 + weight * 0.01);
  let inner;
  if (wdsk) inner = 10 * 0.1 + 0.05 * (10 * wdsk);
  else { const p = Math.trunc(percent) + damage; inner = 0.1 * p + 0.05 * (damage * p); }
  return Math.min(0.01 * kbg * (1.4 * (wf * inner) + 18) + bkb, 2500);
}

export const tumbles = (kb) => kb * 0.4 >= 32;

export function launchAngle(angle, kb, grounded = true) {
  if (angle !== 361) return angle;
  if (!grounded) return 45;
  if (kb < 32) return 0;
  return Math.min(44, 44 * ((kb - 32) / (32.1 - 32)) + 1);
}

export function flight(kb, angle, target, stage = FINAL_DESTINATION, x = 0, y = 0, grounded = true, frames = 600) {
  const a = launchAngle(angle, kb, grounded) * Math.PI / 180, speed = kb * 0.03;
  let kx = speed * Math.cos(a), ky = speed * Math.sin(a);
  if (Math.abs(ky) < 1e-6) ky = 0;
  let onGround = grounded && ky <= 0;
  if (onGround) ky = 0;
  const stun = kb * 0.4, g = target.gravity ?? 0.1, fall = target.fall_speed ?? 2, traction = target.ground_friction ?? 0.06;
  let vy = 0, prevY = y;
  const path = [[x, y]];
  for (let f = 1; f <= frames; f++) {
    if (onGround) {
      if (kx) { const s = Math.abs(kx) - traction; kx = s > 0 ? Math.sign(kx) * s : 0; }
      x += kx;
      if (Math.abs(x) > stage.edge) onGround = false;
    } else {
      if (kx || ky) {
        const mag = Math.hypot(kx, ky);
        if (mag < 0.051) kx = ky = 0; else { kx -= 0.051 * kx / mag; ky -= 0.051 * ky / mag; }
      }
      vy = Math.max(vy - g, -fall);
      x += kx; y += ky + vy;
      if (y < 0 && Math.abs(x) <= stage.edge && prevY >= 0) { y = 0; if (ky < 0) ky = 0; vy = 0; onGround = true; }
    }
    path.push([x, y]); prevY = y;
    const carried = f < stun || kx || ky;
    const side = x < stage.left ? 'left' : x > stage.right ? 'right' : y > stage.top ? 'top' : y < stage.bottom ? 'bottom' : null;
    if (side) return { ko: carried ? side : null, frame: f, path };
    if (!carried && (onGround || (vy <= -fall + 1e-9 && y > 0))) break;
  }
  return { ko: null, frame: null, path };
}

const kbOf = (hit, p, t) => knockback(hit.damage, p, t.weight, hit.knockback_growth, hit.base_knockback, hit.weight_set_knockback || 0);

// Lowest percent (before the hit) at which `hit` KOs `target` from the centre of the stage, or null.
export function koPercent(hit, target, stage = FINAL_DESTINATION, limit = 999) {
  const kos = (p) => flight(kbOf(hit, p, target), hit.angle, target, stage).ko !== null;
  if (hit.weight_set_knockback) return kos(0) ? 0 : null;
  if (!kos(limit)) return null;
  if (kos(0)) return 0;
  let lo = 0, hi = limit;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (kos(mid)) hi = mid; else lo = mid; }
  return hi;
}

export function tumblePercent(hit, target, limit = 999) {
  for (let p = 0; p <= limit; p++) {
    if (tumbles(kbOf(hit, p, target))) return p;
    if (hit.weight_set_knockback) return null;
  }
  return null;
}

// The hitbox that launches hardest (a 100-weight target at 100%).
export function strongestHit(hitboxes) {
  let best = null, bestKb = -1;
  for (const b of hitboxes) {
    if (!(b.damage > 0)) continue;
    const kb = knockback(b.damage, 100, 100, b.knockback_growth, b.base_knockback, b.weight_set_knockback || 0);
    if (kb > bestKb) { best = b; bestKb = kb; }
  }
  return best;
}
