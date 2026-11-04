// Trajectory ribbons: a lit tube along an object's recorded path, coloured per playback frame
// by the checker's verdict, revealed up to the playhead with the remainder drawn faintly.
import * as THREE from 'three';

// Polyline tube with parallel-transport frames (no spline smoothing: the path is the data).
export function polyTube(points, colors, radius = 0.002, sides = 8) {
  const n = points.length;
  const pos = new Float32Array(n * sides * 3), nor = new Float32Array(n * sides * 3), col = new Float32Array(n * sides * 3);
  const T = new THREE.Vector3(), N = new THREE.Vector3(), B = new THREE.Vector3(), prevT = new THREE.Vector3();
  const tmp = new THREE.Vector3(), q = new THREE.Quaternion();
  for (let i = 0; i < n; i++) {
    const a = points[Math.max(0, i - 1)], b = points[Math.min(n - 1, i + 1)];
    T.subVectors(b, a); if (T.lengthSq() < 1e-14) T.copy(prevT.lengthSq() ? prevT : new THREE.Vector3(1, 0, 0)); T.normalize();
    if (i === 0) {
      tmp.set(0, 0, 1); if (Math.abs(T.dot(tmp)) > 0.9) tmp.set(1, 0, 0);
      N.crossVectors(T, tmp).normalize();
    } else {
      q.setFromUnitVectors(prevT, T); N.applyQuaternion(q).normalize();
    }
    B.crossVectors(T, N).normalize();
    prevT.copy(T);
    const p = points[i], c = colors[i];
    for (let s = 0; s < sides; s++) {
      const ang = (s / sides) * Math.PI * 2, cs = Math.cos(ang), sn = Math.sin(ang);
      const k = (i * sides + s) * 3;
      const nx = N.x * cs + B.x * sn, ny = N.y * cs + B.y * sn, nz = N.z * cs + B.z * sn;
      pos[k] = p.x + nx * radius; pos[k + 1] = p.y + ny * radius; pos[k + 2] = p.z + nz * radius;
      nor[k] = nx; nor[k + 1] = ny; nor[k + 2] = nz;
      col[k] = c.r; col[k + 1] = c.g; col[k + 2] = c.b;
    }
  }
  const idx = [];
  for (let i = 0; i < n - 1; i++) for (let s = 0; s < sides; s++) {
    const a = i * sides + s, b = i * sides + ((s + 1) % sides), c2 = a + sides, d = b + sides;
    idx.push(a, c2, b, b, c2, d);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.setIndex(idx);
  g.userData.sides = sides;
  return g;
}

// Build a ribbon for one body. `colorAt(frameIndex)` returns a THREE.Color.
// Returns { group, frames, setFrame(i) } where frames maps ribbon points to payload frames.
export function buildRibbon(payload, body, colorAt, { radius = 0.0021, futureOpacity = 0.16, minStep = 0.0015 } = {}) {
  const arr = payload?.bodies?.[body];
  if (!arr) return null;
  const pts = [], cols = [], frames = [];
  let prev = null;
  for (let i = 0; i < arr.length; i++) {
    const s = arr[i];
    if (!s) continue;
    if (prev && Math.hypot(s[0] - prev[0], s[1] - prev[1], s[2] - prev[2]) < minStep && i !== arr.length - 1) continue;
    pts.push(new THREE.Vector3(s[0], s[1], s[2]));
    cols.push(colorAt(i).clone());
    frames.push(i);
    prev = s;
  }
  if (pts.length < 2) return null;
  const geo = polyTube(pts, cols, radius);
  const solid = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.4, metalness: 0.05 }));
  solid.renderOrder = 2;
  solid.castShadow = false;
  const future = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: futureOpacity, depthWrite: false }));
  future.renderOrder = 1;
  const group = new THREE.Group();
  group.add(future, solid);
  group.name = `ribbon:${body}`;
  const per = geo.userData.sides * 6;
  const setFrame = (fi) => {
    let lo = 0, hi = frames.length;            // number of ribbon points with frame <= fi
    while (lo < hi) { const m = (lo + hi) >> 1; if (frames[m] <= fi) lo = m + 1; else hi = m; }
    const k = Math.max(0, lo - 1);
    solid.userData.count = k * per;
  };
  // Separate draw ranges for the two meshes sharing one geometry.
  solid.onBeforeRender = (r, s, c, g) => { g.setDrawRange(0, solid.userData.count ?? Infinity); };
  future.onBeforeRender = (r, s, c, g) => { g.setDrawRange(0, Infinity); };
  return { group, frames, setFrame, points: pts };
}
