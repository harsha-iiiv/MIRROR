// Live rule panel: each rule of the spec the run was checked under, its value at the playhead
// (the checker's geometry on this playback frame) and the checker's verdict for the run.
import { tiltOf, objectObstacleClosest } from './geom.js';
import { carrying } from './verdict.js';

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function applies(sel, o) {
  if (!sel) return true;
  if (sel.ids?.length) return sel.ids.includes(o.id);
  return !sel.kind || sel.kind === o.kind;
}

export class RulePanel {
  constructor(el) {
    this.el = document.createElement('div');
    this.el.className = 'viz-rules';
    this.el.setAttribute('aria-label', 'Rules at the playhead');
    this.el.hidden = true;
    el.appendChild(this.el);
  }

  update(v) {
    const spec = v.spec, report = v.report, scene = v.sceneState, p = v.payload;
    const cons = (spec?.constraints || []).filter((c) => c.status !== 'rejected');
    if (!v.showRulePanel || !cons.length || !scene || !p) { this.el.hidden = true; return; }
    const i = v.frameIndex(), t = v.t, ph = p.phase?.[i] || '';
    const rows = [];
    for (const c of cons) {
      const objs = (scene.objects || []).filter((o) => applies(c.applies_to, o));
      const enforced = !c.status || c.status === 'confirmed';
      const ints = [...(report?.violations || []).filter((x) => x.constraint_id === c.id).map((x) => ({ ...x, k: 'violated' })),
        ...(report?.advisory_violations || []).filter((x) => x.constraint_id === c.id).map((x) => ({ ...x, k: 'advisory' }))];
      const now = ints.find((x) => t >= x.t_start && t <= x.t_end);
      const run = ints.some((x) => x.k === 'violated') ? 'violated' : ints.length ? 'advisory' : report ? 'held' : '–';
      let val = '–', lim = '', over = false;
      const end = t >= p.t[p.t.length - 1] - 1e-6;
      const live = objs.filter((o) => v.bodies.get(o.id) && p.bodies?.[o.id] && (c.phase === 'always' || (c.phase === 'transport' && carrying(ph, o.id)) || (c.phase === 'final' && end)));
      const pose = (o) => { const g = v.bodies.get(o.id), q = g.quaternion; return [[g.position.x, g.position.y, g.position.z], [q.w, q.x, q.y, q.z]]; };
      if (c.type === 'upright') {
        lim = `≤ ${(+c.params?.max_tilt_deg).toFixed(0)}°`;
        if (live.length) { const tl = Math.max(...live.map((o) => tiltOf(pose(o)[1]))); val = `${tl.toFixed(1)}°`; over = tl > +c.params.max_tilt_deg; }
      } else if (c.type === 'keep_clearance') {
        lim = `≥ ${(100 * c.params?.min_m).toFixed(1)} cm`;
        const boxes = v.rules.boxes || [];
        if (live.length && boxes.length) {
          const d = Math.min(...live.map((o) => { const [x, q] = pose(o); return objectObstacleClosest(o, x, q, boxes).d; }));
          val = `${(100 * d).toFixed(2)} cm`; over = d < +c.params.min_m;
        }
      } else if (c.type === 'forbidden_contact') lim = `no contact: ${(c.params?.with || []).join(', ')}`;
      const scope = `${c.applies_to?.ids?.join(',') || c.applies_to?.kind || 'all'} · ${c.phase}`;
      rows.push(`<div class="r ${now ? (now.k === 'violated' ? 'bad' : 'warn') : ''}"><span class="id" title="${esc(c.description || '')}">${esc(c.id)}${enforced ? '' : ' <i>proposed</i>'}</span>`
        + `<span class="sc">${esc(scope)}</span><b class="${over ? (enforced ? 'bad-t' : 'warn-t') : ''}">${esc(val)}</b><span class="lim">${esc(lim)}</span>`
        + `<span class="vd ${run}">${run}</span></div>`);
    }
    const head = `<div class="h"><span>rules · ${esc(spec.spec_id)} v${spec.version}</span><span>t ${t.toFixed(2)} s</span></div>`;
    const foot = `<div class="f">values as drawn at the playhead (checker geometry) · run verdict: ${report ? esc(report.status) : 'no report'} (checker, every physics step)</div>`;
    this.el.innerHTML = head + rows.join('') + foot;
    this.el.hidden = false;
  }
}
