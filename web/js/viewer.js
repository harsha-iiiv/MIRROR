// Three.js playback viewer for MuJoCo rollouts (VisualScene + columnar frames).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { tiltDeg } from './util.js';

const Z_UP = new THREE.Vector3(0, 0, 1);
const tmpA = new THREE.Quaternion(), tmpB = new THREE.Quaternion();

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function q4(q) { return new THREE.Quaternion(q[1], q[2], q[3], q[0]); }  // (w,x,y,z) -> three

export function tiltColor(tilt, tol, out = new THREE.Color()) {
  const accent = new THREE.Color(cssVar('--accent') || '#2f54d9');
  const warn = new THREE.Color(cssVar('--warn') || '#b7791f');
  const bad = new THREE.Color(cssVar('--bad') || '#c8372d');
  if (tilt <= tol) return out.copy(accent);
  if (tilt <= 45) return out.copy(warn).lerp(bad, (tilt - tol) / (45 - tol) * 0.35);
  return out.copy(warn).lerp(bad, 0.35 + 0.65 * Math.min(1, (tilt - 45) / 45));
}

function geomMesh(g, material) {
  let geo = null;
  const s = g.size;
  switch (g.type) {
    case 'box': geo = new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]); break;
    case 'sphere': geo = new THREE.SphereGeometry(s[0], 16, 12); break;
    case 'cylinder': geo = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 28); geo.rotateX(Math.PI / 2); break;
    case 'capsule': geo = new THREE.CapsuleGeometry(s[0], 2 * s[1], 6, 16); geo.rotateX(Math.PI / 2); break;
    case 'plane': geo = new THREE.PlaneGeometry(2 * (s[0] || 1), 2 * (s[1] || 1)); break;
    default: return null; // mesh geoms carry no vertex data in VisualScene
  }
  const m = new THREE.Mesh(geo, material);
  m.position.set(g.pos[0], g.pos[1], g.pos[2]);
  m.quaternion.copy(q4(g.quat));
  m.castShadow = g.type !== 'plane';
  m.receiveShadow = true;
  return m;
}

function wallLike(b, g, trayIds) { return b.static && !trayIds.has(b.name) && g.type === 'box' && 2 * g.size[2] > 0.15; }
function isBead(name) { return /bead/i.test(name); }
function isGripper(name) { return /gripper|palm|finger|hand|pad/i.test(name); }

