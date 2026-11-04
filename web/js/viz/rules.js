// Rule geometry: what the specification asks, drawn where it applies.
//   keep_clearance  -> the forbidden region around each obstacle box: the box grown by min_m
//                      (a rounded box, i.e. box ⊕ ball, exactly the set the object must not enter)
//   upright         -> a cone of half-angle max_tilt_deg about world z on the carried object, with
//                      the object's own z axis as a needle (needle outside the cone = over the limit)
//   inside (goal)   -> the tray's inner footprint as a goal region
//   violations      -> markers where the checker's intervals peak (pulse while the playhead is inside)
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { sceneBoxes, obstacleBoxes, objectObstacleClosest, tiltOf, quatToMat } from './geom.js';
import { cssVar, prefersReducedMotion } from './look.js';
import { intervalsFor, carrying } from './verdict.js';

const UP = new THREE.Vector3(0, 0, 1);

function selects(sel, obj) {
  if (!sel) return true;
  if (sel.ids && sel.ids.length) return sel.ids.includes(obj.id);
  if (sel.kind && sel.kind !== obj.kind) return false;
  return true;
}

function label(text, cls = '') {
  const div = document.createElement('div');
  div.className = 'label3d viz-rule ' + cls;
  const span = document.createElement('span');
  span.textContent = text;
  div.appendChild(span);
  const o = new CSS2DObject(div);
  o.userData.isLabel = true;
  return o;
}

// Rules drawn: confirmed ones are enforced by the checker; proposed ones are drawn muted and
// labelled "proposed, not enforced" (the checker reports them as advisory).
export function enforcedConstraints(spec) {
  return (spec?.constraints || []).filter((c) => c.status !== 'rejected');
}
export const isEnforced = (c) => !c.status || c.status === 'confirmed';

export class RuleLayer {
  constructor() {
    this.group = new THREE.Group();
    this.group.name = 'rules';
    this.cones = [];
    this.markers = [];
    this.volumes = [];
    this.closest = null;
  }

  clear() {
    for (const c of [...this.group.children]) {
      this.group.remove(c);
      c.traverse?.((o) => { o.geometry?.dispose?.(); if (o.isCSS2DObject) o.element.remove(); });
    }
    this.cones = []; this.markers = []; this.volumes = []; this.closest = null;
  }

