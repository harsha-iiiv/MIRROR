// Timeline: phases per object, grasp/release events, the checker's violation intervals and the
// playhead on one canvas. Click or drag to scrub; frame-accurate stepping via the viewer API.
import { intervalsFor, events } from './verdict.js';
import { cssVar } from './look.js';

const PHASE_ORDER = ['approach', 'grasp', 'lift', 'transport', 'move', 'place', 'release', 'retreat'];

export class Timeline {
  constructor(container, viewer) {
    this.viewer = viewer;
    this.el = document.createElement('div');
    this.el.className = 'viz-timeline';
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('role', 'slider');
    this.canvas.setAttribute('aria-label', 'Timeline: phases, events and rule violations. Arrow keys step frames.');
    this.canvas.tabIndex = 0;
    this.tip = document.createElement('div');
    this.tip.className = 'viz-tl-tip';
    this.tip.hidden = true;
    this.el.append(this.canvas, this.tip);
    container.appendChild(this.el);
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.el);
    const seek = (ev) => {
      const r = this.canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (ev.clientX - r.left - this.pad) / (r.width - 2 * this.pad)));
      viewer.pause(); viewer.setTime(x * viewer.duration());
    };
    this.canvas.addEventListener('pointerdown', (ev) => { this.drag = true; this.canvas.setPointerCapture(ev.pointerId); seek(ev); });
    this.canvas.addEventListener('pointermove', (ev) => { if (this.drag) seek(ev); this._hover(ev); });
    this.canvas.addEventListener('pointerup', () => { this.drag = false; });
    this.canvas.addEventListener('pointerleave', () => { this.tip.hidden = true; });
    this.canvas.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { ev.preventDefault(); viewer.pause(); viewer.stepFrame(ev.key === 'ArrowRight' ? (ev.shiftKey ? 10 : 1) : (ev.shiftKey ? -10 : -1)); }
    });
    this.pad = 10;
    this.refresh();
  }

  refresh() {
    const p = this.viewer.payload;
    this.ev = events(p);
    this.ints = intervalsFor(this.viewer.report || p?.report, null);
    this.rows = [];
    if (p?.phase) {
      const objs = [...new Set(p.phase.map((ph) => (ph || '').split(':')[1]).filter(Boolean))];
      this.rows = objs.map((o) => {
        const segs = []; let cur = null;
        p.phase.forEach((ph, i) => {
          const [k, ob] = (ph || '').split(':');
          const key = ob === o ? k : '';
          if (!cur || cur.k !== key) { if (cur) cur.i1 = i; cur = { k: key, i0: i, i1: i }; segs.push(cur); }
        });
        if (cur) cur.i1 = p.phase.length - 1;
        return { obj: o, segs: segs.filter((s) => s.k) };
      });
    }
    this.draw();
  }

  draw() {
    const v = this.viewer, p = v.payload, c = this.canvas;
    const w = this.el.clientWidth || 300;
    const rowH = 9, gap = 3, top = 16;
    const h = top + Math.max(1, this.rows.length) * (rowH + gap) + 18;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    c.style.width = w + 'px'; c.style.height = h + 'px';
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const dur = v.duration();
    const ink = cssVar('--text', '#1d1d1f'), muted = cssVar('--muted', '#6b6b70'), faint = cssVar('--faint', '#b8b6b0');
    const accent = cssVar('--accent', '#2f54d9'), bad = cssVar('--bad', '#c8372d'), warn = cssVar('--warn', '#b7791f');
    g.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    if (!p || !dur) { g.fillStyle = muted; g.fillText('no rollout', this.pad, 14); return; }
    const X = (t) => this.pad + (t / dur) * (w - 2 * this.pad);
    // seconds ruler
    g.strokeStyle = faint; g.fillStyle = muted; g.lineWidth = 1;
    const step = dur > 40 ? 10 : dur > 12 ? 5 : 1;
    for (let s = 0; s <= dur + 1e-9; s += step) { g.beginPath(); g.moveTo(X(s), 10); g.lineTo(X(s), 14); g.stroke(); g.fillText(`${s}s`, X(s) + 2, 9); }
    // phase rows
    const shade = (k) => { const i = PHASE_ORDER.indexOf(k); return i < 0 ? 0.25 : 0.18 + 0.1 * (i % 4); };
    this.rows.forEach((r, ri) => {
      const y = top + ri * (rowH + gap);
      g.fillStyle = faint; g.globalAlpha = 0.35; g.fillRect(X(0), y, X(dur) - X(0), rowH); g.globalAlpha = 1;
      for (const s of r.segs) {
        const carry = /grasp|lift|transport|place/.test(s.k);
        g.fillStyle = carry ? accent : ink; g.globalAlpha = carry ? 0.55 : shade(s.k);
        g.fillRect(X(p.t[s.i0]), y, Math.max(1, X(p.t[s.i1]) - X(p.t[s.i0])), rowH);
      }
      g.globalAlpha = 1;
      g.fillStyle = muted; g.fillText(r.obj, X(dur) - g.measureText(r.obj).width, y + rowH - 1);
    });
    const yb = top + Math.max(1, this.rows.length) * (rowH + gap);
    // violations
    for (const x of this.ints) {
      g.fillStyle = x.kind === 'advisory' ? warn : bad;
      g.fillRect(X(x.t_start), yb, Math.max(2, X(x.t_end) - X(x.t_start)), 4);
    }
    // events
    for (const e of this.ev) {
      g.strokeStyle = e.type === 'grasp' ? ink : muted; g.lineWidth = 1.2;
      g.beginPath(); g.moveTo(X(e.t), top - 2); g.lineTo(X(e.t), yb + 6); g.stroke();
    }
    // playhead
    g.strokeStyle = accent; g.lineWidth = 2;
    g.beginPath(); g.moveTo(X(v.t), 2); g.lineTo(X(v.t), h - 2); g.stroke();
    g.fillStyle = ink;
    const lab = `${v.t.toFixed(3)} s · frame ${v.frameIndex()} / ${p.t.length - 1}`;
    g.fillText(lab, this.pad, h - 3);
    this.canvas.setAttribute('aria-valuetext', lab);
  }

  _hover(ev) {
    const v = this.viewer, p = v.payload;
    if (!p) return;
    const r = this.canvas.getBoundingClientRect();
    const x = (ev.clientX - r.left - this.pad) / (r.width - 2 * this.pad);
    const t = x * v.duration();
    const near = [...this.ints.filter((i) => t >= i.t_start && t <= i.t_end).map((i) => `${i.constraint_id} ${i.kind} · peak ${i.peak.toFixed(2)} · ${i.t_start.toFixed(2)}–${i.t_end.toFixed(2)} s`),
      ...this.ev.filter((e) => Math.abs(e.t - t) < v.duration() / 150).map((e) => `${e.type} ${e.object_id} at ${e.t.toFixed(2)} s`)];
    this.tip.hidden = !near.length;
    if (near.length) { this.tip.textContent = near.join(' · '); this.tip.style.left = `${Math.min(r.width - 220, Math.max(0, ev.clientX - r.left - 60))}px`; }
  }

  dispose() { this.ro.disconnect(); this.el.remove(); }
}
