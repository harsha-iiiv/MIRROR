// Camera glides: one eased move that settles. Orbit about the target in z-up spherical
// coordinates (log-space radius), sine in-out timing. No zoom punches, no shake.
import * as THREE from 'three';
import { prefersReducedMotion } from './look.js';

export const easeInOutSine = (x) => -(Math.cos(Math.PI * x) - 1) / 2;

function toSph(v) { const r = v.length(); return { r, pol: Math.acos(Math.max(-1, Math.min(1, v.z / (r || 1)))), az: Math.atan2(v.y, v.x) }; }
function fromSph(s) { const sp = Math.sin(s.pol); return new THREE.Vector3(s.r * sp * Math.cos(s.az), s.r * sp * Math.sin(s.az), s.r * Math.cos(s.pol)); }

export class Glide {
  constructor(camera, controls) { this.camera = camera; this.controls = controls; this.active = null; }

  to(target, position, { duration = 0.85, fov = null } = {}) {
    const c = this.camera, ctl = this.controls;
    if (prefersReducedMotion() || duration <= 0) {
      ctl.target.copy(target); c.position.copy(position);
      if (fov) { c.fov = fov; c.updateProjectionMatrix(); }
      this.active = null; ctl.update(); return;
    }
    const t0 = ctl.target.clone(), t1 = target.clone();
    const s0 = toSph(c.position.clone().sub(t0)), s1 = toSph(position.clone().sub(t1));
    let daz = s1.az - s0.az;
    if (daz > Math.PI) daz -= 2 * Math.PI; if (daz < -Math.PI) daz += 2 * Math.PI;
    this.active = { t: 0, duration, t0, t1, s0, s1, daz, f0: c.fov, f1: fov ?? c.fov };
  }

  step(dt) {
    const g = this.active;
    if (!g) return false;
    g.t = Math.min(1, g.t + dt / g.duration);
    const k = easeInOutSine(g.t);
    const tgt = g.t0.clone().lerp(g.t1, k);
    const r = Math.exp(Math.log(Math.max(1e-6, g.s0.r)) * (1 - k) + Math.log(Math.max(1e-6, g.s1.r)) * k);
    const off = fromSph({ r, pol: g.s0.pol + (g.s1.pol - g.s0.pol) * k, az: g.s0.az + g.daz * k });
    this.controls.target.copy(tgt);
    this.camera.position.copy(tgt).add(off);
    if (g.f0 !== g.f1) { this.camera.fov = g.f0 + (g.f1 - g.f0) * k; this.camera.updateProjectionMatrix(); }
    if (g.t >= 1) this.active = null;
    return true;
  }

  cancel() { this.active = null; }
}
