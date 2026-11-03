// The first-visit story, derived from the session itself: what went wrong, what the
// person changed, what changed in execution, and where the numbers come from.
// Pure functions (no DOM, no network) so they can be tested outside a browser.
import { cupOutcome, isAux, constraintText } from './util.js';

const TOL = 15;

function correctionRecord(snap) {
  const spec = (snap.specs || [])[snap.specs.length - 1];
  if (!spec) return null;
  const recs = (spec.history || []).filter((r) => r.kind === 'correction');
  return recs.length ? recs[recs.length - 1] : null;
}

const specV = (snap, v) => (snap.specs || []).find((s) => s.version === v) || null;

// Route of one object in a rollout: the plan's transport entry, else the report's route label.
export function routeFor(r, objId) {
  const t = (r?.plan?.transport || []).find((x) => x.object_id === objId);
  if (t) return t.route;
  for (const x of r?.report?.route_labels || []) {
    const raw = String(x).replace(/^route=/, '');
    if (raw.startsWith(objId + ':') && !raw.slice(objId.length + 1).startsWith('transit:')) return raw.slice(objId.length + 1);
  }
  return null;
}

// Objects the touched constraints apply to (by id or kind), from the corrected spec.
function scopeOf(spec, ids) {
  const out = [];
  for (const c of spec?.constraints || []) {
    if (!ids.has(c.id)) continue;
    out.push({ id: c.id, text: constraintText(c), phase: c.phase, status: c.status, applies_to: c.applies_to || null });
  }
  return out;
}

function appliesTo(sel, obj) {
  if (!sel) return true;
  if (sel.ids && sel.ids.length) return sel.ids.includes(obj.id);
  if (sel.kind) return sel.kind === obj.kind;
  return true;
}

// Returns null when the session holds no complete correction story.
export function correctionStory(snap) {
  if (!snap) return null;
  const rec = correctionRecord(snap);
  if (!rec) return null;
  const cv = rec.version;
  const after = specV(snap, cv);
  const before = specV(snap, after?.parent_version ?? cv - 1) || specV(snap, cv - 1);
  const rs = snap.rollouts || [];
  // The failure: a run made under an older version that the checker now rejects under the correction.
  const rechecks = (snap.rechecks || []).filter((x) => x.spec_version === cv && x.report?.status === 'constraint_violated');
  let failed = null, recheck = null;
  for (let i = rechecks.length - 1; i >= 0 && !failed; i--) {
    const r = rs.find((x) => x.id === rechecks[i].rollout_id && !isAux(x));
    if (r) { failed = r; recheck = rechecks[i]; }
  }
  if (!failed) return null;
  let fixed = null;
  for (let i = rs.length - 1; i >= 0; i--) {
    const r = rs[i];
    if (r.spec_version >= cv && r.scene_id === failed.scene_id && !isAux(r) && r.has_frames !== false) { fixed = r; break; }
  }
  const touched = new Set((rec.diff || []).map((o) => o.target_id).filter(Boolean));
  const scope = scopeOf(after, touched);
  const objects = (failed.scene?.objects || snap.scene?.objects || []);
  const cupA = cupOutcome(failed.report);
  const cupB = fixed ? cupOutcome(fixed.report) : null;
  const cupId = cupA?.object_id || cupB?.object_id || null;
  // Objects outside the correction's scope whose route did not change keep their freedom.
  const unchanged = [];
  if (fixed) {
    for (const o of objects) {
      if (o.id === cupId) continue;
      if (scope.some((c) => appliesTo(c.applies_to, o))) continue;
      const ra = routeFor(failed, o.id), rb = routeFor(fixed, o.id);
      if (ra && rb && ra === rb) unchanged.push({ object_id: o.id, kind: o.kind, route: rb });
    }
  }
  const violation = (recheck.report.violations || [])[0] || null;
  const transcript = (snap.transcripts || []).slice().reverse()
    .find((t) => t.text && (t.text === rec.text || t.text.trim() === String(rec.text || '').trim())) || null;
  return {
    version: cv,
    beforeVersion: before?.version ?? null,
    text: rec.text,
    compiledBy: rec.model || null,
    spoken: transcript ? { model: transcript.model, source: transcript.source, audio_file: transcript.audio_file || null } : null,
    scope,
    failed: {
      id: failed.id, spec_version: failed.spec_version, scene_id: failed.scene_id, status: failed.report.status,
      cup_route: cupId ? routeFor(failed, cupId) : null,
      tilt: cupA?.max_transport_tilt_deg ?? null,
      beads: cupA ? { kept: cupA.beads_in_container, total: cupA.beads_total } : null,
      overTol: (cupA?.max_transport_tilt_deg ?? 0) > TOL,
    },
    recheck: { status: recheck.report.status, constraint_id: violation?.constraint_id || null, peak: violation?.peak ?? null,
      t_start: violation?.t_start ?? null, t_end: violation?.t_end ?? null },
    fixed: fixed ? {
      id: fixed.id, spec_version: fixed.spec_version, status: fixed.report.status,
      cup_route: cupId ? routeFor(fixed, cupId) : null,
      tilt: cupB?.max_transport_tilt_deg ?? null,
      beads: cupB ? { kept: cupB.beads_in_container, total: cupB.beads_total } : null,
      violations: (fixed.report.violations || []).length,
    } : null,
    unchanged,
  };
}

// Where the shown numbers come from, in one plain sentence plus a short tag.
export function provenanceLine(snap, { isStatic }) {
  if (snap?.mode === 'fixture') return { tag: 'fixture', text: 'Fixture data: key-framed placeholder motion for interface development. Not evidence.' };
  if (isStatic) {
    const replay = snap?.llm_mode === 'replay';
    return {
      tag: 'recorded',
      text: 'Recorded session. The physics ran in MuJoCo when this session was recorded; nothing is computed while you watch.'
        + (replay ? ' Model responses in it were replayed from earlier recordings.' : ''),
    };
  }
  return { tag: 'fresh', text: 'Live session. Runs you start here execute in MuJoCo on the server now and are checked from their physics log.' };
}
