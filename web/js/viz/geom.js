// Geometry shared with the checker (mirror/evaluate/geom.py), ported line for line so the
// numbers the viewer shows on hover are the checker's definitions applied to a playback frame.
// Pure functions, no three.js: tests run this file under node against the Python originals.

export function quatToMat(q) {            // (w,x,y,z) -> row-major 3x3
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  const w = q[0] / n, x = q[1] / n, y = q[2] / n, z = q[3] / n;
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
    [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
    [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
  ];
}

export function quatRotate(q, v) { const R = quatToMat(q); return mul(R, v); }
const mul = (R, v) => [R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2], R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2], R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2]];
const mulT = (R, v) => [R[0][0] * v[0] + R[1][0] * v[1] + R[2][0] * v[2], R[0][1] * v[0] + R[1][1] * v[1] + R[2][1] * v[2], R[0][2] * v[0] + R[1][2] * v[1] + R[2][2] * v[2]];

// Tilt in degrees of a (w,x,y,z) quaternion: angle between the body z axis and world z.
export function tiltOf(q) {
  const [, x, y] = q;
  const n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3] || 1;
  const zz = 1 - 2 * (x * x + y * y) / n2;
  return (Math.acos(Math.max(-1, Math.min(1, zz))) * 180) / Math.PI;
}

// Obstacle -> solid boxes {owner, center, half, quat} (walls minus their slots), as obstacle_boxes().
export function obstacleBoxes(obs) {
  const q = obs.pose.quat, c = obs.pose.pos;
  if (obs.kind === 'box') return [{ owner: obs.id, center: [...c], half: [...obs.half], quat: [...q] }];
  const L = +obs.length, H = +obs.height, T = +obs.thickness;
  const pieces = [];
  const slots = [...(obs.slots || [])].sort((a, b) => a.y_min - b.y_min);
  let yc = -L / 2;
  for (const s of slots) {
    const y0 = Math.max(s.y_min, -L / 2), y1 = Math.min(s.y_max, L / 2);
    if (y0 > yc) pieces.push([yc, y0, 0, H]);
    if (s.z_min > 0) pieces.push([y0, y1, 0, s.z_min]);
    if (s.z_max < H) pieces.push([y0, y1, s.z_max, H]);
    yc = Math.max(yc, y1);
  }
  if (yc < L / 2) pieces.push([yc, L / 2, 0, H]);
  return pieces.map(([y0, y1, z0, z1]) => {
    const w = quatRotate(q, [0, (y0 + y1) / 2, (z0 + z1) / 2]);
    return { owner: obs.id, center: [c[0] + w[0], c[1] + w[1], c[2] + w[2]], half: [T / 2, (y1 - y0) / 2, (z1 - z0) / 2], quat: [...q] };
  });
}

export function pointBoxDistance(p, box) {
  const R = quatToMat(box.quat);
  const l = mulT(R, [p[0] - box.center[0], p[1] - box.center[1], p[2] - box.center[2]]);
  const d = l.map((v, i) => Math.max(Math.abs(v) - box.half[i], 0));
  return Math.hypot(d[0], d[1], d[2]);
}

const clip = (v, h) => v.map((x, i) => Math.max(-h[i], Math.min(h[i], x)));

// Distance between solid oriented boxes A and B, with the closest points (accelerated
// projected gradient, step 1/4, as box_box_distance()).
export function boxBoxClosest(cA, RA, hA, cB, RB, hB, iters = 400) {
  const d0 = [cA[0] - cB[0], cA[1] - cB[1], cA[2] - cB[2]];
  let a = clip(mulT(RA, d0).map((v) => -v), hA);
  let b = clip(mulT(RB, d0), hB);
  let ya = a.slice(), yb = b.slice(), tk = 1;
  const step = 0.25;
  for (let it = 0; it < iters; it++) {
    const ra = mul(RA, ya), rb = mul(RB, yb);
    const d = [d0[0] + ra[0] - rb[0], d0[1] + ra[1] - rb[1], d0[2] + ra[2] - rb[2]];
    const ga = mulT(RA, d), gb = mulT(RB, d).map((v) => -v);
    const a1 = clip(ya.map((v, i) => v - step * 2 * ga[i]), hA);
    const b1 = clip(yb.map((v, i) => v - step * 2 * gb[i]), hB);
    const t1 = 0.5 * (1 + Math.sqrt(1 + 4 * tk * tk));
    ya = a1.map((v, i) => v + ((tk - 1) / t1) * (v - a[i]));
    yb = b1.map((v, i) => v + ((tk - 1) / t1) * (v - b[i]));
    a = a1; b = b1; tk = t1;
  }
  const ra = mul(RA, a), rb = mul(RB, b);
  const pA = [cA[0] + ra[0], cA[1] + ra[1], cA[2] + ra[2]];
  const pB = [cB[0] + rb[0], cB[1] + rb[1], cB[2] + rb[2]];
  return { d: Math.hypot(pA[0] - pB[0], pA[1] - pB[1], pA[2] - pB[2]), pA, pB };
}

export function objectHalfExtents(obj) {
  if (obj.kind === 'cup') return [...obj.size.outer_half];
  if (obj.kind === 'block') return [...obj.size.half];
  const r = +obj.size.radius, l = +obj.size.half_length;
  return [r, r, l + r];
}

// (core half extents, radius): object = core box swept by a ball (marker: segment + radius).
export function objectCore(obj) {
  if (obj.kind === 'marker') return [[0, 0, +obj.size.half_length], +obj.size.radius];
  return [objectHalfExtents(obj), 0];
}

// Exact distance from the object's solid shape at (pos, quat) to the nearest box, with the
// closest pair of points (on the object's surface and on the obstacle) and the box owner.
export function objectObstacleClosest(obj, pos, quat, boxes) {
  if (!boxes.length) return null;
  const [core, radius] = objectCore(obj);
  const RA = quatToMat(quat);
  let best = null;
  for (const bx of boxes) {
    const r = boxBoxClosest(pos, RA, core, bx.center, quatToMat(bx.quat), bx.half);
    const d = r.d - radius;
    if (!best || d < best.d) {
      let pA = r.pA;
      if (radius > 0 && r.d > 1e-9) {   // move from the core to the swept surface
        const u = [(r.pB[0] - r.pA[0]) / r.d, (r.pB[1] - r.pA[1]) / r.d, (r.pB[2] - r.pA[2]) / r.d];
        pA = [r.pA[0] + u[0] * radius, r.pA[1] + u[1] * radius, r.pA[2] + u[2] * radius];
      }
      best = { d, pA, pB: r.pB, owner: bx.owner };
    }
  }
  best.d = Math.max(0, best.d);
  return best;
}

export function sceneBoxes(scene) { return (scene?.obstacles || []).flatMap(obstacleBoxes); }
