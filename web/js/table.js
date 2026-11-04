// "Your table": a real phone capture as a guided story.
// Video with tracked outlines -> moments -> confirmations -> the task (or the gate's refusal) ->
// the robot run on your layout -> one correction -> before/after with the re-check table.
// Data: data/table/index.json and data/table/<id>/table.json, built by mirror/studio/real_table.py.
import { h } from 'preact';
import { useState, useEffect, useRef, useMemo, useCallback } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { MicButton } from './media.js';

const html = htm.bind(h);
const REDUCED = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt = (x, d = 1) => (x == null || Number.isNaN(+x) ? '–' : (+x).toFixed(d));
const fmtT = (t) => { if (t == null) return '–'; const m = Math.floor(t / 60); const s = t - 60 * m; return `${m}:${s.toFixed(1).padStart(4, '0')}`; };
const human = (s) => String(s || '').replace(/_/g, ' ');
const easeOut = (x) => 1 - Math.pow(1 - x, 3);

export const TABLE_STEPS = [
  { key: 'watch', n: 1, title: 'What MIRROR saw', short: 'Watch' },
  { key: 'moments', n: 2, title: 'Moments in your video', short: 'Moments' },
  { key: 'confirm', n: 3, title: 'What you confirmed', short: 'Confirm' },
  { key: 'task', n: 4, title: 'The task', short: 'Task' },
  { key: 'robot', n: 5, title: 'The robot on your layout', short: 'Robot' },
  { key: 'correct', n: 6, title: 'One correction', short: 'Correct' },
  { key: 'compare', n: 7, title: 'What the correction changed', short: 'Compare' },
];
const VIDEO_STEPS = new Set(['watch', 'moments', 'confirm', 'task']);

// ------------------------------------------------------------------ data
function useTable() {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    let gone = false;
    (async () => {
      try {
        const want = new URLSearchParams(location.search).get('table');
        const idx = await api.getDataFile('table/index.json');
        const rows = idx.tables || [];
        const row = rows.find((r) => r.id === want) || rows[0];
        if (!row) throw new Error('no capture is bundled with this site');
        const dir = 'table/' + row.path.replace(/table\.json$/, '');
        const t = await api.getDataFile(dir + 'table.json');
        const contours = t.contours ? await api.getDataFile(dir + t.contours).catch(() => null) : null;
        if (!gone) setState({ loading: false, table: t, contours, dir, rows });
      } catch (e) { if (!gone) setState({ loading: false, error: String(e.message || e) }); }
    })();
    return () => { gone = true; };
  }, []);
  return state;
}
const urlOf = (dir, rel) => (rel ? (api.config.base || 'data/') + dir + rel : '');

const rolloutCache = new Map();
function loadRollout(dir, rel) {
  // live/<name>: a run made by this server for a correction you typed or spoke (session-scoped API).
  if (rel && rel.startsWith('live/')) {
    const key = 'api:' + dir + rel;
    if (!rolloutCache.has(key)) rolloutCache.set(key, api.getJSONApi(`table/${encodeURIComponent(dir.split('/').filter(Boolean).pop())}/live/${encodeURIComponent(rel.slice(5))}`));
    return rolloutCache.get(key);
  }
  const u = urlOf(dir, rel);
  if (!rolloutCache.has(u)) rolloutCache.set(u, fetch(u).then((r) => { if (!r.ok) throw new Error(rel + ': ' + r.status); return r.json(); }));
  return rolloutCache.get(u);
}

// Carry windows per tracked object: from each pick to the next place.
function carries(events) {
  const out = {};
  const open = {};
  for (const e of [...events].sort((a, b) => a.t - b.t)) {
    if (e.type === 'pick') open[e.object] = e;
    else if (e.type === 'place' && open[e.object]) {
      (out[e.object] = out[e.object] || []).push({ from: open[e.object].t, to: e.t, pick: open[e.object], place: e,
        person: !!(open[e.object].person || e.person) || open[e.object].status === 'person_corrected' });
      delete open[e.object];
    }
  }
  return out;
}

// ------------------------------------------------------------------ video + outlines
function polyAt(obj, t) {
  if (!obj) return null;
  if (obj.static) return obj.poly[0];
  const step = obj.t.length > 1 ? obj.t[1] - obj.t[0] : 0.1;
  const i = Math.max(0, Math.min(obj.t.length - 1, Math.round(t / step)));
  return obj.poly[i];
}

