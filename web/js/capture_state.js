// Phone capture: pure helpers for the capture wizard (no DOM, no network).
// Thresholds and wording come from the phone track (mirror/capture/calibrate.py THRESHOLDS,
// mirror/capture/proxy.py, docs/CAMERA_CAPTURE.md and the capture wizard content).

export const HONESTY = 'Real-phone capture is a supported workflow, tested on synthetic renders only. No real footage has been validated yet. Your hand is not a robot gripper: MIRROR infers pick-up and put-down from how the objects move.';
export const REAL_FOOTAGE_STATUS = 'awaiting footage';
export const TARGET = { name: 'mirror_charuco_v1', scale_bar_mm: 50.0, square_mm: 30.0 };
export const PRINT_TOL = { bar_mm: 1.0, square_rel: 0.02 };

// The person's ruler readings of the printed target: [{field, message}] problems, empty when fine.
export function printCheck(bar, square, target = TARGET) {
  const out = [];
  const b = Number(bar), s = Number(square);
  if (bar === '' || bar == null || !Number.isFinite(b)) out.push({ field: 'bar', message: 'Measure the 50 mm bar.' });
  else if (Math.abs(b - target.scale_bar_mm) > PRINT_TOL.bar_mm) out.push({ field: 'bar', message: 'Reprint at 100% before continuing.' });
  if (square === '' || square == null || !Number.isFinite(s)) out.push({ field: 'square', message: 'Measure one square.' });
  else if (Number.isFinite(b) && b > 0) {
    const implied = target.square_mm * b / target.scale_bar_mm;
    if (Math.abs(s / implied - 1) > PRINT_TOL.square_rel) out.push({ field: 'square', message: 'These two measurements disagree; measure again.' });
  }
  return out;
}

export const REVIEW_TITLES = {
  print_scale_invalid: 'Reprint the target', intrinsics_missing: "Calibrate this phone's lens",
  calibration_invalid: 'Lens calibration not good enough', recalibration_required: 'Recalibrate: the camera changed',
  pose_invalid: 'Board on the table not seen well', camera_moved: 'A phone moved during the take',
  no_calibrated_camera: 'No usable camera', sync_refused: 'Not combining the two views', single_view: 'Single view: reduced results',
  mapping_unconfirmed: 'What is this object?', proxy_size_mismatch: 'Real object differs from its proxy',
  height_not_measured: 'Height not measured', multiple_trays: 'Only one tray is supported', views_disagree: 'The two views disagree',
  tilt_unobserved: 'Tilt not observed', identity_uncertain: 'Same object?', ambiguous_event_interval: 'When was it picked up / put down?',
  low_coverage: 'Object often not found',
};

// Evidence badge for a session (never stronger than the session's own evidence kind).
export function evidenceBadge(s) {
  if (!s) return null;
  if (s.synthetic || s.evidence_kind === 'synthetic_test_render') return { tone: 'synthetic', text: 'SYNTHETIC test render - not real footage' };
  if (s.evidence_kind === 'camera_video_reconstruction') return { tone: 'ok', text: 'Real phone footage (calibrated, reviewed)' };
  return { tone: 'warn', text: 'Phone footage - not yet verified' };
}

// Review items with local resolutions applied. resolutions: {key: text}; key = code|object|event|camera.
export const itemKey = (r) => [r.code, r.object || '', r.event || '', r.camera || ''].join('|');
export function reviewState(s, resolutions = {}) {
  const items = (s?.review || []).map((r) => ({ ...r, title: REVIEW_TITLES[r.code] || r.code,
    resolved: !!r.resolved || !!resolutions[itemKey(r)], resolution: r.resolution || resolutions[itemKey(r)] || null }));
  const blocking = items.filter((r) => r.severity === 'abstain' && !r.resolved);
  return { items, blocking, ready: blocking.length === 0 };
}

// Share of samples per status, for the coverage bar.
export const COVER_KINDS = ['measured', 'inferred', 'interpolated', 'unobserved'];
export function coverage(statuses) {
  const n = (statuses || []).length;
  const out = Object.fromEntries(COVER_KINDS.map((k) => [k, 0]));
  if (!n) return { ...out, n: 0 };
  for (const x of statuses) out[COVER_KINDS.includes(x) ? x : 'unobserved'] += 1;
  for (const k of COVER_KINDS) out[k] = out[k] / n;
  return { ...out, n };
}

const PROXY = {
  cup: { sim: 'cup', note: 'open-top box cup with simulated beads' }, block: { sim: 'block', note: 'rigid cube' },
  marker: { sim: 'marker', note: 'capped cylinder' }, tray: { sim: 'tray', note: 'open box destination' },
  wall: { sim: 'wall obstacle', note: 'static vertical wall, sized from the real object' },
};
// Mirrors ObjectMapping.fidelity_statement() (mirror/capture/proxy.py); a server-supplied statement wins.
export function fidelityStatement(m) {
  if (m.fidelity_statement) return m.fidelity_statement;
  if (m.proxy === 'ignore') return `${m.real_identity}: ignored, not part of the task.`;
  const p = PROXY[m.proxy] || { sim: m.proxy, note: '' };
  const dims = Object.entries(m.real_dims || {});
  const measured = dims.filter(([, d]) => String(d.source || '').startsWith('measured')).map(([k]) => k);
  const supplied = dims.filter(([, d]) => !String(d.source || '').startsWith('measured')).map(([k]) => k);
  const parts = [`${m.real_identity} is represented in simulation by the ${p.sim} proxy${p.note ? ` (${p.note})` : ''}.`];
  if (measured.length) parts.push('Measured: ' + measured.join(', ') + '.');
  if (supplied.length) parts.push('Supplied or defaulted, not measured: ' + supplied.join(', ') + '.');
  if ((m.unsupported_features || []).length) parts.push('Not modelled: ' + m.unsupported_features.join(', ') + '.');
  if (m.proxy === 'cup') parts.push(`Simulated beads stand in for ${m.real_contents || 'its real contents'}; spill counts are simulated, not measured from the footage${m.contents_observable ? ' (contents measured independently).' : '.'}`);
  if ((m.assumptions || []).length) parts.push('Assumed: ' + m.assumptions.map((a) => `${a.name} ${a.value} (${a.basis})`).join('; ') + '.');
  parts.push('Proxy fidelity is not measured physical accuracy.');
  return parts.join(' ');
}

