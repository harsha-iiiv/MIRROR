// Top-down 2D view used when WebGL is unavailable. Same public surface as Viewer for what the
// studio calls; 3D-only features are no-ops. Draws the table, obstacles, trays, objects at the
// current playback pose and the traced object's path coloured by the checker's verdict.
import { OBJECT_COLORS } from './palette.js';
import { sceneBoxes, tiltOf } from './geom.js';
import { intervalsFor, verdictAt } from './verdict.js';
import { cssVar } from './look.js';

export class Fallback2D {
  constructor(el, opts = {}) {
    this.el = el; this.onTick = opts.onTick || (() => {}); this.onEdit = opts.onEdit || (() => {});
    this.fallback = '2d'; this.tol = opts.tol ?? 15;
    this.canvas = document.createElement('canvas');
    Object.assign(this.canvas.style, { width: '100%', height: '100%', display: 'block' });
    el.appendChild(this.canvas);
    const note = document.createElement('div');
    note.className = 'viz-fallback-note';
    note.textContent = '3D unavailable in this browser (no WebGL): top-down 2D view.';
    el.appendChild(note);
    this.payload = null; this.ghost = null; this.t = 0; this.playing = false; this.speed = 1; this.view = 'top';
    this.showTrace = true; this.hasUnobservedTilt = false; this.traceObject = null; this.live = false;
    this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(el);
    let last = performance.now();
    const loop = (now) => {
      this._raf = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      if (this.playing && this.payload) {
        this.t = Math.min(this.duration(), this.t + dt * this.speed);
        if (this.t >= this.duration()) this.playing = false;
        this.onTick(this.t, this.playing); this.draw();
      }
    };
    this._raf = requestAnimationFrame(loop);
  }
  dispose() { cancelAnimationFrame(this._raf); this.ro.disconnect(); this.el.innerHTML = ''; }
  resize() { this.draw(); }
  applyTheme() { this.draw(); }
  setView(k) { this.view = k; }
  setEditMode() {}
  setOptions(o = {}) { if (o.trace !== undefined) this.showTrace = o.trace; this.draw(); }
  getOptions() { return { trace: this.showTrace }; }
  getState() { return { view: 'top', t: this.t, frame: this.frameIndex(), playing: this.playing, fallback: '2d' }; }
  handleKey() { return false; } measure() { return null; } events() { return []; } seekEvent() {}
  setSpecs() {} setSpec() {} setCapture() { return Promise.resolve(); } mountTimeline() { return null; }
  endLive() { this.live = false; }
  beadsOffTable() { return 0; }
  snapshotPNG() { return this.canvas.toDataURL('image/png'); }
  showScene(visual, scene) { this.payload = null; this.scene = scene; this.frame = null; this.draw(); }
  showLive(visual, scene, frame) { this.live = true; this.scene = scene; this.frame = frame?.bodies || null; this.draw(); }
  applyBodies(b) { this.frame = b; this.draw(); }
  loadRollout(payload, { ghost = null, sceneState = null, traceObject = null, report = null } = {}) {
    this.payload = payload; this.report = report || payload.report; this.ghost = ghost; this.scene = sceneState || payload.scene || this.scene;
    const cup = (this.scene?.objects || []).find((o) => o.kind === 'cup');
    this.traceObject = traceObject || (cup && payload.bodies?.[cup.id] ? cup.id : null);
    this.t = 0; this.draw();
  }
  duration() { const t = this.payload?.t; return t?.length ? t[t.length - 1] : 0; }
  frameIndex() { const ts = this.payload?.t || []; let i = 0; while (i + 1 < ts.length && ts[i + 1] <= this.t) i++; return i; }
  setTime(t) { this.t = Math.max(0, Math.min(this.duration(), t)); this.onTick(this.t, this.playing); this.draw(); }
  stepFrame(n) { const ts = this.payload?.t || []; const i = Math.max(0, Math.min(ts.length - 1, this.frameIndex() + n)); this.setTime(ts[i] ?? 0); }
  play() { if (!this.payload) return; if (this.t >= this.duration() - 1e-6) this.t = 0; this.playing = true; this.onTick(this.t, true); }
  pause() { this.playing = false; this.onTick(this.t, false); }
  toggle() { this.playing ? this.pause() : this.play(); }

  draw() {
    const c = this.canvas, w = this.el.clientWidth || 300, h = this.el.clientHeight || 200;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = cssVar('--viewer', '#eceae6'); g.fillRect(0, 0, w, h);
    const s = Math.min(w / 1.0, h / 0.85);
    const X = (x) => w / 2 + x * s, Y = (y) => h / 2 - y * s;
    g.fillStyle = cssVar('--bg', '#f4f3f0'); g.fillRect(X(-0.45), Y(0.4), 0.9 * s, 0.8 * s);
    const sc = this.scene;
    if (!sc) return;
    g.strokeStyle = OBJECT_COLORS.obstacle; g.fillStyle = 'rgba(178,95,217,0.35)';
    for (const b of sceneBoxes(sc)) this._rect(g, X, Y, b.center, b.half, b.quat, s);
    for (const t of sc.trays || []) { g.strokeStyle = OBJECT_COLORS.tray; g.strokeRect(X(t.pose.pos[0] - t.inner_half[0]), Y(t.pose.pos[1] + t.inner_half[1]), 2 * t.inner_half[0] * s, 2 * t.inner_half[1] * s); }
    const p = this.payload, i = p ? this.frameIndex() : 0;
    if (p && this.showTrace && this.traceObject) {
      const arr = p.bodies[this.traceObject], ints = intervalsFor(this.report, this.traceObject);
      for (let k = 1; k <= i; k++) {
        const a = arr[k - 1], b = arr[k];
        if (!a || !b) continue;
        const v = verdictAt(ints, p.t[k]);
        g.strokeStyle = v === 'violated' ? cssVar('--bad', '#c8372d') : v === 'advisory' ? cssVar('--warn', '#b7791f') : cssVar('--accent', '#2f54d9');
        g.lineWidth = 2; g.beginPath(); g.moveTo(X(a[0]), Y(a[1])); g.lineTo(X(b[0]), Y(b[1])); g.stroke();
      }
    }
    for (const o of sc.objects || []) {
      const st = p?.bodies?.[o.id]?.[i] || this.frame?.[o.id] || [...o.pose.pos, ...o.pose.quat];
      const half = o.kind === 'cup' ? o.size.outer_half : o.kind === 'block' ? o.size.half : [o.size.radius, o.size.radius, o.size.half_length];
      const over = o.kind === 'cup' && tiltOf(st.slice(3, 7)) > this.tol;
      g.fillStyle = OBJECT_COLORS[o.kind] || cssVar('--text', '#333');
      g.strokeStyle = over ? cssVar('--bad', '#c8372d') : cssVar('--text', '#333');
      g.lineWidth = over ? 2.5 : 1;
      this._rect(g, X, Y, st.slice(0, 3), half, st.slice(3, 7), s);
    }
  }

  _rect(g, X, Y, c, half, q, s) {
    const yaw = Math.atan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] * q[2] + q[3] * q[3]));
    g.save(); g.translate(X(c[0]), Y(c[1])); g.rotate(-yaw);
    g.fillRect(-half[0] * s, -half[1] * s, 2 * half[0] * s, 2 * half[1] * s);
    g.strokeRect(-half[0] * s, -half[1] * s, 2 * half[0] * s, 2 * half[1] * s);
    g.restore();
  }
}