function TakePlayer({ table, contours, dir, t, setT, playing, setPlaying, focus, setFocus, seekReq, carry }) {
  const vid = useRef(null);
  const cvs = useRef(null);
  const glow = useRef({});           // label -> current emphasis 0..1 (eased toward its target)
  const colors = useMemo(() => Object.fromEntries(table.objects.map((o) => [o.label, o.color])), [table]);
  const [outlines, setOutlines] = useState(true);

  useEffect(() => { const v = vid.current; if (v && seekReq && Math.abs(v.currentTime - seekReq.t) > 0.02) v.currentTime = seekReq.t; }, [seekReq]);
  useEffect(() => { const v = vid.current; if (!v) return; if (playing && v.paused) v.play().catch(() => setPlaying(false)); if (!playing && !v.paused) v.pause(); }, [playing]);

  useEffect(() => {
    let raf = 0, last = performance.now();
    const draw = (now) => {
      raf = requestAnimationFrame(draw);
      const v = vid.current, c = cvs.current;
      if (!v || !c) return;
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      const r = v.getBoundingClientRect();
      const dpr = Math.min(2, devicePixelRatio || 1);
      if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
      const g = c.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, r.width, r.height);
      const now_t = v.currentTime;
      if (Math.abs(now_t - t) > 0.04) setT(now_t);
      if (!outlines || !contours) return;
      for (const [label, obj] of Object.entries(contours.objects)) {
        const moving = (carry[label] || []).some((w) => now_t >= w.from - 0.15 && now_t <= w.to + 0.15);
        const target = focus === label ? 1 : moving ? 0.85 : focus ? 0.12 : 0.42;
        const cur = glow.current[label] ?? target;
        const k = REDUCED ? 1 : 1 - Math.exp(-dt * 9);
        const e = glow.current[label] = cur + (target - cur) * k;
        const p = polyAt(obj, now_t);
        if (!p || p.length < 6) continue;
        const col = colors[label] || '#888';
        g.beginPath();
        for (let i = 0; i < p.length; i += 2) { const x = p[i] * r.width, y = p[i + 1] * r.height; i ? g.lineTo(x, y) : g.moveTo(x, y); }
        g.closePath();
        g.globalAlpha = 0.06 + 0.26 * e; g.fillStyle = col; g.fill();
        g.globalAlpha = 0.5 + 0.5 * e; g.lineWidth = 1.4 + 1.4 * e; g.strokeStyle = col;
        if (obj.static || label === 'tray') g.setLineDash([5, 4]); else g.setLineDash([]);
        g.stroke(); g.setLineDash([]);
        if (e > 0.5) {               // label tag at the outline's top
          let mx = 0, my = 1e9; for (let i = 0; i < p.length; i += 2) { if (p[i + 1] < my) { my = p[i + 1]; mx = p[i]; } }
          const name = human(label);
          g.font = '600 11px ui-sans-serif, -apple-system, Inter, sans-serif';
          const w = g.measureText(name).width + 10;
          const x = Math.max(2, Math.min(r.width - w - 2, mx * r.width - w / 2)), y = Math.max(2, my * r.height - 22);
          g.globalAlpha = Math.min(1, (e - 0.5) * 2.4);
          g.fillStyle = col; g.beginPath(); g.roundRect ? g.roundRect(x, y, w, 17, 8.5) : g.rect(x, y, w, 17); g.fill();
          g.fillStyle = '#fff'; g.fillText(name, x + 5, y + 12.5);
        }
      }
      g.globalAlpha = 1;
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [contours, focus, outlines, carry, colors, t]);

  const v = table.video;
  return html`<div class="yt-take">
    <div class="yt-frame" style=${{ aspectRatio: v ? `${v.width} / ${v.height}` : '9 / 16' }}>
      <video ref=${vid} src=${urlOf(dir, v?.file)} muted playsinline preload="auto"
        onPlay=${() => setPlaying(true)} onPause=${() => setPlaying(false)} onEnded=${() => setPlaying(false)}
        aria-label="Your phone video"></video>
      <canvas ref=${cvs} aria-hidden="true"></canvas>
      <div class="yt-src">Your phone · ${v ? `${v.width}×${v.height} · ${fmt(v.fps, 0)} fps` : ''}</div>
    </div>
    <div class="yt-legend" role="list">
      ${table.objects.map((o) => html`<button role="listitem" class=${'yt-chip' + (focus === o.label ? ' on' : '')}
        onMouseEnter=${() => setFocus(o.label)} onMouseLeave=${() => setFocus(null)} onFocus=${() => setFocus(o.label)} onBlur=${() => setFocus(null)}
        onClick=${() => { const w = (carry[o.label] || [])[0]; if (w) setT(w.from - 0.6, true); }}
        title=${o.identity}><i style=${{ background: o.color }}></i>${o.name}</button>`)}
      <label class="yt-toggle"><input type="checkbox" checked=${outlines} onChange=${(e) => setOutlines(e.target.checked)} /> Outlines</label>
    </div>
  </div>`;
}

// ------------------------------------------------------------------ timeline (video)
function Ticks({ dur, every = 5 }) {
  const n = Math.floor(dur / every);
  return html`<div class="yt-ticks" aria-hidden="true">${Array.from({ length: n + 1 }, (_, i) => html`<span style=${{ left: `${(100 * i * every) / dur}%` }}>${i * every}s</span>`)}</div>`;
}

function Scrub({ dur, t, onSeek, children, label }) {
  const el = useRef(null);
  const drag = useRef(false);
  const at = (e) => { const r = el.current.getBoundingClientRect(); const lab = parseFloat(getComputedStyle(el.current).getPropertyValue('--lab')) || 0; return Math.max(0, Math.min(dur, ((e.clientX - r.left - lab) / Math.max(1, r.width - lab)) * dur)); };
  return html`<div class="yt-scrub" ref=${el} role="slider" tabindex="0" aria-label=${label} aria-valuemin="0" aria-valuemax=${dur} aria-valuenow=${t.toFixed(1)}
    onPointerDown=${(e) => { drag.current = true; e.currentTarget.setPointerCapture(e.pointerId); onSeek(at(e)); }}
    onPointerMove=${(e) => drag.current && onSeek(at(e))} onPointerUp=${() => { drag.current = false; }}
    onKeyDown=${(e) => { if (e.key === 'ArrowRight') { onSeek(Math.min(dur, t + (e.shiftKey ? 5 : 1))); e.preventDefault(); } if (e.key === 'ArrowLeft') { onSeek(Math.max(0, t - (e.shiftKey ? 5 : 1))); e.preventDefault(); } }}>
    ${children}
    <div class="yt-head" style=${{ left: `calc(var(--lab) + (100% - var(--lab)) * ${(t / (dur || 1)).toFixed(5)})` }}></div>
  </div>`;
}

function EventLanes({ table, carry, t, onSeek, focus, setFocus }) {
  const dur = table.video?.duration_s || 50;
  const objs = table.objects.filter((o) => o.proxy !== 'wall');
  return html`<div class="yt-lanes">
    <${Scrub} dur=${dur} t=${t} onSeek=${onSeek} label="Video position">
      ${objs.map((o) => html`<div class=${'yt-lane' + (focus && focus !== o.label ? ' dim' : '')} onMouseEnter=${() => setFocus(o.label)} onMouseLeave=${() => setFocus(null)}>
        <span class="yt-lname"><i style=${{ background: o.color }}></i>${o.name}</span>
        <div class="yt-track">
          ${(carry[o.label] || []).map((w) => html`<div class=${'yt-carry' + (w.person ? ' person' : '')} style=${{ left: `${(100 * w.from) / dur}%`, width: `${Math.max(0.4, (100 * (w.to - w.from)) / dur)}%`, '--c': o.color }}
            title=${`${o.name}: picked ${fmtT(w.from)}, placed ${fmtT(w.to)} in ${human(w.place.placed_in) || '?'}${w.person ? ' · confirmed by you' : ' · inferred from motion'}`}></div>`)}
          ${table.events.filter((e) => e.object === o.label).map((e) => html`<div class=${'yt-pin ' + e.type} style=${{ left: `${(100 * e.t) / dur}%`, '--c': o.color }}></div>`)}
        </div></div>`)}
    </${Scrub}>
    <${Ticks} dur=${dur} />
  </div>`;
}

// ------------------------------------------------------------------ charts (SVG, inspectable)
function useWidth(ref) {
  const [w, setW] = useState(600);
  useEffect(() => { if (!ref.current) return; const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width))); ro.observe(ref.current); return () => ro.disconnect(); }, []);
  return w;
}

