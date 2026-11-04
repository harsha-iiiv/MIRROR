// Synchronized ghost: the earlier behaviour's bodies, translucent, posed at the same playback
// time as the current run. Times are matched in seconds from each run's start (runs differ in
// length: past its end the ghost holds its last pose).
import * as THREE from 'three';
import { cssVar } from './look.js';

const tA = new THREE.Quaternion(), tB = new THREE.Quaternion();

function geomFor(g) {
  const s = g.size;
  let geo = null;
  switch (g.type) {
    case 'box': geo = new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]); break;
    case 'sphere': geo = new THREE.SphereGeometry(s[0], 12, 8); break;
    case 'cylinder': geo = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 20); geo.rotateX(Math.PI / 2); break;
    case 'capsule': geo = new THREE.CapsuleGeometry(s[0], 2 * s[1], 4, 12); geo.rotateX(Math.PI / 2); break;
    default: return null;
  }
  return geo;
}

export class GhostLayer {
  constructor() { this.group = new THREE.Group(); this.group.name = 'ghost-bodies'; this.bodies = new Map(); this.payload = null; }

  clear() {
    for (const c of [...this.group.children]) { this.group.remove(c); c.traverse((o) => o.geometry?.dispose?.()); }
    this.bodies.clear(); this.payload = null;
  }

  build(payload) {
    this.clear();
    if (!payload?.visual || !payload.t?.length) return;
    this.payload = payload;
    const ink = new THREE.Color(cssVar('--muted', '#6b6b70'));
    const fill = new THREE.MeshBasicMaterial({ color: ink, transparent: true, opacity: 0.14, depthWrite: false });
    const edge = new THREE.LineBasicMaterial({ color: ink, transparent: true, opacity: 0.55 });
    for (const b of payload.visual.bodies) {
      if (b.static || !payload.bodies[b.name]) continue;
      const grp = new THREE.Group();
      for (const g of b.geoms) {
        const geo = geomFor(g);
        if (!geo) continue;
        const m = new THREE.Mesh(geo, fill);
        m.position.set(...g.pos);
        m.quaternion.set(g.quat[1], g.quat[2], g.quat[3], g.quat[0]);
        m.renderOrder = 4;
        if (!/bead/i.test(b.name)) {
          const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo), edge);
          m.add(e);
        }
        grp.add(m);
      }
      grp.visible = false;
      this.group.add(grp);
      this.bodies.set(b.name, grp);
    }
  }

  setTime(t) {
    const p = this.payload;
    if (!p) return;
    const ts = p.t;
    let lo = 0, hi = ts.length - 1, a = 0;
    if (t <= ts[0]) hi = 0;
    else if (t >= ts[hi]) lo = hi;
    else { while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; } a = (t - ts[lo]) / Math.max(1e-9, ts[hi] - ts[lo]); }
    const i = lo, j = Math.min(lo + 1, ts.length - 1);
    for (const [name, grp] of this.bodies) {
      const arr = p.bodies[name];
      const A = arr[i], B = arr[j] || A;
      if (!A) continue;
      grp.visible = true;
      grp.position.set(A[0] + (B[0] - A[0]) * a, A[1] + (B[1] - A[1]) * a, A[2] + (B[2] - A[2]) * a);
      tA.set(A[4], A[5], A[6], A[3]); tB.set(B[4], B[5], B[6], B[3]);
      grp.quaternion.copy(tA).slerp(tB, a);
    }
  }
}
