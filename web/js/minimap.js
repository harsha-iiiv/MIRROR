// Top-down SVG map of a scene, optionally with the container's path coloured by tilt.
import { OBJECT_COLORS } from './viz/palette.js';
import { h } from 'preact';
import htm from 'htm';
const html = htm.bind(h);

function tiltClass(t, tol) { return t <= tol ? 'var(--accent)' : t <= 45 ? 'var(--warn)' : 'var(--bad)'; }

export function MiniMap({ scene, trace, tol = 15, ghost = null, label = '' }) {
  if (!scene) return html`<svg class="mini" viewBox="-0.5 -0.45 1 0.9" role="img" aria-label="no scene"></svg>`;
  const ws = scene.workspace || { x_range: [-0.45, 0.45], y_range: [-0.4, 0.4] };
  const parts = [];
  parts.push(html`<rect x=${ws.x_range[0]} y=${-ws.y_range[1]} width=${ws.x_range[1] - ws.x_range[0]}
    height=${ws.y_range[1] - ws.y_range[0]} fill="none" stroke="var(--line-strong)" stroke-width="0.003" />`);
  for (const t of scene.trays || []) {
    const [x, y] = t.pose.pos, [hx, hy] = t.inner_half;
    parts.push(html`<rect x=${x - hx} y=${-(y + hy)} width=${2 * hx} height=${2 * hy} rx="0.006"
      fill="var(--surface)" stroke=${OBJECT_COLORS.tray} stroke-width="0.004" />`);
  }
  for (const o of scene.obstacles || []) {
    if (o.kind === 'box') {
      const [x, y] = o.pose.pos, [hx, hy] = o.half;
      parts.push(html`<rect x=${x - hx} y=${-(y + hy)} width=${2 * hx} height=${2 * hy} fill=${OBJECT_COLORS.obstacle} opacity="0.55" />`);
    } else {
      const [x, y] = o.pose.pos, L = o.length, T = Math.max(o.thickness || 0.02, 0.012);
      let cursor = y - L / 2;
      const slots = [...(o.slots || [])].sort((a, b) => a.y_min - b.y_min);
      for (const s of slots) {
        if (s.y_min > cursor) parts.push(html`<rect x=${x - T / 2} y=${-s.y_min} width=${T} height=${s.y_min - cursor} fill=${OBJECT_COLORS.obstacle} />`);
        parts.push(html`<rect x=${x - T / 2} y=${-s.y_max} width=${T} height=${s.y_max - s.y_min} fill="none"
          stroke=${OBJECT_COLORS.obstacle} stroke-width="0.003" stroke-dasharray="0.01 0.008" />`);
        cursor = s.y_max;
      }
      if (cursor < y + L / 2) parts.push(html`<rect x=${x - T / 2} y=${-(y + L / 2)} width=${T} height=${y + L / 2 - cursor} fill=${OBJECT_COLORS.obstacle} />`);
    }
  }
  const drawPath = (pts, ghostMode) => {
    const segs = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      const col = ghostMode ? (b[3] > tol ? 'var(--bad)' : 'var(--faint)') : tiltClass(b[3], tol);
      segs.push(html`<line x1=${a[0]} y1=${-a[1]} x2=${b[0]} y2=${-b[1]} stroke=${col}
        stroke-width=${ghostMode ? 0.004 : 0.006} stroke-linecap="round" opacity=${ghostMode ? 0.7 : 1} />`);
    }
    return segs;
  };
  if (ghost?.points) parts.push(...drawPath(ghost.points, true));
  if (trace?.points) parts.push(...drawPath(trace.points, false));
  for (const o of scene.objects || []) {
    const [x, y] = o.pose.pos;
    const fill = OBJECT_COLORS[o.kind] || 'var(--muted)';
    if (o.kind === 'cup') parts.push(html`<rect x=${x - 0.03} y=${-y - 0.03} width="0.06" height="0.06" rx="0.008" fill=${fill} stroke="var(--text)" stroke-width="0.003" />`);
    else if (o.kind === 'block') parts.push(html`<rect x=${x - 0.02} y=${-y - 0.02} width="0.04" height="0.04" fill=${fill} />`);
    else parts.push(html`<circle cx=${x} cy=${-y} r="0.013" fill=${fill} />`);
  }
  return html`<svg class="mini" viewBox="-0.5 -0.45 1 0.9" role="img" aria-label=${label || 'top-down map of ' + scene.scene_id}>${parts}</svg>`;
}