const EVENT_VERB = { pick: 'picked up', place: 'put down', grasp: 'picked up', release: 'put down', carry_start: 'started moving', carry_end: 'stopped moving' };
export function eventText(e) {
  const v = EVENT_VERB[e.type] || e.type;
  if (e.t_lo != null && e.t_hi != null && e.t_hi - e.t_lo > 1e-6) return `${e.object} ${v} between ${e.t_lo.toFixed(2)} s and ${e.t_hi.toFixed(2)} s`;
  return `${e.object} ${v} at ${Number(e.t).toFixed(2)} s`;
}
export const EVENT_LABEL = 'Inferred from object motion (human hand), not robot contact';

export function syncText(s) {
  if (!s || s.mode === 'single_view') return { ok: null, text: 'Single view: depth along the camera axis and tilt about it are not observed; heights are assumed while lifted.' };
  const y = s.sync;
  if (!y) return { ok: false, text: 'No synchronisation result.' };
  if (!y.ok) return { ok: false, text: 'Not combining the two views: ' + ((y.reasons || []).join('; ') || 'the sync events did not match') + '.' };
  return { ok: true, text: `Synchronised by the ${y.method}: offset ${y.offset_s.toFixed(3)} s, uncertainty ${Math.round(1000 * y.uncertainty_s)} ms, ${(y.matched || []).length} matched events.` };
}

// Every calibration check of every camera, flattened: [{camera, group, name, value, threshold, passed, note}].
export function calibrationRows(s) {
  const rows = [];
  for (const cam of Object.keys({ ...(s?.intrinsics || {}), ...(s?.poses || {}) })) {
    for (const [group, src] of [['lens', s.intrinsics?.[cam]], ['table pose', s.poses?.[cam]], ['recalibration', s.recalibration?.[cam]]]) {
      for (const c of src?.checks || []) rows.push({ camera: cam, group, ...c });
    }
  }
  return rows;
}

export function modelProvenance(s) {
  const calls = s?.model_calls || [];
  if (!calls.length) return 'No model calls (person-drawn boxes + classical tracking)';
  return calls.map((c) => `${c.model || 'model'} · ${c.origin === 'recorded' || c.recorded ? 'recorded model output' : 'fresh model call'}`).join('; ');
}

// Codes a person can settle by looking (the rest need a new recording or calibration).
export const RESOLVABLE = new Set(['mapping_unconfirmed', 'identity_uncertain', 'proxy_size_mismatch', 'height_not_measured',
  'views_disagree', 'low_coverage', 'tilt_unobserved', 'ambiguous_event_interval', 'single_view']);
export const NEEDS_NEW_TAKE = { print_scale_invalid: 'Reprint the target at 100% and record the lens clip again.',
  intrinsics_missing: "Record this phone's lens clip.", calibration_invalid: 'Record the lens clip again: slower, tilted, near the corners.',
  recalibration_required: 'Record a new lens clip with the phone as it was during the take.',
  pose_invalid: 'Lay the board flat at the start of the take, closer or more tilted.', camera_moved: 'Fix the phone in place and record the take again.',
  no_calibrated_camera: 'Calibrate at least one phone.', sync_refused: 'Record both phones during the same take, with both sync events.',
  multiple_trays: 'Use one tray.' };

const EV_TYPE = { pick: 'grasp', place: 'place', grasp: 'grasp', release: 'release', carry_start: 'carry_start', carry_end: 'carry_end' };
// The studio review body (CHALLENGE_API section 6) from a session plus the person's decisions.
export function reviewPayload(s, resolutions = {}, times = {}) {
  const correspondence = (s?.mappings || []).map((m) => ({ track_id: m.track_label, proxy: ['cup', 'block', 'marker'].includes(m.proxy) ? m.proxy : null,
    label: m.real_identity || '', dimensions_m: null, dimensions_source: null, unsupported_features: m.unsupported_features || [],
    assumed: Object.fromEntries((m.assumptions || []).map((a) => [a.name, a.value])) }));
  const resolvedEvent = (id) => Object.keys(resolutions).some((k) => k.split('|')[2] === id);
  const events = (s?.events || []).map((e) => {
    const set = times[e.id];
    return { track_id: e.object, type: EV_TYPE[e.type] || 'place', t_start: set != null ? set : (e.t_lo ?? e.t), t_end: set != null ? set : (e.t_hi ?? e.t),
      status: set != null ? 'edited' : resolvedEvent(e.id) ? 'confirmed' : (e.ambiguous || e.identity_uncertain ? 'uncertain' : 'confirmed') };
  });
  return { correspondence, events };
}