function LineChart({ title, unit, lines, threshold, thresholdLabel, t, onSeek, height = 150, yMin, yMax, badAbove = false, digits = 1, note }) {
  const box = useRef(null);
  const W = useWidth(box);
  const [hover, setHover] = useState(null);
  const [drawn, setDrawn] = useState(REDUCED ? 1 : 0);
  const key = lines.map((l) => l.name + l.y.length).join('|');
  useEffect(() => {
    if (REDUCED) return;
    let raf, t0 = performance.now();
    const tick = (now) => { const k = Math.min(1, (now - t0) / 700); setDrawn(easeOut(k)); if (k < 1) raf = requestAnimationFrame(tick); };
    setDrawn(0); raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [key]);
  const pad = { l: 38, r: 10, t: 10, b: 20 };
  const all = lines.flatMap((l) => l.y.filter((v) => v != null));
  const tMax = Math.max(...lines.map((l) => l.t[l.t.length - 1] || 0), 1);
  let lo = yMin ?? Math.min(...all, threshold ?? Infinity), hi = yMax ?? Math.max(...all, threshold ?? -Infinity);
  if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-6) hi = lo + 1;
  const span = hi - lo; if (yMin == null) lo -= span * 0.06; if (yMax == null) hi += span * 0.08;
  const X = (x) => pad.l + ((W - pad.l - pad.r) * x) / tMax;
  const Y = (y) => pad.t + (height - pad.t - pad.b) * (1 - (Math.min(hi, Math.max(lo, y)) - lo) / (hi - lo));
  const ticks = (() => { const n = 4, step = (hi - lo) / n; return Array.from({ length: n + 1 }, (_, i) => lo + i * step); })();
  const path = (l) => { let d = ''; const n = Math.max(2, Math.floor(l.y.length * drawn)); for (let i = 0; i < n; i++) { if (l.y[i] == null) continue; d += (d ? 'L' : 'M') + X(l.t[i]).toFixed(1) + ',' + Y(l.y[i]).toFixed(1); } return d; };
  const at = (l, tt) => { let b = 0; for (let i = 0; i < l.t.length; i++) if (Math.abs(l.t[i] - tt) < Math.abs(l.t[b] - tt)) b = i; return l.y[b]; };
  const move = (e) => { const r = box.current.getBoundingClientRect(); const x = e.clientX - r.left; setHover(Math.max(0, Math.min(tMax, ((x - pad.l) / (W - pad.l - pad.r)) * tMax))); };
  const ht = hover ?? t;
  return html`<figure class="yt-chart" ref=${box}>
    <figcaption><span>${title}</span><span class="num faint">${lines.map((l) => html`<span class="yt-read"><i style=${{ background: l.color }}></i>${l.name} ${fmt(at(l, ht), digits)} ${unit}</span>`)} <span class="yt-read">t ${fmt(ht, 1)} s</span></span></figcaption>
    <svg width=${W} height=${height} role="img" aria-label=${title}
      onPointerMove=${move} onPointerLeave=${() => setHover(null)} onClick=${() => hover != null && onSeek && onSeek(hover)}>
      ${ticks.map((v) => html`<g><line x1=${pad.l} x2=${W - pad.r} y1=${Y(v)} y2=${Y(v)} class="grid" /><text x=${pad.l - 6} y=${Y(v) + 3.5} class="axis" text-anchor="end">${v.toFixed(Math.abs(hi - lo) < 5 ? 1 : 0)}</text></g>`)}
      ${threshold != null ? html`<g><rect x=${pad.l} y=${badAbove ? pad.t : Y(threshold)} width=${W - pad.l - pad.r} height=${Math.max(0, badAbove ? Y(threshold) - pad.t : height - pad.b - Y(threshold))} class="bad-zone" />
        <line x1=${pad.l} x2=${W - pad.r} y1=${Y(threshold)} y2=${Y(threshold)} class="thr" /><text x=${W - pad.r - 4} y=${Y(threshold) - 4} text-anchor="end" class="thr-t">${thresholdLabel}</text></g>` : ''}
      ${lines.map((l) => html`<path d=${path(l)} class="ln" style=${{ stroke: l.color, strokeDasharray: l.dash ? '4 3' : null }} />`)}
      ${t != null ? html`<line x1=${X(t)} x2=${X(t)} y1=${pad.t} y2=${height - pad.b} class="now" />` : ''}
      ${hover != null ? html`<line x1=${X(hover)} x2=${X(hover)} y1=${pad.t} y2=${height - pad.b} class="hov" />` : ''}
      <text x=${pad.l} y=${height - 5} class="axis">0 s</text><text x=${W - pad.r} y=${height - 5} class="axis" text-anchor="end">${fmt(tMax, 0)} s</text>
    </svg>
    ${note ? html`<p class="yt-note">${note}</p>` : ''}
  </figure>`;
}


// ------------------------------------------------------------------ plan view (simulator frame, metres)
function quatYaw(q) { const [w, x, y, z] = q; return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)); }