  build({ scene, spec, report, payload, tol = 15, compact = false }) {
    this.clear();
    this.scene = scene; this.payload = payload; this.report = report;
    if (!scene) return;
    const accent = new THREE.Color(cssVar('--accent', '#2f54d9'));
    const bad = new THREE.Color(cssVar('--bad', '#c8372d'));
    const warn = new THREE.Color(cssVar('--warn', '#b7791f'));
    this.colors = { accent, bad, warn, muted: new THREE.Color(cssVar('--muted', '#6b6b70')) };
    const cons = enforcedConstraints(spec);
    this.constraints = cons;

    // Goal regions: tray inner footprint (inside goals).
    for (const t of scene.trays || []) {
      const [hx, hy] = t.inner_half;
      const hz = Math.max(0.01, (t.wall_height || 0.03) / 2);
      const geo = new THREE.BoxGeometry(2 * hx, 2 * hy, 2 * hz);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.06, depthWrite: false }));
      m.position.set(t.pose.pos[0], t.pose.pos[1], (t.pose.pos[2] || 0) + hz + 0.0015);
      m.quaternion.set(t.pose.quat[1], t.pose.quat[2], t.pose.quat[3], t.pose.quat[0]);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineDashedMaterial({ color: accent, dashSize: 0.008, gapSize: 0.005, transparent: true, opacity: 0.85 }));
      e.computeLineDistances();
      m.add(e);
      const lab = label(compact ? `goal · ${t.id}` : `goal · inside ${t.id} · ${(2 * hx * 100).toFixed(1)} × ${(2 * hy * 100).toFixed(1)} cm`, 'goal');
      lab.position.set(0, -hy - 0.012, hz);
      m.add(lab);
      m.userData.ruleKind = 'goal';
      this.group.add(m);
    }

    // Clearance: obstacle boxes grown by min_m (exact forbidden region for the object's surface).
    const clearance = cons.filter((c) => c.type === 'keep_clearance');
    const boxes = sceneBoxes(scene);
    this.boxes = boxes;
    for (const c of clearance) {
      const r = +(c.params?.min_m || 0);
      if (!(r > 0)) continue;
      for (const bx of boxes) {
        const geo = new RoundedBoxGeometry(2 * (bx.half[0] + r), 2 * (bx.half[1] + r), 2 * (bx.half[2] + r), 4, r);
        const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: warn, transparent: true, opacity: 0.07, depthWrite: false, roughness: 0.9, side: THREE.DoubleSide }));
        m.position.set(...bx.center);
        m.quaternion.set(bx.quat[1], bx.quat[2], bx.quat[3], bx.quat[0]);
        m.renderOrder = 3;
        const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), new THREE.LineBasicMaterial({ color: warn, transparent: true, opacity: 0.35 }));
        m.add(e);
        m.userData = { ruleKind: 'clearance', constraint: c.id, min_m: r, box: bx };
        this.volumes.push(m);
        this.group.add(m);
      }
      if (boxes.length) {
        const top = boxes.reduce((a, b) => (b.center[2] + b.half[2] > a.center[2] + a.half[2] ? b : a));
        const lab = label(compact ? `clearance ≥ ${(r * 100).toFixed(1)} cm` : `${c.id} · clearance ≥ ${(r * 100).toFixed(1)} cm`, 'clear');
        lab.position.set(top.center[0], top.center[1], top.center[2] + top.half[2] + r + 0.015);
        this.group.add(lab);
      }
    }

    // Upright cones on every object an upright rule applies to.
    for (const c of cons.filter((x) => x.type === 'upright')) {
      const deg = +(c.params?.max_tilt_deg ?? tol);
      for (const o of (scene.objects || []).filter((ob) => selects(c.applies_to, ob))) {
        if (this.cones.some((k) => k.object === o.id && k.phase === c.phase && k.deg === deg)) continue;
        const enforced = isEnforced(c);
        const h = o.kind === 'cup' ? o.size.outer_half[2] : o.kind === 'block' ? o.size.half[2] : (o.size.half_length || 0.05);
        const H = Math.max(0.06, h * 2.2);
        const rad = Math.tan((deg * Math.PI) / 180) * H;
        const coneGeo = new THREE.ConeGeometry(rad, H, 48, 1, true);
        coneGeo.translate(0, -H / 2, 0); coneGeo.rotateX(-Math.PI / 2);   // apex at origin, opening along +z
        const cone = new THREE.Mesh(coneGeo, new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.13, depthWrite: false, side: THREE.DoubleSide }));
        const rim = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
          Array.from({ length: 64 }, (_, i) => new THREE.Vector3(rad * Math.cos((i / 64) * 2 * Math.PI), rad * Math.sin((i / 64) * 2 * Math.PI), H))),
          new THREE.LineBasicMaterial({ color: accent, transparent: true, opacity: 0.7 }));
        const needle = new THREE.ArrowHelper(UP, new THREE.Vector3(), H * 1.3, accent.getHex(), 0.014, 0.008);
        needle.traverse((n) => { if (n.material) { n.material.depthTest = false; n.material.transparent = true; } n.renderOrder = 8; });
        const grp = new THREE.Group();
        grp.add(cone, rim, needle);
        grp.userData.ruleKind = 'upright';
        const lab = label('', 'tilt');
        lab.position.set(0, 0, H + 0.012);
        grp.add(lab);
        grp.visible = false;
        this.group.add(grp);
        this.cones.push({ grp, cone, rim, needle, lab, object: o.id, phase: c.phase, deg, id: c.id, H, enforced });
      }
    }

    // Violation markers at the frame where each interval peaks.
    for (const v of intervalsFor(report, null)) {
      const at = this._peakFrame(v);
      if (!at) continue;
      const col = v.kind === 'advisory' ? warn : bad;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.016, 0.0016, 8, 48), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.95, depthTest: false }));
      ring.position.copy(at.pos);
      ring.renderOrder = 6;
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.0032, 12, 8), new THREE.MeshBasicMaterial({ color: col, depthTest: false }));
      dot.position.copy(at.pos); dot.renderOrder = 6;
      const what = v.constraint_id + (v.kind === 'advisory' ? ' (advisory: rule not enforced in this version)' : '');
      const val = /clear/i.test(v.constraint_id) || v.kind === 'contact' ? `${(v.peak * 100).toFixed(1)} cm` : `${v.peak.toFixed(1)}°`;
      const lab = label(compact ? `${v.kind === 'advisory' ? 'advisory' : 'violated'} · peak ${val}` : `${what} · peak ${val} · ${v.t_start.toFixed(2)}–${v.t_end.toFixed(2)} s`, v.kind === 'advisory' ? 'warn' : 'bad');
      lab.position.copy(at.pos).add(new THREE.Vector3(0, 0, 0.03));
      lab.userData.isViolationLabel = true;
      this.group.add(ring, dot, lab);
      this.markers.push({ ring, dot, lab, v, frame: at.frame });
    }
  }

  _peakFrame(v) {
    const p = this.payload;
    const arr = p?.bodies?.[v.object_id];
    if (!arr || !p.t?.length) return null;
    const obj = (this.scene?.objects || []).find((o) => o.id === v.object_id);
    const boxes = this.boxes || sceneBoxes(this.scene);
    let best = null;
    for (let i = 0; i < p.t.length; i++) {
      if (p.t[i] < v.t_start - 1e-6 || p.t[i] > v.t_end + 1e-6 || !arr[i]) continue;
      const s = arr[i];
      let score, pos = new THREE.Vector3(s[0], s[1], s[2]);
      if (/clear/i.test(v.constraint_id) || v.kind === 'contact') {
        if (!obj || !boxes.length) { score = 0; } else {
          const r = objectObstacleClosest(obj, s.slice(0, 3), s.slice(3, 7), boxes);
          score = -r.d; pos = new THREE.Vector3(...r.pA);
        }
      } else score = tiltOf(s.slice(3, 7));
      if (!best || score > best.score) best = { score, frame: i, pos };
    }
    if (!best) {   // interval shorter than a playback frame: take the nearest frame
      let bi = 0;
      for (let i = 0; i < p.t.length; i++) if (Math.abs(p.t[i] - v.t_start) < Math.abs(p.t[bi] - v.t_start)) bi = i;
      const s = arr[bi];
      if (!s) return null;
      best = { frame: bi, pos: new THREE.Vector3(s[0], s[1], s[2]) };
    }
    return best;
  }

  // Per-frame update: cone placement and colour, clearance tint, marker pulse.
  update(t, frame, bodies, now) {
    const p = this.payload;
    const reduce = prefersReducedMotion();
    let animating = false;
    for (const k of this.cones) {
      const g = bodies.get(k.object);
      const ph = p?.phase?.[frame];
      const show = !!g && (k.phase === 'always' || (k.phase === 'transport' && carrying(ph, k.object)) ||
        (k.phase === 'final' && p && t >= (p.t[p.t.length - 1] - 1e-6)) || (!p && k.phase !== 'transport'));
      k.grp.visible = show;
      if (!show) continue;
      k.grp.position.copy(g.position);
      const z = new THREE.Vector3(0, 0, 1).applyQuaternion(g.quaternion);
      k.needle.setDirection(z);
      const tilt = (Math.acos(Math.max(-1, Math.min(1, z.z))) * 180) / Math.PI;
      const over = tilt > k.deg + 1e-9;
      const col = !k.enforced ? (over ? this.colors.warn : this.colors.muted) : over ? this.colors.bad : this.colors.accent;
      k.cone.material.color.copy(col); k.rim.material.color.copy(col); k.needle.setColor(col);
      k.cone.material.opacity = over ? 0.2 : 0.12;
      k.lab.element.firstChild.textContent = `tilt ${tilt.toFixed(1)}° / ${k.deg.toFixed(0)}°` + (k.enforced ? '' : ' · proposed, not enforced');
      k.lab.element.classList.toggle('warn', over && !k.enforced);
      k.lab.element.classList.toggle('bad', over && k.enforced);
    }
    for (const m of this.markers) {
      const inside = t >= m.v.t_start && t <= m.v.t_end;
      const active = inside && !reduce;
      const s = active ? 1 + 0.35 * (0.5 + 0.5 * Math.sin(now / 160)) : 1;
      m.ring.scale.setScalar(s);
      m.ring.material.opacity = inside ? 1 : 0.55;
      m.lab.visible = inside || this.showAllMarkers;
      animating = animating || active;
    }
    return animating;
  }

  // Closest approach from an object to the obstacles at its current pose (for the measure line).
  closestFor(objId, grp) {
    const obj = (this.scene?.objects || []).find((o) => o.id === objId);
    if (!obj || !grp || !(this.boxes || []).length) return null;
    const q = grp.quaternion;
    return objectObstacleClosest(obj, [grp.position.x, grp.position.y, grp.position.z], [q.w, q.x, q.y, q.z], this.boxes);
  }
}

export { quatToMat, obstacleBoxes };