export class Viewer {
  constructor(el, opts = {}) {
    this.el = el;
    this.tol = opts.tol ?? 15;
    this.onTick = opts.onTick || (() => {});
    this.onEdit = opts.onEdit || (() => {});
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });
    el.appendChild(this.labels.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
    this.camera.up.copy(Z_UP);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 0.35;
    this.controls.maxDistance = 4;
    this.controls.addEventListener('change', () => (this.dirty = true));
    this.controls.addEventListener('start', () => (this._userMoved = true));
    this._followPos = new THREE.Vector3();
    this.setView('3d');

    this.hemi = new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 1.1);
    this.scene.add(this.hemi);
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(-0.6, -0.8, 1.6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, { left: -0.8, right: 0.8, top: 0.8, bottom: -0.8, near: 0.1, far: 4 });
    sun.shadow.bias = -0.0004;
    this.scene.add(sun);

    this.world = new THREE.Group();   // static + dynamic bodies
    this.overlay = new THREE.Group(); // traces, ghosts
    this.scene.add(this.world, this.overlay);
    this.bodies = new Map();
    this.payload = null;
    this.ghost = null;
    this.t = 0;
    this.playing = false;
    this.speed = 1;
    this.showLabels = true;
    this.showTrace = true;
    this.showGhost = true;
    this.editMode = null;
    this.dirty = true;

    this.raycaster = new THREE.Raycaster();
    this.plane = new THREE.Plane(Z_UP, 0);
    this._bindPointer();
    this.applyTheme();

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(el);
    this.resize();
    this._last = performance.now();
    const loop = (now) => {
      this._raf = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - this._last) / 1000);
      this._last = now;
      if (this.playing && this.payload) {
        const end = this.duration();
        this.t = Math.min(end, this.t + dt * this.speed);
        if (this.t >= end) this.playing = false;
        this._applyTime();
        this.onTick(this.t, this.playing);
      }
      if (this.view === 'follow') this._follow(dt);
      if (this.controls.update() || this.dirty) {
        this._sizeBeads();
        this.renderer.render(this.scene, this.camera);
        this.labels.render(this.scene, this.camera);
        this._declutterLabels();
        this.dirty = false;
      }
    };
    this._raf = requestAnimationFrame(loop);
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this.ro.disconnect();
    this.renderer.dispose();
    this.el.innerHTML = '';
  }

  resize() {
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1;
    const aspect0 = this.camera.aspect;
    this.renderer.setSize(w, h, false);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const m of this._lineMats || []) m.resolution.set(w, h);
    // Re-frame for a new shape (phone rotation, chat pane) unless the visitor moved the camera.
    if (!this._userMoved && Math.abs(aspect0 - this.camera.aspect) > 0.05) this.setView(this.view);
    this.dirty = true;
  }

  // Portrait panes see less of the table at the same distance; back the camera off.
  _frameScale() {
    const a = this.camera.aspect || 1.6;
    return a >= 1.3 ? 1 : Math.min(2.1, Math.pow(1.3 / a, 0.85));
  }

  applyTheme() {
    this.scene.background = new THREE.Color(cssVar('--viewer') || '#eceae6');
    const dark = document.documentElement.dataset.theme === 'dark';
    this.hemi.intensity = dark ? 0.8 : 1.1;
    if (this.tableMat) this.tableMat.color.set(dark ? '#26262b' : '#f4f3f0');
    if (this.gridMat) this.gridMat.color.set(dark ? '#3a3a41' : '#d9d7d1');
    if (this.edgeMat) this.edgeMat.color.set(cssVar('--muted'));
    if (this.payload) this._buildTraces();
    this.dirty = true;
  }

  setView(kind) {
    this.view = kind;
    const k = this._frameScale();
    const put = (target, offset) => {
      this.controls.target.set(...target);
      this.camera.position.set(target[0] + offset[0] * k, target[1] + offset[1] * k, target[2] + offset[2] * k);
    };
    if (kind === 'top') put([0.02, 0, 0], [0, -0.04, 1.55]);
    else if (kind === 'side') put([0.0, 0, 0.12], [0, -1.35, 0.30]);
    else if (kind === 'follow') {
      // Close behind-and-above view that keeps the carried container in frame.
      const p = this._followTarget() || new THREE.Vector3(0.02, 0.02, 0.04);
      this._followPos.copy(p);
      put([p.x, p.y, p.z], [-0.30, -0.74, 0.58]);
    } else put([0.02, 0.02, 0.04], [-0.44, -1.14, 0.98]);
    this.controls.update();
    this.dirty = true;
  }

  // The followed body: the traced container during playback.
  _followTarget() {
    const g = this.traceObject && this.bodies.get(this.traceObject);
    if (!g || !g.visible || !this.payload) return null;
    return new THREE.Vector3(g.position.x, g.position.y, Math.max(0.03, g.position.z));
  }

  // Move target and camera together so orbit and zoom chosen by the visitor are kept.
  _follow(dt) {
    const p = this._followTarget();
    if (!p) return;
    const a = 1 - Math.exp(-dt * 6);
    const before = this._followPos.clone();
    this._followPos.lerp(p, a);
    const d = this._followPos.clone().sub(before);
    if (d.lengthSq() < 1e-12) return;
    this.controls.target.add(d);
    this.camera.position.add(d);
    this.dirty = true;
  }

  // ------------------------------------------------------------ build
  _clearWorld() {
    for (const c of [...this.world.children]) {
      this.world.remove(c);
      c.traverse?.((o) => { o.geometry?.dispose?.(); if (o.isCSS2DObject) o.element.remove(); });
    }
    this.bodies.clear();
  }

  _buildVisual(visual, sceneState) {
    this._clearWorld();
    if (!visual) return;
    const trayIds = new Set((sceneState?.trays || []).map((t) => t.id));
    const objIds = new Map((sceneState?.objects || []).map((o) => [o.id, o]));
    for (const b of visual.bodies) {
      const grp = new THREE.Group();
      grp.name = b.name;
      for (const g of b.geoms) {
        const isTable = b.static && (/table|floor|ground/i.test(b.name) || /table|floor|ground/i.test(g.name || ''));
        let mat;
        if (isTable) {
          this.tableMat = this.tableMat || new THREE.MeshStandardMaterial({ roughness: 0.95 });
          this.tableMat.color.set(document.documentElement.dataset.theme === 'dark' ? '#26262b' : '#f4f3f0');
          mat = this.tableMat;
        } else if (isBead(b.name)) {
          // Beads are a few millimetres across; draw them bright and never smaller than a few pixels.
          this.beadMat = this.beadMat || new THREE.MeshStandardMaterial({ color: '#ff2bd6', emissive: '#c2009f',
            emissiveIntensity: 0.9, roughness: 0.35 });
          mat = this.beadMat;
        } else {
          const [r, gg, bb, a0] = g.rgba;
          // Tall static walls are drawn translucent so the robot stays visible behind them.
          const wallLike = b.static && !trayIds.has(b.name) && g.type === 'box' && 2 * g.size[2] > 0.15;
          const a = wallLike ? Math.min(a0, 0.38) : a0;
          mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(r, gg, bb), roughness: 0.62, metalness: 0.02,
            transparent: a < 0.999, opacity: a, depthWrite: a >= 0.999 });
        }
        const m = geomMesh(g, mat);
        if (m) {
          grp.add(m);
          if (isBead(b.name)) { m.userData.beadR = Math.max(1e-4, g.size[0]); m.userData.basePos = m.position.clone(); }
          if (isTable) this._addGrid(grp);
          if (b.static && !isTable && g.type === 'box') {
            this.edgeMat = this.edgeMat || new THREE.LineBasicMaterial({ transparent: true, opacity: 0.55 });
            this.edgeMat.color.set(cssVar('--muted') || '#6b6b70');
            const e = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), this.edgeMat);
            e.position.copy(m.position); e.quaternion.copy(m.quaternion);
            grp.add(e);
          }
          if (wallLike(b, g, trayIds)) m.castShadow = false;
        }
      }
      if (trayIds.has(b.name)) grp.userData.editable = { kind: 'tray', id: b.name };
      if (objIds.has(b.name)) grp.userData.editable = { kind: 'object', id: b.name };
      if (this.showLabels && (trayIds.has(b.name) || objIds.has(b.name))) {
        const div = document.createElement('div');
        div.className = 'label3d';
        const span = document.createElement('span');
        span.textContent = objIds.has(b.name) ? objIds.get(b.name).kind : 'tray';
        div.appendChild(span);
        const lab = new CSS2DObject(div);
        const top = objIds.has(b.name) ? this._halfHeight(objIds.get(b.name)) + 0.012 : 0.05;
        lab.position.set(0, 0, top);
        if (trayIds.has(b.name)) {
          const t = sceneState.trays.find((x) => x.id === b.name);
          lab.position.set(t.pose.pos[0], t.pose.pos[1] + t.inner_half[1] + 0.02, 0.05);
        }
        lab.userData.isLabel = true;
        grp.add(lab);
      }
      grp.userData.static = b.static;
      grp.userData.isBead = isBead(b.name);
      this.world.add(grp);
      this.bodies.set(b.name, grp);
    }
    this.dirty = true;
  }

  // Beads that left the table top (they come to rest on the floor plane, out of view).
  beadsOffTable() {
    let n = 0;
    for (const [, g] of this.bodies) if (g.userData.isBead && g.visible && g.position.z < -0.05) n++;
    return n;
  }

  // Scale each bead sphere about its own centre so it spans at least MIN_PX on screen.
  // Positions are untouched; the legend says beads are drawn enlarged.
  _sizeBeads() {
    const MIN_PX = this.el.clientWidth < 560 ? 9 : 7;
    const h = this.renderer.domElement.clientHeight || 1;
    const k = h / (2 * Math.tan((this.camera.fov * Math.PI) / 360));
    const wp = new THREE.Vector3();
    for (const [, grp] of this.bodies) {
      if (!grp.userData.isBead || !grp.visible) continue;
      for (const m of grp.children) {
        const r = m.userData.beadR;
        if (!r) continue;
        m.getWorldPosition(wp);
        const d = Math.max(1e-3, wp.distanceTo(this.camera.position));
        m.scale.setScalar(Math.max(1, (MIN_PX / 2) / ((r * k) / d)));
      }
    }
  }

  // Nudge overlapping labels apart vertically in screen space (anchors stay put).
  _declutterLabels() {
    if (!this.showLabels) return;
    const items = [];
    for (const [, grp] of this.bodies) {
      for (const o of grp.children) {
        if (!o.isCSS2DObject || !o.visible || !grp.visible) continue;
        const span = o.element.firstChild;
        if (!span) continue;
        span.style.transform = '';
        const r = o.element.getBoundingClientRect();
        if (r.width) items.push({ span, x0: r.left, x1: r.right, y0: r.top, y1: r.bottom });
      }
    }
    items.sort((a, b) => b.y0 - a.y0);           // lowest first; others move up out of its way
    const placed = [];
    for (const it of items) {
      let dy = 0;
      for (let pass = 0; pass < 6; pass++) {
        const hit = placed.find((p) => it.x0 < p.x1 + 2 && it.x1 > p.x0 - 2 && it.y0 + dy < p.y1 + 2 && it.y1 + dy > p.y0 - 2);
        if (!hit) break;
        dy = hit.y0 - 2 - it.y1;
      }
      if (dy) it.span.style.transform = `translateY(${Math.round(dy)}px)`;
      placed.push({ ...it, y0: it.y0 + dy, y1: it.y1 + dy });
    }
  }

  _halfHeight(o) {
    if (o.kind === 'cup') return o.size.outer_half[2];
    if (o.kind === 'block') return o.size.half[2];
    return (o.size.half_length || 0) + (o.size.radius || 0);
  }

  _addGrid(grp) {
    const pts = [];
    const H = 0.55, W = 0.45, step = 0.05;
    for (let x = -H; x <= H + 1e-6; x += step) pts.push(x, -W, 0.0004, x, W, 0.0004);
    for (let y = -W; y <= W + 1e-6; y += step) pts.push(-H, y, 0.0004, H, y, 0.0004);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    this.gridMat = this.gridMat || new THREE.LineBasicMaterial({ transparent: true, opacity: 0.8 });
    const dark = document.documentElement.dataset.theme === 'dark';
    this.gridMat.color.set(dark ? '#3a3a41' : '#d9d7d1');
    grp.add(new THREE.LineSegments(g, this.gridMat));
  }

  // Static preview of a scene (no rollout): objects at their initial poses.
  showScene(visual, sceneState) {
    if (this.live) return;
    this.payload = null;
    this.ghost = null;
    this.playing = false;
    this.sceneState = sceneState;
    this._buildVisual(visual, sceneState);
    this._clearOverlay();
    const objs = new Map((sceneState?.objects || []).map((o) => [o.id, o]));
    for (const [name, grp] of this.bodies) {
      if (grp.userData.static) continue;
      if (objs.has(name)) {
        const o = objs.get(name);
        grp.position.set(...o.pose.pos);
        grp.quaternion.copy(q4(o.pose.quat));
      } else if (grp.userData.isBead) {
        grp.visible = false; // bead start positions come from the simulator, not the scene description
      } else if (isGripper(name) && sceneState?.gripper_start) {
        const p = sceneState.gripper_start.pos;
        const side = /left|_l\b|1$/i.test(name) ? 1 : /right|_r\b|2$/i.test(name) ? -1 : 0;
        grp.position.set(p[0], p[1] + side * 0.051, p[2] + (side ? 0.02 : 0));
        grp.quaternion.copy(q4(sceneState.gripper_start.quat));
      }
    }
    this.dirty = true;
  }

  // Live teleoperation: show bodies from one frame ({name: [x,y,z,w,x,y,z]}).
  showLive(visual, sceneState, frame) {
    this.live = true;
    this.payload = null; this.ghost = null; this.playing = false;
    this.onTick(0, false);
    this.sceneState = sceneState;
    this._buildVisual(visual, sceneState);
    this._clearOverlay();
    if (frame) this.applyBodies(frame.bodies);
  }

  endLive() { this.live = false; }

  applyBodies(bodies) {
    for (const [name, s] of Object.entries(bodies || {})) {
      const g = this.bodies.get(name);
      if (!g || !s) continue;
      g.visible = true;
      g.position.set(s[0], s[1], s[2]);
      g.quaternion.set(s[4], s[5], s[6], s[3]);
    }
    this.dirty = true;
  }

  // ---------------------------------------------------------- playback
  loadRollout(payload, { ghost = null, traceObject = null, sceneState = null, keepTime = false, tiltStatus = null } = {}) {
    if (this.live) return;
    this.payload = payload;
    // (frame index) -> 'measured' | 'unobserved' | ...; unobserved tilt is drawn neutral, never as upright.
    this.tiltStatus = tiltStatus;
    this.ghost = ghost;
    this.sceneState = sceneState || payload.scene || this.sceneState;
    this.traceObject = traceObject || this._pickTraceObject(payload);
    this._buildVisual(payload.visual, this.sceneState);
    if (payload.beads_hidden) for (const [, g] of this.bodies) if (g.userData.isBead) g.visible = false;
    this._buildTraces();
    if (!keepTime) this.t = 0;
    this._applyTime();
    if (this.view === 'follow') this.setView('follow');
    this.dirty = true;
  }

  _pickTraceObject(payload) {
    const names = Object.keys(payload.bodies || {});
    const cupObj = (this.sceneState?.objects || payload.scene?.objects || []).find((o) => o.kind === 'cup');
    if (cupObj && names.includes(cupObj.id)) return cupObj.id;
    return names.find((n) => /cup/i.test(n) && !isBead(n)) || null;
  }

  duration() { const t = this.payload?.t; return t && t.length ? t[t.length - 1] : 0; }

  _index(t) {
    const ts = this.payload.t;
    let lo = 0, hi = ts.length - 1;
    if (t <= ts[0]) return [0, 0];
    if (t >= ts[hi]) return [hi, 0];
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ts[mid] <= t) lo = mid; else hi = mid; }
    return [lo, (t - ts[lo]) / Math.max(1e-9, ts[lo + 1] - ts[lo])];
  }

  _applyTime() {
    const p = this.payload;
    if (!p || !p.t.length) return;
    const [i, a] = this._index(this.t);
    const j = Math.min(i + 1, p.t.length - 1);
    for (const [name, arr] of Object.entries(p.bodies)) {
      const grp = this.bodies.get(name);
      if (!grp) continue;
      const A = arr[i], B = arr[j] || A;
      if (!A) continue;
      if (!p.beads_hidden || !grp.userData.isBead) grp.visible = true;
      grp.position.set(A[0] + (B[0] - A[0]) * a, A[1] + (B[1] - A[1]) * a, A[2] + (B[2] - A[2]) * a);
      tmpA.set(A[4], A[5], A[6], A[3]);
      tmpB.set(B[4], B[5], B[6], B[3]);
      grp.quaternion.copy(tmpA).slerp(tmpB, a);
    }
    if (this.trace) {
      const k = upperBound(this.traceFrames, i);
      this.trace.geometry.instanceCount = Math.max(0, k - 1);
      this.trace.visible = this.showTrace && k > 1;
    }
    this.dirty = true;
  }

  frameIndex() { return this.payload ? this._index(this.t)[0] : 0; }

  setTime(t) { this.t = Math.max(0, Math.min(this.duration(), t)); this._applyTime(); this.onTick(this.t, this.playing); }
  play() { if (!this.payload) return; if (this.t >= this.duration() - 1e-6) this.t = 0; this.playing = true; this.onTick(this.t, true); }
  pause() { this.playing = false; this.onTick(this.t, false); }
  toggle() { this.playing ? this.pause() : this.play(); }

  // ------------------------------------------------------------ traces
  _clearOverlay() {
    for (const c of [...this.overlay.children]) { this.overlay.remove(c); c.geometry?.dispose?.(); }
    this.trace = null;
    this._lineMats = [];
  }

  _traceData(payload, objId, opts = {}) {
    const arr = payload?.bodies?.[objId];
    if (!arr) return null;
    const pos = [], col = [], frames = [], tilts = [];
    const c = new THREE.Color();
    let prev = null;
    for (let i = 0; i < arr.length; i++) {
      const s = arr[i];
      if (!s) continue;
      if (prev && Math.hypot(s[0] - prev[0], s[1] - prev[1], s[2] - prev[2]) < 0.003 && i !== arr.length - 1) continue;
      const tl = tiltDeg(s.slice(3));
      pos.push(s[0], s[1], s[2]);
      tilts.push(tl);
      const unobserved = !opts.ghost && payload === this.payload && this.tiltStatus && this.tiltStatus(i) === 'unobserved';
      if (unobserved) {
        c.set(cssVar('--faint') || '#999');
        this.hasUnobservedTilt = true;
      } else if (opts.ghost) {
        if (tl > this.tol) tiltColor(90, this.tol, c); else c.set(cssVar('--faint') || '#999');
      } else tiltColor(tl, this.tol, c);
      col.push(c.r, c.g, c.b);
      frames.push(i);
      prev = s;
    }
    return pos.length >= 6 ? { pos, col, frames, tilts } : null;
  }

  _line(data, { width = 3, opacity = 1 } = {}) {
    const g = new LineGeometry();
    g.setPositions(data.pos);
    g.setColors(data.col);
    const m = new LineMaterial({ vertexColors: true, linewidth: width, transparent: opacity < 1, opacity, depthTest: true });
    m.resolution.set(this.el.clientWidth || 1, this.el.clientHeight || 1);
    this._lineMats.push(m);
    const l = new Line2(g, m);
    l.computeLineDistances();
    l.renderOrder = 2;
    return l;
  }

  _buildTraces() {
    this._clearOverlay();
    this.hasUnobservedTilt = false;
    if (!this.payload || !this.traceObject) return;
    const cur = this._traceData(this.payload, this.traceObject);
    if (cur) {
      this.trace = this._line(cur, { width: 3.2 });
      this.traceFrames = cur.frames;
      this.overlay.add(this.trace);
    }
    if (this.ghost && this.showGhost) this._buildGhost(this.ghost);
    this._applyTime();
  }

  _buildGhost(g) {
    const obj = g.traceObject || this.traceObject;
    const d = this._traceData(g, obj, { ghost: true });
    if (!d) return;
    this.overlay.add(this._line(d, { width: 2.4, opacity: 0.85 }));
    // The peak-tilt pose of the old behaviour, drawn as an outline of the container.
    const arr = g.bodies[obj];
    let best = -1, bi = -1;
    arr.forEach((s, i) => { if (s) { const tl = tiltDeg(s.slice(3)); if (tl > best) { best = tl; bi = i; } } });
    const cupSpec = (g.scene?.objects || this.sceneState?.objects || []).find((o) => o.id === obj);
    if (bi >= 0 && best > this.tol && cupSpec?.size?.outer_half) {
      const h = cupSpec.size.outer_half;
      const edges = new THREE.EdgesGeometry(new THREE.BoxGeometry(2 * h[0], 2 * h[1], 2 * h[2]));
      const ln = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: new THREE.Color(cssVar('--bad')), transparent: true, opacity: 0.9 }));
      const s = arr[bi];
      ln.position.set(s[0], s[1], s[2]);
      ln.quaternion.set(s[4], s[5], s[6], s[3]);
      this.overlay.add(ln);
    }
    // Where the old behaviour's beads ended up, if they left the container.
    const last = arr[arr.length - 1];
    const spilled = [];
    for (const [name, a] of Object.entries(g.bodies)) {
      if (!isBead(name)) continue;
      const s = a[a.length - 1];
      if (s && last && Math.hypot(s[0] - last[0], s[1] - last[1], s[2] - last[2]) > 0.07) spilled.push(s);
    }
    if (spilled.length) {
      const geo = new THREE.SphereGeometry(0.008, 12, 8);
      const mat = new THREE.MeshBasicMaterial({ color: new THREE.Color(cssVar('--bad')), transparent: true, opacity: 0.55 });
      const im = new THREE.InstancedMesh(geo, mat, spilled.length);
      const M = new THREE.Matrix4();
      spilled.forEach((s, i) => { M.makeTranslation(s[0], s[1], s[2]); im.setMatrixAt(i, M); });
      this.overlay.add(im);
      const ringGeo = new THREE.RingGeometry(0.013, 0.0145, 24);
      const ringMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(cssVar('--bad')), side: THREE.DoubleSide });
      for (const s of spilled) {
        const r = new THREE.Mesh(ringGeo, ringMat);
        r.position.set(s[0], s[1], 0.001);
        this.overlay.add(r);
      }
    }
    this.ghostSpilled = spilled.length;
  }

  setOptions({ labels, trace, ghost } = {}) {
    if (labels !== undefined && labels !== this.showLabels) {
      this.showLabels = labels;
      this.world.traverse((o) => { if (o.userData?.isLabel) o.visible = labels; });
    }
    if (trace !== undefined) this.showTrace = trace;
    if (ghost !== undefined && ghost !== this.showGhost) { this.showGhost = ghost; if (this.payload) this._buildTraces(); }
    this._applyTime();
    this.dirty = true;
  }

  // ------------------------------------------------------- scene edits
  setEditMode(mode) {
    this.editMode = mode;
    this.renderer.domElement.style.cursor = mode === 'obstacle' ? 'crosshair' : mode === 'drag' ? 'grab' : '';
    if (this.preview) { this.overlay.remove(this.preview); this.preview = null; this.dirty = true; }
  }

  _hit(ev) {
    const r = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    return this.raycaster;
  }

  _planePoint(rc) { const p = new THREE.Vector3(); return rc.ray.intersectPlane(this.plane, p) ? p : null; }

  _bindPointer() {
    const dom = this.renderer.domElement;
    dom.addEventListener('pointerdown', (ev) => {
      if (!this.editMode) return;
      const rc = this._hit(ev);
      if (this.editMode === 'obstacle') {
        const p = this._planePoint(rc);
        if (p) this.onEdit({ kind: 'obstacle', x: p.x, y: p.y });
        return;
      }
      const hits = rc.intersectObjects(this.world.children, true);
      for (const h of hits) {
        let o = h.object;
        while (o && !o.userData?.editable) o = o.parent;
        if (o) {
          const p = this._planePoint(rc);
          if (!p) return;
          const ed = o.userData.editable;
          const base = ed.kind === 'tray' ? this._trayPos(ed.id) : o.position.clone();
          this.drag = { grp: o, ed, start: p, origin: o.position.clone(), base };
          this.controls.enabled = false;
          dom.setPointerCapture(ev.pointerId);
          dom.style.cursor = 'grabbing';
          return;
        }
      }
    });
    dom.addEventListener('pointermove', (ev) => {
      if (this.drag) {
        const p = this._planePoint(this._hit(ev));
        if (!p) return;
        const d = this.drag;
        d.grp.position.set(d.origin.x + p.x - d.start.x, d.origin.y + p.y - d.start.y, d.origin.z);
        d.last = p;
        this.dirty = true;
      } else if (this.editMode === 'obstacle') {
        const p = this._planePoint(this._hit(ev));
        if (!p) return;
        if (!this.preview) {
          this.preview = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.08, 0.24),
            new THREE.MeshBasicMaterial({ color: new THREE.Color(cssVar('--accent')), transparent: true, opacity: 0.25 }));
          this.overlay.add(this.preview);
        }
        this.preview.position.set(p.x, p.y, 0.12);
        this.dirty = true;
      }
    });
    const end = (ev) => {
      if (!this.drag) return;
      const d = this.drag;
      this.drag = null;
      this.controls.enabled = true;
      this.renderer.domElement.style.cursor = this.editMode === 'drag' ? 'grab' : '';
      if (d.last) {
        const dx = d.last.x - d.start.x, dy = d.last.y - d.start.y;
        if (Math.hypot(dx, dy) > 0.004) this.onEdit({ kind: d.ed.kind, id: d.ed.id, x: d.base.x + dx, y: d.base.y + dy });
      }
    };
    dom.addEventListener('pointerup', end);
    dom.addEventListener('pointercancel', end);
  }

  _trayPos(id) {
    const t = (this.sceneState?.trays || []).find((x) => x.id === id);
    return new THREE.Vector3(t ? t.pose.pos[0] : 0, t ? t.pose.pos[1] : 0, 0);
  }

  snapshotPNG() { this.renderer.render(this.scene, this.camera); return this.renderer.domElement.toDataURL('image/png'); }
}

function upperBound(arr, v) { // number of entries <= v
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= v) lo = m + 1; else hi = m; }
  return lo;
}