function PlanView({ table, run, ghost, t, clearance }) {
  const carryW = useMemo(() => carries(table.events), [table]);
  const box = useRef(null);
  const W = useWidth(box);
  const [hover, setHover] = useState(null);
  const [hands, setHands] = useState(true);
  const scene = table.robot?.scene, plan = table.robot?.plan;
  if (!scene) return '';
  const xr = [-0.48, 0.48], yr = [-0.36, 0.30];
  const H = Math.round(W * (yr[1] - yr[0]) / (xr[1] - xr[0]));
  const sx = (x) => ((x - xr[0]) / (xr[1] - xr[0])) * W;
  const sy = (y) => H - ((y - yr[0]) / (yr[1] - yr[0])) * H;
  const k = W / (xr[1] - xr[0]);           // px per metre
  const kindLabel = Object.fromEntries(table.objects.map((o) => [o.proxy, o]));
  const col = (id) => (kindLabel[(scene.objects.find((o) => o.id === id) || {}).kind] || {}).color || 'var(--muted)';
  const pathD = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + sx(p[0]).toFixed(1) + ',' + sy(p[1]).toFixed(1)).join('');
  const idx = (n) => Math.max(0, Math.min(n - 1, Math.round(t * 5)));
  const wall = scene.obstacles?.[0];
  const tray = scene.trays?.[0];
  const rectAt = (cx, cy, hx, hy, yaw, cls, extra = {}) => html`<rect x=${-hx * k} y=${-hy * k} width=${2 * hx * k} height=${2 * hy * k} class=${cls}
    transform=${`translate(${sx(cx).toFixed(1)} ${sy(cy).toFixed(1)}) rotate(${(-yaw * 180) / Math.PI})`} ...${extra} />`;
  const move = (e) => { const r = box.current.getBoundingClientRect(); const x = xr[0] + ((e.clientX - r.left) / W) * (xr[1] - xr[0]); const y = yr[0] + (1 - (e.clientY - r.top) / H) * (yr[1] - yr[0]); setHover([x, y]); };
  const cam = plan?.camera;
  return html`<figure class="yt-plan" ref=${box}>
    <figcaption><span>From above: your hands and the robot</span>
      <span class="num faint">${hover ? `x ${fmt(hover[0] * 100, 1)} cm · y ${fmt(hover[1] * 100, 1)} cm` : 'simulator frame'}</span></figcaption>
    <svg width=${W} height=${H} onPointerMove=${move} onPointerLeave=${() => setHover(null)} role="img" aria-label="Top view of the table">
      <defs><pattern id="ytgrid" width=${k * 0.05} height=${k * 0.05} patternUnits="userSpaceOnUse"><path d=${`M ${k * 0.05} 0 L 0 0 0 ${k * 0.05}`} class="g5" /></pattern></defs>
      <rect x=${sx(-0.45)} y=${sy(0.4)} width=${0.9 * k} height=${0.8 * k} class="ws" />
      <rect x="0" y="0" width=${W} height=${H} fill="url(#ytgrid)" />
      ${tray ? rectAt(tray.pose.pos[0], tray.pose.pos[1], tray.inner_half[0], tray.inner_half[1], quatYaw(tray.pose.quat), 'tray') : ''}
      ${wall && clearance ? html`<g transform=${`translate(${sx(wall.pose.pos[0]).toFixed(1)} ${sy(wall.pose.pos[1]).toFixed(1)}) rotate(${(-quatYaw(wall.pose.quat) * 180) / Math.PI})`}>
        <rect x=${-(wall.thickness / 2 + clearance) * k} y=${-(wall.length / 2 + clearance) * k} width=${(wall.thickness + 2 * clearance) * k} height=${(wall.length + 2 * clearance) * k} rx=${clearance * k} class="band" /></g>` : ''}
      ${wall ? rectAt(wall.pose.pos[0], wall.pose.pos[1], wall.thickness / 2, wall.length / 2, quatYaw(wall.pose.quat), 'wall') : ''}
      ${hands && plan ? Object.entries(plan.real).filter(([l]) => !['tray', 'divider'].includes(l)).flatMap(([l, r]) => {
        const o = table.objects.find((x) => x.label === l);
        // Only while carried (the confirmed or inferred carries), so resting jitter is not drawn as motion.
        return (carryW[l] || []).filter((w) => w.to - w.from > 0.8).map((w) => html`<path d=${pathD(r.path.filter((p) => p[0] >= w.from - 0.1 && p[0] <= w.to + 0.1).map((p) => [p[1], p[2]]))} class="hand" style=${{ stroke: o?.color }} />`); }) : ''}
      ${ghost ? Object.entries(ghost.paths || {}).map(([id, pts]) => html`<path d=${pathD(pts)} class="ghost" />`) : ''}
      ${Object.entries(run?.paths || {}).map(([id, pts]) => html`<path d=${pathD(pts.slice(0, idx(pts.length) + 1))} class="robot" style=${{ stroke: col(id) }} />
        <path d=${pathD(pts)} class="robot-future" style=${{ stroke: col(id) }} />`)}
      ${scene.objects.map((o) => html`<circle cx=${sx(o.pose.pos[0])} cy=${sy(o.pose.pos[1])} r="3.5" class="start" style=${{ stroke: col(o.id) }} />`)}
      ${Object.entries(run?.paths || {}).map(([id, pts]) => { const p = pts[idx(pts.length)]; return p ? html`<circle cx=${sx(p[0])} cy=${sy(p[1])} r=${4 + Math.min(6, p[2] * 30)} class="now" style=${{ fill: col(id) }} />` : ''; })}
      ${cam ? html`<g class="cam" transform=${`translate(${sx(cam.xy[0]).toFixed(1)} ${sy(cam.xy[1]).toFixed(1)}) rotate(${(-Math.atan2(cam.look_xy[1], cam.look_xy[0]) * 180) / Math.PI})`}>
        <path d="M0,-7 L14,-11 L14,11 L0,7 Z" /><circle r="2.5" /></g>
        <text x=${Math.min(W - 4, sx(cam.xy[0]) + 6)} y=${sy(cam.xy[1]) + 22} class="lbl" text-anchor="end">your phone · ${fmt(cam.height_m * 100, 0)} cm up</text>` : ''}
      <g class="scale" transform=${`translate(${12} ${H - 12})`}><line x1="0" x2=${0.1 * k} y1="0" y2="0" /><text x=${0.05 * k} y="-5" text-anchor="middle">10 cm</text></g>
    </svg>
    <div class="yt-plan-key">
      <label><input type="checkbox" checked=${hands} onChange=${(e) => setHands(e.target.checked)} /> <i class="hand-k"></i> your hands (from the video, approx.)</label>
      <span><i class="robot-k"></i> robot${ghost ? html` · <i class="ghost-k"></i> before` : ''}</span>
      ${clearance ? html`<span><i class="band-k"></i> ${fmt(clearance * 100, 0)} cm clearance</span>` : ''}
    </div>
  </figure>`;
}

