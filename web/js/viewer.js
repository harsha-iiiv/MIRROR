// Three.js playback workspace for MuJoCo rollouts (VisualScene + columnar frames + the checker's
// records). Public API is the one the studio uses (see status/viz3d.md for additions):
//   new Viewer(el, {onTick, onEdit, tol}) · loadRollout(payload, {ghost, traceObject, sceneState, keepTime, tiltStatus, spec})
//   showScene · showLive · applyBodies · endLive · setView · setOptions · setTime · play/pause/toggle
//   duration · frameIndex · beadsOffTable · snapshotPNG · setEditMode · applyTheme · dispose
// Additions: setSpecs/setSpec, setCapture, stepFrame, seekEvent, events, mountTimeline, measure, getState.
// Without WebGL the constructor returns a top-down 2D view with the same surface (viz/fallback2d.js).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { tiltDeg } from './util.js';
import { cssVar, isDark, isSmallDevice, prefersReducedMotion, setupRenderer, makeEnvironment, makeLights, bodyRole, materialFor, beadMaterial, tableTexture, frameTriad } from './viz/look.js';
import { RuleLayer } from './viz/rules.js';
import { GhostLayer } from './viz/ghost.js';
import { PhoneLayer } from './viz/phone.js';
import { Glide } from './viz/ease.js';
import { buildRibbon } from './viz/ribbon.js';
import { intervalsFor, verdictAt, events as runEvents } from './viz/verdict.js';
import { makeHud, cardHTML } from './viz/hud.js';
import { Timeline } from './viz/timeline.js';
import { Fallback2D } from './viz/fallback2d.js';
import { injectStyles } from './viz/style.js';
import { tiltOf } from './viz/geom.js';

const Z_UP = new THREE.Vector3(0, 0, 1);
const tmpA = new THREE.Quaternion(), tmpB = new THREE.Quaternion();
function q4(q) { return new THREE.Quaternion(q[1], q[2], q[3], q[0]); }  // (w,x,y,z) -> three

export function tiltColor(tilt, tol, out = new THREE.Color()) {
  const accent = new THREE.Color(cssVar('--accent') || '#2f54d9');
  const warn = new THREE.Color(cssVar('--warn') || '#b7791f');
  const bad = new THREE.Color(cssVar('--bad') || '#c8372d');
  if (tilt <= tol) return out.copy(accent);
  if (tilt <= 45) return out.copy(warn).lerp(bad, (tilt - tol) / (45 - tol) * 0.35);
  return out.copy(warn).lerp(bad, 0.35 + 0.65 * Math.min(1, (tilt - 45) / 45));
}

export function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGL2RenderingContext && c.getContext('webgl2')) || !!c.getContext('webgl');
  } catch { return false; }
}

function geomMesh(g, material) {
  let geo = null;
  const s = g.size;
  switch (g.type) {
    case 'box': geo = new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]); break;
    case 'sphere': geo = new THREE.SphereGeometry(s[0], 16, 12); break;
    case 'cylinder': geo = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 40); geo.rotateX(Math.PI / 2); break;
    case 'capsule': geo = new THREE.CapsuleGeometry(s[0], 2 * s[1], 8, 20); geo.rotateX(Math.PI / 2); break;
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
function upperBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= v) lo = m + 1; else hi = m; } return lo; }

