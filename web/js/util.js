// Small shared helpers.
export function tiltDeg(q) {
  const [, x, y] = q.length === 7 ? q.slice(3) : q;
  const zz = 1 - 2 * (x * x + y * y);
  return (Math.acos(Math.max(-1, Math.min(1, zz))) * 180) / Math.PI;
}

export function fmtT(t) {
  if (t == null || isNaN(t)) return '–';
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

export function fmtNum(v, d = 1) {
  if (v == null || isNaN(v)) return '–';
  return Number(v).toFixed(d);
}

export const STATUS_LABEL = {
  success: 'Success', task_incomplete: 'Task incomplete', constraint_violated: 'Rule violated',
  planning_failed: 'No plan', execution_failed: 'Execution failed', timeout: 'Timed out',
  unverified: 'Not verified', unknown: 'Unknown',
};

export function statusLabel(report) {
  if (!report) return '';
  if (report.status === 'planning_failed') {
    return report.plan_status === 'no_plan' ? 'No plan found' : 'Planning failed';
  }
  return STATUS_LABEL[report.status] || report.status;
}

export function cupOutcome(report) {
  return (report?.outcomes || []).find((o) => (o.beads_total || 0) > 0) || null;
}

export function selectorText(sel) {
  if (!sel) return '';
  const parts = [];
  if (sel.kind) parts.push(sel.kind);
  if (sel.role) parts.push(`role ${sel.role}`);
  if (sel.ids) parts.push(sel.ids.join(', '));
  return parts.join(' · ') || 'any object';
}

const PHASE_TEXT = { final: 'when placed', transport: 'throughout transport', always: 'at all times' };

export function constraintText(c) {
  const who = selectorText(c.applies_to);
  if (c.type === 'upright') {
    return `${cap(who)} stays within ${c.params?.max_tilt_deg ?? '?'}° of upright ${PHASE_TEXT[c.phase] || c.phase}`;
  }
  if (c.type === 'forbidden_contact') return `${cap(who)} must not touch ${(c.params?.with || []).join(', ')} (${c.phase})`;
  if (c.type === 'keep_clearance') return `${cap(who)} keeps ${c.params?.min_m ?? '?'} m from obstacles (${c.phase})`;
  return c.description || c.type;
}

export function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

export function sceneLabel(id) {
  if (!id) return '';
  const base = id.replace(/^ref_/, '');
  if (base === 'demo') return 'Demonstration layout';
  if (base === 'shortcut') return 'Shortcut layout (slot in the wall)';
  if (base === 'blocked') return 'Blocked layout (slot is the only way)';
  if (id.startsWith('custom')) return `Edited layout (${id})`;
  const m = id.match(/(eval|dev)[_-]?(\d+)/);
  if (m) return `${m[1] === 'eval' ? 'Fresh' : 'Development'} layout ${m[2]}`;
  return id;
}

export function provText(p) {
  if (!p) return '';
  const src = { demo_inference: 'Inferred from the demonstration', user_correction: 'From your correction',
    clarification: 'From your clarification answer', default: 'Default', manual: 'Set manually' }[p.source] || p.source;
  return `${src}${p.evidence ? ': ' + p.evidence : ''} · v${p.version_introduced}${p.model ? ' · ' + p.model : ''}`;
}

// Find the newest rollout matching a predicate.
export function latest(rollouts, pred = () => true) {
  for (let i = rollouts.length - 1; i >= 0; i--) if (pred(rollouts[i])) return rollouts[i];
  return null;
}

export const isAux = (r) => r.tags.includes('candidate') || r.tags.includes('eval');
export const isBlockedScene = (id) => /blocked/.test(id || '');

// Plain-language route names; the raw planner id stays available as a tooltip.
const OBJ_PREFIX = /^((?:cup|block|marker)_\d+):\s*/;
export function humanRoute(raw, short = false) {
  if (!raw) return '';
  let s = String(raw).trim();
  const m0 = s.match(/^route=([^;]+)/);
  if (m0) s = m0[1];
  let obj = '';
  const mo = s.match(OBJ_PREFIX);
  if (mo) { obj = mo[1].replace(/_\d+$/, ''); s = s.slice(mo[0].length); }
  let transit = false;
  if (s.startsWith('transit:')) { transit = true; s = s.slice(8); }
  const p = s.split(':');
  const last = p[p.length - 1];
  const mm = last.match(/^(pitched|rolled|tilted)([+-]?\d+(?:\.\d+)?)$/);
  const paths = short
    ? { direct: 'straight', around: 'around', through_slot: 'slot', over: 'over', rrt: 'search', detour: 'detour' }
    : { direct: 'straight across', around: 'around the wall', through_slot: 'through the slot', over: 'over the wall',
        rrt: 'free-space search', detour: 'detour' };
  const path = paths[p[0]] || s;
  let mode = '';
  if (mm) {
    const a = Math.round(Math.abs(parseFloat(mm[2])));
    mode = a >= 80 ? (short ? 'on side' : `on its side (${a}°)`) : (short ? `${a}°` : `tilted ${a}°`);
  } else if (last === 'upright' || p[0] === 'around' || p[0] === 'direct' || p[0] === 'over') {
    mode = 'upright';
  }
  const txt = path + (mode && !transit ? ', ' + mode : '');
  return (obj ? obj + ': ' : '') + (transit ? (short ? 'empty, ' : 'empty gripper, ') + path : txt);
}