// ------------------------------------------------------------------ 3D stage (studio Viewer API)
function RobotStage({ dir, run, ghost, t, setT, playing, setPlaying, seekReq, label, capture }) {
  const el = useRef(null);
  const vref = useRef(null);
  const [err, setErr] = useState(null);
  const [view, setView] = useState('3d');
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let gone = false, viewer = null;
    (async () => {
      try {
        const { Viewer } = await import('./viewer.js');
        if (gone) return;
        viewer = new Viewer(el.current, { tol: 15, onTick: (tt, pl) => { setT(tt); if (!pl) setPlaying(false); } });
        vref.current = viewer;
        setReady(true);
      } catch (e) { setErr('3D view unavailable here (' + (e.message || e) + '). The numbers and charts below are complete.'); }
    })();
    return () => { gone = true; try { viewer && viewer.dispose(); } catch (_) { /* nothing */ } vref.current = null; };
  }, []);
  useEffect(() => {
    const v = vref.current; if (!v || !run) return;
    let gone = false;
    (async () => {
      try {
        const p = await loadRollout(dir, run.file);
        const g = ghost ? await loadRollout(dir, ghost.file) : null;
        if (gone) return;
        v.loadRollout(p, { ghost: g, sceneState: p.scene, spec: p.spec || undefined });
        // Newer viewers can show the phone that filmed the table (feature-detected; optional data).
        if (capture && v.setCapture && !v.__ytCapture) { try { v.setCapture(capture.view, capture.base); v.__ytCapture = true; } catch (_) { /* optional */ } }
        if (v.setOptions) v.setOptions({ labels: true, trace: true, ghost: !!g });
        v.setTime(0); setT(0);
      } catch (e) { setErr(String(e.message || e)); }
    })();
    return () => { gone = true; };
  }, [ready, run?.file, ghost?.file]);
  useEffect(() => { const v = vref.current; if (v) playing ? v.play() : v.pause(); }, [playing, ready]);
  useEffect(() => { const v = vref.current; if (v && seekReq) v.setTime(seekReq.t); }, [seekReq]);
  useEffect(() => { const v = vref.current; if (v && v.setView) v.setView(view); }, [view, ready]);
  return html`<div class="yt-3d">
    <div class="yt-3d-canvas" ref=${el} tabindex="0" aria-label="3D view of the simulated run. Drag to orbit, scroll to zoom."></div>
    ${err ? html`<p class="yt-3d-err">${err}</p>` : ''}
    <div class="yt-3d-hud"><span>${label}</span>
      <div class="seg" role="group" aria-label="Camera">${[['3d', '3D'], ['top', 'Top'], ['follow', 'Follow'], ...(capture ? [['phone', 'Phone']] : [])].map(([k, n]) => html`<button aria-pressed=${view === k} class="r" onClick=${() => setView(k)}>${n}</button>`)}</div></div>
  </div>`;
}

// ------------------------------------------------------------------ step bodies
function Labels({ table }) {
  return html`<div class="yt-labels">${table.labels.map((l) => html`<span class="yt-label">${l}</span>`)}</div>`;
}

function StepWatch({ table, carry, seek }) {
  return html`<div>
    <p class="yt-lead">MIRROR tracked ${table.objects.length} objects through ${fmt(table.video?.duration_s, 1)} s of your video and saw ${Object.values(carry).flat().length} carries.</p>
    <table class="yt-table"><thead><tr><th>Object</th><th>Stands in as</th><th class="r">Visible</th><th>Carried</th></tr></thead><tbody>
      ${table.objects.map((o) => { const cs = carry[o.label] || []; const vis = (table.__contours?.objects?.[o.label]?.visible);
        return html`<tr><td><i class="sw" style=${{ background: o.color }}></i>${o.name}</td><td>${o.proxy}</td><td class="r num">${vis != null ? Math.round(100 * vis) + '%' : '–'}</td>
        <td>${cs.length ? cs.map((w) => html`<button class="yt-tlink num" onClick=${() => seek(w.from - 0.5)}>${fmtT(w.from)}–${fmtT(w.to)}</button>`) : html`<span class="faint">stays put</span>`}</td></tr>`; })}
    </tbody></table>
    <p class="yt-note">Outlines are SAM 3.1 masks (recorded, ${table.contours ? 'sampled at 10 Hz' : 'not bundled'}). Moving objects light up while they are carried. A carry is inferred from object motion in one view; it is not contact sensing.</p>
  </div>`;
}

function StepMoments({ table, seek, t }) {
  const st = { inferred: 'inferred', person_corrected: 'you set this', confirmed: 'confirmed' };
  return html`<div>
    <p class="yt-lead">${table.events.length} moments, in order. Click one to jump to it.</p>
    <ol class="yt-events">${table.events.map((e) => { const o = table.objects.find((x) => x.label === e.object); const near = Math.abs(t - e.t) < 0.4;
      return html`<li class=${near ? 'now' : ''}><button onClick=${() => seek(e.t - 0.5)}>
        <span class="num t">${fmtT(e.t)}</span><i class="sw" style=${{ background: o?.color }}></i>
        <span class="w">${human(e.object)} ${e.type === 'pick' ? 'picked up' : `placed${e.placed_in ? ' on the ' + human(e.placed_in) : ''}`}</span>
        <span class=${'yt-st ' + (e.status || '')}>${st[e.status] || human(e.status)}</span>
        <span class="num faint br">±${fmt((e.bracket[1] - e.bracket[0]) / 2, 2)} s</span></button></li>`; })}</ol>
    <p class="yt-note">Brackets are the frames between which the change happened. Extra short mug moves are mask changes as a hand arrived; MIRROR keeps them because only you can reject them.</p>
  </div>`;
}

function StepConfirm({ table, dir }) {
  const open = table.review.filter((r) => !r.resolved);
  return html`<div>
    <div class="yt-cards">${table.objects.map((o) => html`<article class="yt-card">
      ${o.evidence ? html`<img src=${urlOf(dir, o.evidence)} alt=${'Evidence frame for ' + o.name} loading="lazy" />` : ''}
      <div class="yt-card-b">
        <p class="yt-card-h"><i class="sw" style=${{ background: o.color }}></i>${o.name} <span class="faint">→ ${o.proxy}</span></p>
        <p class="yt-card-id">${o.identity}</p>
        <p class="num faint">${Object.entries(o.dims_m || {}).filter(([, v]) => v != null).map(([k, v]) => `${k} ${fmt(v * 100, 1)} cm`).join(' · ')}</p>
        ${o.confirmed_by_person ? html`<p class="yt-yes">✓ You: “${o.confirmed?.answer || 'confirmed'}”${o.confirmed?.seconds_spent != null ? html` <span class="faint num">${fmt(o.confirmed.seconds_spent, 1)} s</span>` : ''}</p>`
          : html`<p class="yt-wait">Waiting for you</p>`}
        ${(o.unsupported || []).length ? html`<p class="faint">Not modelled: ${o.unsupported.join(', ')}</p>` : ''}
      </div></article>`)}</div>
    ${open.length ? html`<div class="callout bad" role="note"><b>Still open (${open.length})</b>${open.map((r) => html`<div>${human(r.code)}: ${r.message}</div>`)}</div>`
      : html`<div class="callout ok">Nothing left to review.</div>`}
  </div>`;
}