export class Viewer {
  constructor(el, opts = {}) {
    if (opts.force2d || !webglAvailable()) return new Fallback2D(el, opts);
    let renderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' }); }
    catch (e) { console.warn('WebGL failed, using the 2D view', e); return new Fallback2D(el, opts); }
    injectStyles();
    this.el = el;
    this.tol = opts.tol ?? 15;
    this.onTick = opts.onTick || (() => {});
    this.onEdit = opts.onEdit || (() => {});
    this.onHover = opts.onHover || (() => {});
    this.small = isSmallDevice(el);
    this.renderer = renderer;
    setupRenderer(renderer, this.small);
    el.appendChild(renderer.domElement);
    renderer.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); this.contextLost = true; });
    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: 'absolute', left: '0px', top: '0px', pointerEvents: 'none' });
    el.appendChild(this.labels.domElement);
    this.hud = makeHud(el);

    this.scene = new THREE.Scene();
    try { this.scene.environment = makeEnvironment(renderer); this.scene.environmentIntensity = 0.6; } catch (e) { console.warn('environment light unavailable', e); }
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
    this.camera.up.copy(Z_UP);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 0.2;
    this.controls.maxDistance = 4;
    this.controls.addEventListener('change', () => (this.dirty = true));
    this.controls.addEventListener('start', () => { this._userMoved = true; this.glide.cancel(); if (this.view === 'phone') this._exitPhone(); });
    this.glide = new Glide(this.camera, this.controls);
    this._followPos = new THREE.Vector3();

    const L = makeLights(this.scene, this.small);
    this.hemi = L.hemi; this.sun = L.key;

    this.world = new THREE.Group();   // static + dynamic bodies
    this.overlay = new THREE.Group(); // ribbons, ghost paths
    this.rules = new RuleLayer();
    this.ghostLayer = new GhostLayer();
    this.phone = new PhoneLayer();
    this.measureGroup = new THREE.Group();
    this.triad = frameTriad();
    this.scene.add(this.world, this.overlay, this.rules.group, this.ghostLayer.group, this.phone.group, this.measureGroup, this.triad);
    this.bodies = new Map();
    this.payload = null;
    this.ghost = null;
    this.specs = [];
    this.spec = null;
    this.t = 0;
    this.playing = false;
    this.speed = 1;
    this.showLabels = true;
    this.showTrace = true;
    this.showGhost = true;
    this.showRules = true;
    this.showMeasure = true;
    this.showTableTexture = true;
    this.editMode = null;
    this.dirty = true;
    this.view = '3d';
    this._fps = { n: 0, t0: performance.now(), value: 0 };

    this.raycaster = new THREE.Raycaster();
    this.plane = new THREE.Plane(Z_UP, 0);
    this._bindPointer();
    this._bindKeys();
    this.applyTheme();
    this.setView('3d', { instant: true });

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(el);
    this.resize();
    this._last = performance.now();
    const loop = (now) => {
      this._raf = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - this._last) / 1000);
      this._last = now;
      if (!this.frozen) this._advance(dt, now);   // frozen: tests drive _advance/_render themselves
    };
    this._raf = requestAnimationFrame(loop);
  }

  // One frame of work; also called by tests to step time without waiting for vsync.
  _advance(dt, now = performance.now()) {
    if (this.playing && this.payload) {
      const end = this.duration();
      this.t = Math.min(end, this.t + dt * this.speed);
      if (this.t >= end) this.playing = false;
      this._applyTime();
      this.onTick(this.t, this.playing);
    }
    if (this.glide.step(dt)) this.dirty = true;
    if (this.view === 'follow') this._follow(dt);
    const animating = this.showRules && this.rules.update(this.t, this.frameIndex(), this.bodies, now);
    const moved = this.view !== 'phone' && this.controls.update();
    if (moved || this.dirty || animating) this._render();
  }

  _render() {
    this._sizeBeads();
    if (this.view === 'phone' && this.phone.view) {
      const r = this._phoneRect();
      this.renderer.setScissorTest(true);
      this.renderer.setViewport(0, 0, this._w, this._h); this.renderer.setScissor(0, 0, this._w, this._h);
      this.renderer.setClearColor(new THREE.Color(cssVar('--viewer') || '#eceae6'), 1);
      this.renderer.clear();
      this.renderer.setViewport(r.x, r.y, r.w, r.h); this.renderer.setScissor(r.x, r.y, r.w, r.h);
      this.renderer.render(this.scene, this.camera);
      this.renderer.setScissorTest(false);
      Object.assign(this.labels.domElement.style, { left: `${r.x}px`, top: `${this._h - r.y - r.h}px`, right: 'auto', bottom: 'auto' });
      this.labels.setSize(r.w, r.h);
    } else {
      this.renderer.setViewport(0, 0, this._w, this._h);
      this.renderer.render(this.scene, this.camera);
    }
    this.labels.render(this.scene, this.camera);
    this._declutterLabels();
    this.dirty = false;
    const f = this._fps; f.n++;
    const now = performance.now();
    if (now - f.t0 > 1000) { f.value = (f.n * 1000) / (now - f.t0); f.n = 0; f.t0 = now; if (!this.hud.fps.hidden) this.hud.fps.textContent = `${f.value.toFixed(0)} fps`; }
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    this.ro.disconnect();
    this.timeline?.dispose();
    window.removeEventListener('keydown', this._winKey);
    this.renderer.dispose();
    this.el.innerHTML = '';
  }

  resize() {
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1;
    this._w = w; this._h = h;
    const aspect0 = this.camera.aspect;
    this.renderer.setSize(w, h, false);
    if (this.view !== 'phone') {
      this._labelsFull();
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
    for (const m of this._lineMats || []) m.resolution.set(w, h);
    if (!this._userMoved && this.view !== 'phone' && Math.abs(aspect0 - this.camera.aspect) > 0.05) this.setView(this.view, { instant: true });
    this.dirty = true;
  }

  _labelsFull() {
    Object.assign(this.labels.domElement.style, { left: '0px', top: '0px', right: 'auto', bottom: 'auto' });
    this.labels.setSize(this._w || 1, this._h || 1);
  }

  _frameScale() {
    const a = this.camera.aspect || 1.6;
    return a >= 1.3 ? 1 : Math.min(2.1, Math.pow(1.3 / a, 0.85));
  }

  applyTheme() {
    this.scene.background = new THREE.Color(cssVar('--viewer') || '#eceae6');
    const dark = isDark();
    this.hemi.intensity = dark ? 0.25 : 0.35;
    if (this.tableTop) { this.tableTop.map?.dispose(); this.tableTop.map = tableTexture(this._tableHalf[0], this._tableHalf[1], this.renderer); this.tableTop.needsUpdate = true; }
    if (this.floorMat) this.floorMat.color.set(dark ? '#17171a' : '#dedcd6');
    if (this.edgeMat) this.edgeMat.color.set(cssVar('--muted'));
    if (this.payload) this._buildTraces();
    this.dirty = true;
  }

  // ---------------------------------------------------------- cameras
  _viewPose(kind) {
    const k = this._frameScale();
    const P = (target, offset) => ({ target: new THREE.Vector3(...target), pos: new THREE.Vector3(target[0] + offset[0] * k, target[1] + offset[1] * k, target[2] + offset[2] * k) });
    if (kind === 'top') return P([0.02, 0, 0], [0, -0.04, 1.55]);
    if (kind === 'side') return P([0.0, 0, 0.12], [0, -1.35, 0.30]);
    if (kind === 'follow') {
      const p = this._followTarget() || new THREE.Vector3(0.02, 0.02, 0.04);
      this._followPos.copy(p);
      return P([p.x, p.y, p.z], [-0.30, -0.74, 0.58]);
    }
    return P([0.02, 0.02, 0.04], [-0.44, -1.14, 0.98]);
  }

  setView(kind, { instant = false } = {}) {
    if (kind === 'phone') { this._enterPhone(); return; }
    if (this.view === 'phone') this._exitPhone(false);
    const from = this.view;
    this.view = kind;
    const v = this._viewPose(kind);
    // Glide between presets; snap on first framing, re-framing after resize, and reduced motion.
    this.glide.to(v.target, v.pos, { duration: instant || from === undefined ? 0 : 0.9 });
    if (instant) this.controls.update();
    this.dirty = true;
  }

  _phoneRect() {
    const a = this.phone.aspect(), W = this._w, H = this._h;
    let w = W, h = W / a;
    if (h > H) { h = H; w = H * a; }
    return { x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w: Math.round(w), h: Math.round(h) };
  }

  _enterPhone() {
    if (!this.phone.view) return;
    this.glide.cancel();
    this._prevView = this.view === 'phone' ? '3d' : this.view;
    this.view = 'phone';
    this.controls.enabled = false;
    this.camera.up.set(0, 0, 1);
    this.phone.poseCamera(this.camera);
    this.camera.projectionMatrix.copy(this.phone.projection(0.01, 20));
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert();
    if (this.phone.backdrop) this.phone.backdrop.visible = true;
    this.phone.group.children.forEach((c) => { if (c !== this.phone.backdrop) c.visible = false; });
    this.dirty = true;
  }

  _exitPhone(reframe = true) {
    if (this.view !== 'phone') return;
    this.controls.enabled = !this.editMode || this.editMode === 'obstacle';
    this.controls.enabled = true;
    if (this.phone.backdrop) this.phone.backdrop.visible = false;
    this.phone.group.children.forEach((c) => { if (c !== this.phone.backdrop) c.visible = true; });
    this.view = this._prevView || '3d';
    this.resize();
    this.camera.aspect = this._w / this._h; this.camera.updateProjectionMatrix();
    if (reframe) this.setView(this.view, { instant: true });
    this.dirty = true;
  }

  _followTarget() {
    const g = this.traceObject && this.bodies.get(this.traceObject);
    if (!g || !g.visible || !this.payload) return null;
    return new THREE.Vector3(g.position.x, g.position.y, Math.max(0.03, g.position.z));
  }

  _follow(dt) {
    if (this.glide.active) return;
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
    this.tableTop = null;
  }

  _buildVisual(visual, sceneState) {
    this._clearWorld();
    if (!visual) return;
    const trayIds = new Set((sceneState?.trays || []).map((t) => t.id));
    const objIds = new Map((sceneState?.objects || []).map((o) => [o.id, o]));
    for (const b of visual.bodies) {
      const grp = new THREE.Group();
      grp.name = b.name;
      const role = bodyRole(b.name, sceneState);
      for (const g of b.geoms) {
        const isTable = b.static && (/table/i.test(g.name || '') || (/table/i.test(b.name) && g.type === 'box'));
        const isFloor = b.static && !isTable && (g.type === 'plane' || /floor|ground/i.test(g.name || ''));
        let mat;
        if (isTable && g.type === 'box') {
          this._tableHalf = [g.size[0], g.size[1]];
          const side = new THREE.MeshStandardMaterial({ color: isDark() ? '#1d1e22' : '#e4e2dc', roughness: 0.9 });
          this.tableTop = new THREE.MeshStandardMaterial({ map: tableTexture(g.size[0], g.size[1], this.renderer), roughness: 0.92, metalness: 0 });
          mat = [side, side, side, side, this.tableTop, side];
        } else if (isFloor) {
          this.floorMat = this.floorMat || new THREE.MeshStandardMaterial({ roughness: 1 });
          this.floorMat.color.set(isDark() ? '#17171a' : '#dedcd6');
          mat = this.floorMat;
        } else if (isBead(b.name)) {
          mat = beadMaterial();
        } else {
          mat = materialFor(role, g.rgba, { translucent: wallLike(b, g, trayIds) });
        }
        const m = geomMesh(g, mat);
        if (!m) continue;
        grp.add(m);
        if (isBead(b.name)) { m.userData.beadR = Math.max(1e-4, g.size[0]); m.castShadow = false; }
        if (b.static && !isTable && !isFloor && g.type === 'box') {
          this.edgeMat = this.edgeMat || new THREE.LineBasicMaterial({ transparent: true, opacity: 0.5 });
          this.edgeMat.color.set(cssVar('--muted') || '#6b6b70');
          const e = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), this.edgeMat);
          e.position.copy(m.position); e.quaternion.copy(m.quaternion);
          grp.add(e);
        }
        if (wallLike(b, g, trayIds)) m.castShadow = false;
      }
      if (trayIds.has(b.name)) grp.userData.editable = { kind: 'tray', id: b.name };
      if (objIds.has(b.name)) grp.userData.editable = { kind: 'object', id: b.name };
      if (trayIds.has(b.name) || objIds.has(b.name)) {
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
        lab.visible = this.showLabels;
        grp.add(lab);
      }
      grp.userData.static = b.static;
      grp.userData.isBead = isBead(b.name);
      grp.userData.bodyName = b.name;
      grp.userData.role = role;
      this.world.add(grp);
      this.bodies.set(b.name, grp);
    }
    if (this.phone.ground) this.world.add(this.phone.ground), (this.phone.ground.visible = this.showTableTexture);
    this.dirty = true;
  }

  beadsOffTable() {
    let n = 0;
    for (const [, g] of this.bodies) if (g.userData.isBead && g.visible && g.position.z < -0.05) n++;
    return n;
  }

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

  _declutterLabels() {
    if (!this.showLabels) return;
    const items = [];
    const collect = (root) => root.traverse((o) => {
      if (!o.isCSS2DObject) return;
      let vis = true; for (let p = o; p; p = p.parent) if (!p.visible) { vis = false; break; }
      if (!vis) return;
      const span = o.element.firstChild;
      if (!span) return;
      span.style.transform = '';
      const r = o.element.getBoundingClientRect();
      if (r.width) items.push({ span, x0: r.left, x1: r.right, y0: r.top, y1: r.bottom });
    });
    collect(this.world); collect(this.rules.group);
    items.sort((a, b) => b.y0 - a.y0);
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

  showScene(visual, sceneState) {
    if (this.live) return;
    this.payload = null;
    this.report = null;
    this.ghost = null;
    this.playing = false;
    this.sceneState = sceneState;
    this._buildVisual(visual, sceneState);
    this._clearOverlay();
    this.ghostLayer.clear();
    this._buildRules();
    const objs = new Map((sceneState?.objects || []).map((o) => [o.id, o]));
    for (const [name, grp] of this.bodies) {
      if (grp.userData.static) continue;
      if (objs.has(name)) {
        const o = objs.get(name);
        grp.position.set(...o.pose.pos);
        grp.quaternion.copy(q4(o.pose.quat));
      } else if (grp.userData.isBead) {
        grp.visible = false;
      } else if (isGripper(name) && sceneState?.gripper_start) {
        const p = sceneState.gripper_start.pos;
        const side = /left|_l\b|1$/i.test(name) ? 1 : /right|_r\b|2$/i.test(name) ? -1 : 0;
        grp.position.set(p[0], p[1] + side * 0.051, p[2] + (side ? 0.02 : 0));
        grp.quaternion.copy(q4(sceneState.gripper_start.quat));
      }
    }
    this.timeline?.refresh();
    this.dirty = true;
  }

  showLive(visual, sceneState, frame) {
    this.live = true;
    this.payload = null; this.ghost = null; this.playing = false;
    this.onTick(0, false);
    this.sceneState = sceneState;
    this._buildVisual(visual, sceneState);
    this._clearOverlay();
    this.ghostLayer.clear();
    this.rules.clear();
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

  // ------------------------------------------------------------- specs
  // All spec versions of the session (studio: snap.specs). The viewer picks the one a rollout was
  // checked under (report.spec_id + spec_version) to draw its rule geometry.
  setSpecs(specs) { this.specs = Array.isArray(specs) ? specs : specs ? Object.values(specs) : []; if (this.payload || this.sceneState) this._buildRules(); }
  setSpec(spec) { this._specOverride = spec || null; this._buildRules(); }

  _specFor(report) {
    if (this._specOverride) return this._specOverride;
    if (!report) return this.specs[this.specs.length - 1] || null;
    return this.specs.find((s) => s.spec_id === report.spec_id && s.version === report.spec_version)
      || this.specs.find((s) => s.version === report.spec_version) || null;
  }

  _buildRules() {
    const report = this.payload ? this.report : null;
    this.spec = this._loadSpec || this._specFor(report);
    this.rules.build({ scene: this.sceneState, spec: this.spec, report, payload: this.payload, tol: this.tol });
    this.rules.group.visible = this.showRules;
    this.clearanceRule = (this.spec?.constraints || []).find((c) => c.type === 'keep_clearance');
    this.dirty = true;
  }

  // ---------------------------------------------------------- playback
  loadRollout(payload, { ghost = null, traceObject = null, sceneState = null, keepTime = false, tiltStatus = null, spec = null, report = null } = {}) {
    if (this.live) return;
    this.payload = payload;
    // `report`: judge this run's frames with another report, e.g. a re-check under a newer spec.
    this.report = report || payload.report || null;
    this.tiltStatus = tiltStatus;
    this.ghost = ghost;
    this._loadSpec = spec;
    this.sceneState = sceneState || payload.scene || this.sceneState;
    this.traceObject = traceObject || this._pickTraceObject(payload);
    this._buildVisual(payload.visual, this.sceneState);
    if (payload.beads_hidden) for (const [, g] of this.bodies) if (g.userData.isBead) g.visible = false;
    this._buildRules();
    this.ghostLayer.build(this.showGhost ? ghost : null);
    this._buildTraces();
    if (!keepTime) this.t = 0;
    this._applyTime();
    if (this.view === 'follow') this.setView('follow', { instant: true });
    this.timeline?.refresh();
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
    for (const r of this.ribbons || []) { r.setFrame(i); r.group.visible = this.showTrace; }
    if (this.trace) this.trace.visible = this.showTrace && (this.ribbons?.length || 0) > 0;
    if (this.showGhost) this.ghostLayer.setTime(this.t);
    this._updateMeasure();
    this.timeline?.draw();
    this.dirty = true;
  }

  frameIndex() { return this.payload ? this._index(this.t)[0] : 0; }

  setTime(t) { this.t = Math.max(0, Math.min(this.duration(), t)); this._applyTime(); this.onTick(this.t, this.playing); }
  play() { if (!this.payload) return; if (this.t >= this.duration() - 1e-6) this.t = 0; this.playing = true; this.onTick(this.t, true); }
  pause() { this.playing = false; this.onTick(this.t, false); }
  toggle() { this.playing ? this.pause() : this.play(); }

  // Frame-accurate stepping on the playback frames (the checker itself used every physics step).
  stepFrame(n = 1) {
    if (!this.payload) return;
    const ts = this.payload.t;
    const [i, a] = this._index(this.t);
    const base = n < 0 && a > 1e-6 ? i + 1 : i;
    const k = Math.max(0, Math.min(ts.length - 1, base + n));
    this.setTime(ts[k]);
  }

  events() { return runEvents(this.payload); }

  // Jump to the next/previous event: grasp, release, violation start or end.
  seekEvent(dir = 1) {
    if (!this.payload) return;
    const ts = [...this.events().map((e) => e.t), ...intervalsFor(this.report, null).flatMap((v) => [v.t_start, v.t_end])].sort((a, b) => a - b);
    const eps = 1e-3;
    const tgt = dir > 0 ? ts.find((x) => x > this.t + eps) : [...ts].reverse().find((x) => x < this.t - eps);
    if (tgt !== undefined) { this.pause(); this.setTime(tgt); }
  }

  mountTimeline(container) { this.timeline?.dispose(); this.timeline = new Timeline(container, this); return this.timeline; }

  // ------------------------------------------------------------ traces
  _clearOverlay() {
    for (const c of [...this.overlay.children]) { this.overlay.remove(c); c.traverse?.((o) => o.geometry?.dispose?.()); }
    this.trace = null;
    this.ribbons = [];
    this._lineMats = [];
  }

  _verdictColor(kind, out = new THREE.Color()) {
    if (kind === 'violated') return out.set(cssVar('--bad') || '#c8372d');
    if (kind === 'advisory') return out.set(cssVar('--warn') || '#b7791f');
    if (kind === 'unobserved') return out.set(cssVar('--faint') || '#a1a1a6');
    return out.set(cssVar('--accent') || '#2f54d9');
  }

  // Ribbons: the traced object (thick) and every other moved object (thin), coloured per frame
  // by the checker's intervals for that object. Demonstration traces (no report) colour the cup
  // by tilt against the tolerance and mark unobserved tilt neutral, as before.
  _buildTraces() {
    this._clearOverlay();
    this.hasUnobservedTilt = false;
    const p = this.payload;
    if (!p) return;
    const report = this.report || null;
    const moving = (name) => { const a = p.bodies[name]; if (!a) return false; let f = null, l = null; for (const s of a) if (s) { f = f || s; l = s; } return f && l && Math.hypot(f[0] - l[0], f[1] - l[1], f[2] - l[2]) > 0.02; };
    const objs = (this.sceneState?.objects || []).map((o) => o.id).filter((id) => p.bodies[id] && (id === this.traceObject || moving(id)));
    if (this.traceObject && !objs.includes(this.traceObject) && p.bodies[this.traceObject]) objs.unshift(this.traceObject);
    const c = new THREE.Color();
    this.verdictMode = report ? 'verdict' : 'tilt';
    for (const id of objs) {
      const main = id === this.traceObject;
      const ints = intervalsFor(report, id);
      const arr = p.bodies[id];
      const colorAt = (i) => {
        if (main && this.tiltStatus && this.tiltStatus(i) === 'unobserved') { this.hasUnobservedTilt = true; return this._verdictColor('unobserved', c); }
        if (!report) return main ? tiltColor(tiltDeg(arr[i].slice(3)), this.tol, c) : c.set(cssVar('--muted') || '#888');
        const v = verdictAt(ints, p.t[i]);
        if (v === 'ok' && !main) return c.set(cssVar('--muted') || '#888');
        return this._verdictColor(v, c);
      };
      const r = buildRibbon(p, id, colorAt, { radius: main ? 0.0022 : 0.0012, futureOpacity: main ? 0.18 : 0.1 });
      if (!r) continue;
      r.object = id;
      this.overlay.add(r.group);
      this.ribbons.push(r);
      if (main) this.trace = r.group;
    }
    if (this.ghost && this.showGhost) this._buildGhost(this.ghost);
    this._applyTime();
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

  _ghostPathData(g, objId) {
    const arr = g?.bodies?.[objId];
    if (!arr) return null;
    const pos = [], col = [];
    const c = new THREE.Color();
    let prev = null;
    for (let i = 0; i < arr.length; i++) {
      const s = arr[i];
      if (!s) continue;
      if (prev && Math.hypot(s[0] - prev[0], s[1] - prev[1], s[2] - prev[2]) < 0.003 && i !== arr.length - 1) continue;
      if (tiltDeg(s.slice(3)) > this.tol) tiltColor(90, this.tol, c); else c.set(cssVar('--faint') || '#999');
      pos.push(s[0], s[1], s[2]); col.push(c.r, c.g, c.b);
      prev = s;
    }
    return pos.length >= 6 ? { pos, col } : null;
  }

  _buildGhost(g) {
    const obj = g.traceObject || this.traceObject;
    const d = this._ghostPathData(g, obj);
    if (!d) return;
    this.overlay.add(this._line(d, { width: 2.0, opacity: 0.8 }));
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

  setOptions({ labels, trace, ghost, rules, measure, table, fps } = {}) {
    if (labels !== undefined && labels !== this.showLabels) {
      this.showLabels = labels;
      const set = (root) => root.traverse((o) => { if (o.userData?.isLabel) o.visible = labels; });
      set(this.world); set(this.rules.group); set(this.phone.group);
    }
    if (trace !== undefined) this.showTrace = trace;
    if (ghost !== undefined && ghost !== this.showGhost) {
      this.showGhost = ghost;
      this.ghostLayer.build(ghost ? this.ghost : null);
      if (this.payload) this._buildTraces();
    }
    if (rules !== undefined) { this.showRules = rules; this.rules.group.visible = rules; }
    if (measure !== undefined) { this.showMeasure = measure; if (!measure) this.hud.card.hidden = true; }
    if (table !== undefined) { this.showTableTexture = table; if (this.phone.ground) this.phone.ground.visible = table; }
    if (fps !== undefined) this.hud.fps.hidden = !fps;
    this._applyTime();
    this.dirty = true;
  }

  getOptions() {
    return { labels: this.showLabels, trace: this.showTrace, ghost: this.showGhost, rules: this.showRules, measure: this.showMeasure, table: this.showTableTexture };
  }

  // ------------------------------------------------------------ capture
  // A real phone capture: calibrated camera frustum + video frame + rectified table (see phone.js).
  async setCapture(view, base = '') {
    if (this.view === 'phone') this._exitPhone();
    await this.phone.build(view, base);
    if (this.phone.ground) { this.world.add(this.phone.ground); this.phone.ground.visible = this.showTableTexture; }
    this.phone.group.traverse((o) => { if (o.userData?.isLabel) o.visible = this.showLabels; });
    this.dirty = true;
    return !!this.phone.view;
  }

  // ------------------------------------------------------------ measure
  // Checker-definition readouts for one body at the current playback frame.
  measure(name) {
    const grp = this.bodies.get(name);
    if (!grp) return null;
    const p = this.payload;
    const i = this.frameIndex();
    const q = grp.quaternion;
    const quat = [q.w, q.x, q.y, q.z];
    const obj = (this.sceneState?.objects || []).find((o) => o.id === name);
    const out = { name, kind: obj?.kind || grp.userData.role, t: this.t, frame: i };
    if (!grp.userData.static) {
      out.tilt = tiltOf(quat);
      out.height = grp.position.z;
      const up = (this.spec?.constraints || []).find((c) => c.type === 'upright' && (!c.applies_to?.kind || c.applies_to.kind === obj?.kind));
      if (up) { out.tiltLimit = +(up.params?.max_tilt_deg ?? this.tol); out.tiltOver = out.tilt > out.tiltLimit; }
      const arr = p?.bodies?.[name];
      if (arr && i > 0 && arr[i] && arr[i - 1]) {
        const a = arr[i - 1], b = arr[i];
        out.speed = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) / Math.max(1e-6, p.t[i] - p.t[i - 1]);
      }
    }
    if (obj) {
      out.clear = this.rules.closestFor(name, grp);
      if (out.clear && this.clearanceRule) { out.clearLimit = +this.clearanceRule.params.min_m; out.clearOver = out.clear.d < out.clearLimit; }
    }
    return out;
  }

  _measureLine(r, over) {
    if (!this._mLine) {
      const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      this._mLine = new THREE.Line(geo, new THREE.LineBasicMaterial({ depthTest: false, transparent: true }));
      this._mLine.renderOrder = 7;
      const dotGeo = new THREE.SphereGeometry(0.0022, 10, 8);
      this._mDots = [new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial({ depthTest: false })), new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial({ depthTest: false }))];
      this._mDots.forEach((d) => (d.renderOrder = 7));
      const div = document.createElement('div'); div.className = 'label3d viz-measure'; div.appendChild(document.createElement('span'));
      this._mLab = new CSS2DObject(div);
      this.measureGroup.add(this._mLine, ...this._mDots, this._mLab);
    }
    const col = new THREE.Color(over ? cssVar('--bad') || '#c8372d' : cssVar('--text') || '#18181b');
    this._mLine.material.color.copy(col); this._mDots.forEach((d) => d.material.color.copy(col));
    const a = new THREE.Vector3(...r.pA), b = new THREE.Vector3(...r.pB);
    this._mLine.geometry.setFromPoints([a, b]);
    this._mDots[0].position.copy(a); this._mDots[1].position.copy(b);
    this._mLab.position.copy(a).lerp(b, 0.5);
    this._mLab.element.firstChild.textContent = `${(r.d * 100).toFixed(2)} cm`;
    this._mLab.element.classList.toggle('bad', !!over);
    this.measureGroup.visible = true;
  }

  // Persistent clearance line for the traced object when a keep_clearance rule exists, or the
  // hovered object's closest approach.
  _updateMeasure() {
    const target = this._hoverName || (this.clearanceRule && this.showRules ? this.traceObject : null);
    if (!this.showMeasure && !this.clearanceRule) { this.measureGroup.visible = false; return; }
    const m = target ? this.measure(target) : null;
    if (m?.clear && (this.showMeasure || this.clearanceRule)) this._measureLine(m.clear, m.clearOver);
    else this.measureGroup.visible = false;
    if (this._hoverName && m && this.showMeasure) {
      this.hud.card.innerHTML = cardHTML(m);
      this.hud.card.hidden = false;
      this.onHover(m);
    }
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

  _hover(ev) {
    if (!this.showMeasure || this.editMode || this.drag || this.view === 'phone') return;
    const rc = this._hit(ev);
    const hits = rc.intersectObjects(this.world.children, true);
    let name = null;
    for (const h of hits) {
      let o = h.object;
      while (o && !o.userData?.bodyName) o = o.parent;
      if (o && !o.userData.isBead && o.userData.role !== 'world') { name = o.userData.bodyName; break; }
    }
    if (name !== this._hoverName) { this._hoverName = name; if (!name) { this.hud.card.hidden = true; this.onHover(null); } }
    if (name) {
      const r = this.el.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      const card = this.hud.card;
      card.style.left = `${Math.min(r.width - 290, x + 16)}px`;
      card.style.top = `${Math.max(8, Math.min(r.height - 170, y + 16))}px`;
    }
    this._updateMeasure();
    this.dirty = true;
  }

  _bindPointer() {
    const dom = this.renderer.domElement;
    dom.addEventListener('pointerleave', () => { this._hoverName = null; this.hud.card.hidden = true; this._updateMeasure(); this.dirty = true; });
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
      } else if (ev.pointerType !== 'touch') this._hover(ev);
    });
    const end = () => {
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

  // Shortcuts when the viewer element has focus. Space and arrows stay with the host page
  // (the studio binds them); pass {keys: 'all'} handling via handleKey from a host if wanted.
  _bindKeys() {
    this.el.addEventListener('keydown', (ev) => { if (this.handleKey(ev, false)) { ev.preventDefault(); ev.stopPropagation(); } });
  }

  handleKey(ev, all = false) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return false;
    const k = ev.key;
    if (all && k === ' ') { this.toggle(); return true; }
    if (all && (k === 'ArrowRight' || k === 'ArrowLeft')) { this.pause(); this.setTime(this.t + (k === 'ArrowRight' ? 1 : -1) * (ev.shiftKey ? 5 : 1)); return true; }
    if (k === ',' || k === '<') { this.pause(); this.stepFrame(-1); return true; }
    if (k === '.' || k === '>') { this.pause(); this.stepFrame(1); return true; }
    if (k === '[') { this.seekEvent(-1); return true; }
    if (k === ']') { this.seekEvent(1); return true; }
    const views = { 1: 'follow', 2: '3d', 3: 'top', 4: 'side', 5: 'phone' };
    if (views[k]) { this._userMoved = true; this.setView(views[k]); this.onViewChange?.(views[k]); return true; }
    const o = this.getOptions();
    const toggles = { r: 'rules', g: 'ghost', p: 'trace', m: 'measure', l: 'labels', x: 'table' };
    const key = toggles[k.toLowerCase()];
    if (key) { this.setOptions({ [key]: !o[key] }); this.onOptionsChange?.(this.getOptions()); return true; }
    if (k === '?') { this.hud.help.hidden = !this.hud.help.hidden; return true; }
    if (k === 'Escape' && !this.hud.help.hidden) { this.hud.help.hidden = true; return true; }
    return false;
  }

  _trayPos(id) {
    const t = (this.sceneState?.trays || []).find((x) => x.id === id);
    return new THREE.Vector3(t ? t.pose.pos[0] : 0, t ? t.pose.pos[1] : 0, 0);
  }

  getState() {
    return { view: this.view, t: this.t, frame: this.frameIndex(), playing: this.playing, fps: this._fps.value, glide: !!this.glide.active,
      ribbons: (this.ribbons || []).map((r) => r.object), rules: { cones: this.rules.cones.length, volumes: this.rules.volumes.length, markers: this.rules.markers.length },
      ghostBodies: this.ghostLayer.bodies.size, capture: !!this.phone.view, spec: this.spec ? `${this.spec.spec_id}@v${this.spec.version}` : null, report: this.report ? `${this.report.rollout_id} (${this.report.status})` : null,
      options: this.getOptions(), verdictMode: this.verdictMode || null, fallback: null };
  }

  snapshotPNG() { this._render(); return this.renderer.domElement.toDataURL('image/png'); }
}