function StepTask({ table }) {
  const g = table.gate, task = table.task;
  const stated = task?.source === 'person_stated_intent';
  return html`<div>
    ${g.passed ? html`<div class="callout ok"><b>Gate passed.</b> Every blocking review item is resolved, so MIRROR may propose the task from your capture.</div>`
      : html`<div class="callout bad yt-gate"><b>MIRROR did not infer the task from this video.</b>
        <div>The capture gate refused: ${g.blocking.map((b) => html`<div class="mono-s">${b.code}: ${b.message}</div>`)}</div>
        ${g.blocking.map((b) => g.fix?.[b.code] ? html`<div>To clear it: ${g.fix[b.code]}</div>` : '')}
        <div class="faint">No bypass exists in the code; none was used.</div></div>`}
    ${task ? html`<div class="yt-spec">
      <p class="yt-spec-h">${stated ? 'The task you stated, run as written' : 'The task MIRROR inferred'} <span class="num faint">${task.spec_id} · v${task.version}</span></p>
      <p class="yt-lead">${task.summary}</p>
      <ul>${task.rules.map((r) => html`<li><span class=${'yt-kind ' + r.kind}>${r.kind}</span><span>${r.text}</span><span class="faint yt-src2">${r.source === 'manual' ? 'you' : human(r.source)}</span></li>`)}</ul>
      ${stated ? html`<p class="yt-note">Written from your answers on the capture page (order mug, box, marker; into the tray; around the divider; mug upright). Not a model output.</p>` : ''}
    </div>` : html`<p class="muted">No task yet.</p>`}
  </div>`;
}

function Metrics({ s }) {
  if (!s) return '';
  const ok = s.status === 'success';
  return html`<div class="yt-metrics">
    <div><span class="k">Result</span><span class=${'v ' + (ok ? 'ok' : 'bad')}>${human(s.status)}</span></div>
    <div><span class="k">In the tray</span><span class="v num">${s.in_target}/${s.objects}</span></div>
    ${s.beads ? html`<div><span class="k">Beads kept</span><span class="v num">${s.beads.kept}/${s.beads.total}</span></div>` : ''}
    <div><span class="k">Max carry tilt</span><span class="v num">${fmt(s.max_transport_tilt_deg, 1)}°</span></div>
    <div><span class="k">Violations</span><span class=${'v num ' + (s.violations ? 'bad' : '')}>${s.violations}</span></div>
    <div><span class="k">Sim time</span><span class="v num">${fmt(s.execution_time_s, 0)} s</span></div>
  </div>`;
}

function StepRobot({ table, dir, t, seek }) {
  const r = table.robot?.run;
  if (!r) return html`<p class="muted">No simulated run in this capture.</p>`;
  return html`<div>
    <${Metrics} s=${r.summary} />
    <p class="faint num yt-routes">${(r.summary.routes || []).map((x) => human(x).replace(/:/g, ' · ')).join('   ')}</p>
    <${PlanView} table=${table} run=${r} t=${t} />
    ${r.series?.gap_cm ? html`<${LineChart} title="Mug proxy to divider (approx.)" unit="cm" t=${t} onSeek=${seek}
      lines=${[{ name: 'run', color: 'var(--accent)', t: r.series.t, y: r.series.gap_cm }]} yMin=${0} yMax=${20}
      note="Horizontal gap from the cup proxy's surface to the divider box, drawn from recorded frames; the checker's verdicts use solid shapes." />` : ''}
    ${r.series?.tilt ? html`<${LineChart} title="Mug proxy tilt" unit="°" t=${t} onSeek=${seek} threshold=${15} thresholdLabel="15° limit" badAbove=${true} yMax=${20}
      lines=${[{ name: 'run', color: 'var(--accent)', t: r.series.t, y: r.series.tilt }]} yMin=${0} />` : ''}
    ${(table.twin || []).length ? html`<div class="yt-twin"><p class="yt-spec-h">The robot on your table, from your phone</p>
      ${table.twin.map((v) => html`<figure class="yt-tw"><figcaption>${TWIN_TITLE[v.name] || human(v.name)} <span class="num faint">${v.width}×${v.height}</span></figcaption>
        <video controls muted playsinline preload="metadata" src=${urlOf(dir, v.file)}></video></figure>`)}
      <p class="yt-note">Approximate: one camera, calibration reuse flagged.</p></div>` : ''}
    ${table.sim_video ? html`<details class="yt-det"><summary>Rendered video of this run</summary><video controls muted playsinline preload="none" src=${urlOf(dir, table.sim_video.file)}></video></details>` : ''}
    <p class="yt-note">${table.robot.placement_note}</p>
  </div>`;
}

const TWIN_TITLE = { overlay: 'The robot on your table, from your phone', side_by_side: 'Your hands · the robot', twin_3panel: 'Video · table twin · simulator', before_after: 'Before and after the example correction' };

const OUTCOME = {
  changed: ['Changes the run', 'ok'], already_held: ['Already true', ''], question: ['Needs your answer', 'warn'],
  refused: ['Not supported', 'bad'], no_change: ['No change', ''],
};

function OwnCorrection({ ctx, table, onItem }) {
  const [text, setText] = useState('');
  const [tid, setTid] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const send = async () => {
    const t = text.trim(); if (!t || busy) return;
    setBusy(true); setErr(null);
    try {
      let job = await api.post(`table/${encodeURIComponent(table.id)}/correct`, { text: t, transcript_id: tid });
      if (job && job.id && job.status && job.status !== 'done') job = await api.waitJob(job);
      const item = job.result || job;
      if (job.status === 'error') throw new Error(job.error || 'the run failed');
      onItem(item); setText(''); setTid(null);
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  return html`<div class="yt-say live">
    <div class="yt-say-row"><input value=${text} maxlength="600" placeholder="Type a correction, for example: keep the box 5 cm from the tray wall"
      aria-label="Your own correction" onInput=${(e) => { setText(e.target.value); }} onKeyDown=${(e) => e.key === 'Enter' && send()} disabled=${busy} />
      <button class="btn primary" disabled=${busy || !text.trim()} onClick=${send}>${busy ? 'Running…' : 'Check it'}</button></div>
    <div class="yt-say-row"><${MicButton} ctx=${ctx} disabled=${busy} onTranscript=${(tr) => { setText(tr.text); setTid(tr.id); }} />
      <span class="faint">${tid ? 'Transcribed; edit it if needed, then Check it.' : 'Compiled by this server’s interpreter, re-checked and run on your layout (about 20 s).'}</span></div>
    ${err ? html`<p class="err" role="alert">${err}</p>` : ''}
  </div>`;
}

function StepCorrect({ ctx, table, pick, setPick, go, extra, addExtra }) {
  const cs = [...(table.robot?.corrections || []), ...extra];
  return html`<div>
    <p class="yt-lead">Pick one correction. Each was compiled into a scoped rule, the old run was re-checked under it, and the robot ran again.</p>
    <div class="yt-cands" role="radiogroup" aria-label="Corrections">${cs.map((c) => { const [lab, cls] = OUTCOME[c.outcome] || [c.outcome, ''];
      return html`<button role="radio" aria-checked=${pick === c.id} class=${'yt-cand' + (pick === c.id ? ' on' : '')} onClick=${() => { setPick(c.id); go(); }}>
        <span class="q">“${c.text}”${c.live ? html` <span class="yt-new">yours</span>` : ''}</span>
        <span class="why">${c.why}</span>
        <span class="row2"><span class=${'yt-out ' + cls}>${lab}</span>${c.diff?.length ? html`<span class="num faint">${c.diff.map((d) => `+ ${d.value?.type} · ${d.value?.applies_to?.kind} · ${d.value?.phase}`).join('; ')}</span>` : ''}</span>
      </button>`; })}</div>
    ${api.isStatic ? html`<div class="yt-say"><input disabled placeholder="Say or type your own correction" aria-label="Your own correction (live studio only)" /><span class="faint">Your own words need the live studio; these were compiled by ${cs[0]?.compiler || 'the offline rules'}.</span></div>`
      : html`<${OwnCorrection} ctx=${ctx} table=${table} onItem=${(it) => { addExtra(it); setPick(it.id); go(); }} />`}
  </div>`;
}

const VERDICT = { satisfied: ['holds', 'ok'], violated: ['broken', 'bad'], not_verified: ['not verified', 'warn'], not_applicable: ['n/a', ''] };
function Verdict({ v }) { const [l, c] = VERDICT[v] || [human(v), '']; return html`<span class=${'yt-v ' + c}>${l}</span>`; }

function StepCompare({ table, pick, t, seek, extra }) {
  const c = [...(table.robot?.corrections || []), ...extra].find((x) => x.id === pick);
  if (!c) return html`<p class="muted">Choose a correction first.</p>`;
  if (c.outcome === 'question' || c.outcome === 'refused') return html`<div>
    <div class=${'callout ' + (c.outcome === 'refused' ? 'bad' : '')}><b>${c.outcome === 'question' ? 'MIRROR asks before changing anything:' : 'Not a supported change:'}</b> ${c.message}</div>
    <p class="yt-note">Nothing was changed and nothing was run. This is the correct answer to an instruction that cannot hold as written.</p></div>`;
  const base = table.robot.run, after = c.after;
  const added = new Set((c.diff || []).map((d) => d.target_id));
  const rows = (after?.rules || []).map((r) => ({ ...r, old: (c.recheck?.rules || []).find((x) => x.rule_id === r.rule_id) }));
  rows.sort((a, b) => (added.has(b.rule_id) ? 1 : 0) - (added.has(a.rule_id) ? 1 : 0));
  const thr = (c.diff || []).map((d) => d.value?.params?.min_m).find((x) => x != null);
  return html`<div>
    <div class="yt-diff">${(c.diff || []).map((d) => html`<div><span class="plus">+</span> <b>${d.value?.type}</b> · ${d.value?.applies_to?.kind} · ${d.value?.phase}${d.value?.params ? ' · ' + Object.entries(d.value.params).map(([k, v]) => `${k} ${v}`).join(', ') : ''}<div class="faint">${d.value?.description}</div></div>`)}
      <div class="faint num">v${table.task?.version} → v${c.version} · ${c.compiler}</div></div>
    ${c.outcome === 'already_held' ? html`<div class="callout">The old run already kept this rule; the re-check confirms it from the step log. The robot does the same thing.</div>`
      : html`<div class="callout ok"><b>The old run breaks the new rule; the new run keeps it.</b> ${(c.recheck?.violations || []).length} violation window${(c.recheck?.violations || []).length === 1 ? '' : 's'} in the old run, ${after?.summary?.violations ?? 0} in the new one.</div>`}
    <table class="yt-table yt-recheck"><thead><tr><th>Rule</th><th>Old run, re-checked</th><th>New run</th></tr></thead><tbody>
      ${rows.map((r) => html`<tr class=${added.has(r.rule_id) ? 'new' : ''}><td><span class="num">${r.rule_id}</span>${added.has(r.rule_id) ? html` <span class="yt-new">new</span>` : ''}${r.old?.detail && r.old.verdict === 'violated' ? html`<div class="faint">${r.old.detail.split(';')[0]}</div>` : ''}</td>
        <td><${Verdict} v=${r.old?.verdict} /></td><td><${Verdict} v=${r.verdict} /></td></tr>`)}
    </tbody></table>
    ${base.series?.gap_cm && after?.series?.gap_cm ? html`<${LineChart} title="Mug proxy to divider, before and after (approx.)" unit="cm" t=${t} onSeek=${seek} yMin=${0} yMax=${20} height=${190}
      threshold=${thr != null ? thr * 100 : null} thresholdLabel=${thr != null ? `${thr * 100} cm rule` : ''}
      lines=${[{ name: 'before', color: 'var(--faint)', t: base.series.t, y: base.series.gap_cm, dash: true }, { name: 'after', color: 'var(--accent)', t: after.series.t, y: after.series.gap_cm }]}
      note="Drawing aid from recorded frames (centre distance minus the proxy radius). Verdicts in the table come from the checker on the dense step log." />` : ''}
    <${PlanView} table=${table} run=${after} ghost=${base} t=${t} clearance=${thr} />
    <${Metrics} s=${after?.summary} />
  </div>`;
}

// ------------------------------------------------------------------ the panel
export function TablePanel({ ctx }) {
  const st = useTable();
  const [step, setStep] = useState(() => { const s = new URLSearchParams(location.search).get('ts'); return TABLE_STEPS.some((x) => x.key === s) ? s : 'watch'; });
  const [t, setTRaw] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seekReq, setSeekReq] = useState(null);
  const [focus, setFocus] = useState(null);
  const [pick, setPick] = useState(null);
  const [rt, setRt] = useState(0);
  const [rPlaying, setRPlaying] = useState(false);
  const [rSeek, setRSeek] = useState(null);
  const [extra, setExtra] = useState([]);
  const [capView, setCapView] = useState(null);
  const table = st.table;
  if (table && st.contours) table.__contours = st.contours;
  const carry = useMemo(() => (table ? carries(table.events) : {}), [table]);
  useEffect(() => { if (table?.capture_view) api.getDataFile(st.dir + table.capture_view).then((v) => setCapView({ view: v, base: (api.config.base || 'data/') + st.dir + table.capture_view.replace(/[^/]*$/, '') })).catch(() => {}); }, [table]);
  const setT = useCallback((x, seek) => { setTRaw(x); if (seek) setSeekReq({ t: Math.max(0, x) }); }, []);
  const seek = (x) => setT(x, true);
  const rseek = (x) => { setRt(x); setRSeek({ t: x }); };
  useEffect(() => { if (table && !pick) { const c = (table.robot?.corrections || []).find((x) => x.outcome === 'changed') || (table.robot?.corrections || [])[0]; c && setPick(c.id); } }, [table]);
  useEffect(() => { const u = new URL(location.href); u.searchParams.set('ts', step); history.replaceState(history.state, '', u); setPlaying(false); setRPlaying(false); }, [step]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest && e.target.closest('input,textarea,select,[role=slider],.yt-3d-canvas')) return;
      if (e.key === ' ') { VIDEO_STEPS.has(step) ? setPlaying((p) => !p) : setRPlaying((p) => !p); e.preventDefault(); }
    };
    addEventListener('keydown', onKey); return () => removeEventListener('keydown', onKey);
  }, [step]);

  if (st.loading) return html`<div class="yt"><p class="faint yt-pad">Loading your table…</p></div>`;
  if (st.error) return html`<div class="yt"><div class="yt-pad"><p class="kicker">Your table</p><p class="muted">No real capture is bundled with this site (${st.error}). Build one with <code>python -m mirror.studio.real_table</code> and export with <code>--table</code>.</p></div></div>`;

  const i = TABLE_STEPS.findIndex((s) => s.key === step);
  const S = TABLE_STEPS[i];
  const onVideo = VIDEO_STEPS.has(step);
  const corr = [...(table.robot?.corrections || []), ...extra].find((x) => x.id === pick);
  const run3d = step === 'compare' && corr?.after ? corr.after : table.robot?.run;
  const ghost3d = step === 'compare' && corr?.after ? table.robot?.run : null;
  const body = {
    watch: html`<${StepWatch} table=${table} carry=${carry} seek=${seek} />`,
    moments: html`<${StepMoments} table=${table} seek=${seek} t=${t} />`,
    confirm: html`<${StepConfirm} table=${table} dir=${st.dir} />`,
    task: html`<${StepTask} table=${table} />`,
    robot: html`<${StepRobot} table=${table} dir=${st.dir} t=${rt} seek=${rseek} />`,
    correct: html`<${StepCorrect} ctx=${ctx} table=${table} pick=${pick} setPick=${setPick} go=${() => setStep('compare')} extra=${extra} addExtra=${(it) => setExtra((x) => [...x, it])} />`,
    compare: html`<${StepCompare} table=${table} pick=${pick} t=${rt} seek=${rseek} extra=${extra} />`,
  }[step];
  const rdur = run3d?.duration_s || 0;

  return html`<div class="yt">
    <header class="yt-hd">
      <div><p class="kicker">Your table</p><h2>${table.title}</h2></div>
      <${Labels} table=${table} />
    </header>
    <nav class="yt-steps" aria-label="Your table steps">${TABLE_STEPS.map((s) => html`<button aria-current=${s.key === step ? 'step' : null} class=${TABLE_STEPS.findIndex((x) => x.key === s.key) < i ? 'done' : ''}
      onClick=${() => setStep(s.key)}><span class="ix num">${s.n}</span><span class="lbl">${s.short}</span></button>`)}</nav>
    <div class="yt-body">
      <section class="yt-stage" aria-label=${onVideo ? 'Your video' : 'Simulated run'}>
        <div class=${'yt-swap' + (onVideo ? ' video' : ' robot')}>
          <div class="yt-pane yt-pane-v" aria-hidden=${!onVideo}>
            <${TakePlayer} table=${table} contours=${st.contours} dir=${st.dir} t=${t} setT=${setT} playing=${playing} setPlaying=${setPlaying}
              focus=${focus} setFocus=${setFocus} seekReq=${seekReq} carry=${carry} />
          </div>
          <div class="yt-pane yt-pane-r" aria-hidden=${onVideo}>
            ${!onVideo || rdur ? html`<${RobotStage} dir=${st.dir} run=${run3d} ghost=${ghost3d} t=${rt} setT=${setRt} playing=${rPlaying} setPlaying=${setRPlaying} seekReq=${rSeek}
              capture=${capView} label=${step === 'compare' && ghost3d ? 'After the correction · grey: before' : 'Your layout · simulated proxies · MuJoCo'} />` : ''}
          </div>
        </div>
        <div class="yt-transport">
          <button class="iconbtn" aria-label=${(onVideo ? playing : rPlaying) ? 'Pause' : 'Play'} onClick=${() => (onVideo ? setPlaying(!playing) : setRPlaying(!rPlaying))}>
            ${(onVideo ? playing : rPlaying) ? html`<svg viewBox="0 0 16 16"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>` : html`<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>`}</button>
          <span class="num">${fmtT(onVideo ? t : rt)} / ${fmtT(onVideo ? table.video?.duration_s : rdur)}</span>
          ${onVideo ? '' : html`<input class="yt-range" type="range" min="0" max=${rdur || 1} step="0.01" value=${rt} onInput=${(e) => rseek(+e.target.value)} aria-label="Run position" />`}
        </div>
        ${onVideo ? html`<${EventLanes} table=${table} carry=${carry} t=${t} onSeek=${seek} focus=${focus} setFocus=${setFocus} />` : ''}
      </section>
      <section class="yt-side" aria-live="polite">
        <div class="yt-step" key=${step}>
          <p class="yt-n num">${S.n} / ${TABLE_STEPS.length}</p>
          <h3>${S.title}</h3>
          ${body}
        </div>
        <div class="yt-nav">
          <button class="btn" disabled=${i === 0} onClick=${() => setStep(TABLE_STEPS[i - 1].key)}>Back</button>
          <span class="spacer"></span>
          ${i < TABLE_STEPS.length - 1 ? html`<button class="btn primary" onClick=${() => setStep(TABLE_STEPS[i + 1].key)}>${TABLE_STEPS[i + 1].short}</button>` : ''}
        </div>
        <details class="yt-det yt-hon"><summary>How this page was made</summary><p>${table.honesty}</p>
          <p class="num faint">built ${table.built_at} · session ${table.session_id} · model calls while building: ${table.model_calls_on_build}${table.spend ? ` · capture spend $${fmt(table.spend.spent_usd ?? table.spend.usd, 2)}` : ''}</p>
          ${(table.notes || []).map((n) => html`<p class="faint">${n}</p>`)}</details>
      </section>
    </div>
  </div>`;
}
