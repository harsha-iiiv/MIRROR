// How results may be described to a person: planning outcomes, interpretation outcomes and
// per-step provenance. Mirrors mirror/plan/outcome.py, mirror/interpret/outcome.py and
// mirror/studio/provenance.py. Pure functions: no DOM, no network.

export const CERTIFICATE_KINDS = ['separating_wall_slot_clearance', 'clearance_at_start'];
export const NOT_A_SAFETY_GUARANTEE = 'This is a statement about the simulated task model under the listed assumptions, not a guarantee about physical safety.';

export function certificateIsValid(cert) {
  return !!cert && cert.proven === true && CERTIFICATE_KINDS.includes(cert.kind)
    && !!cert.statement && Array.isArray(cert.assumptions) && cert.assumptions.length > 0;
}

// {kind: planned | proven_infeasible | no_route_found | invalid_specification | error | unknown, headline, detail,
//  assumptions, not_covered}. A server-supplied outcome is used only if it does not claim more than its certificate.
export function planningOutcome({ plan = null, report = null, outcome = null } = {}) {
  const cert = (outcome && outcome.certificate) || (plan && plan.infeasibility) || null;
  if (outcome && outcome.kind && !(outcome.kind === 'proven_infeasible' && !certificateIsValid(cert))) {
    const o = { assumptions: [], not_covered: [], detail: '', ...outcome };
    if (o.kind === 'proven_infeasible') {
      o.headline = 'Proven infeasible under these stated assumptions';
      if (!o.detail.includes(NOT_A_SAFETY_GUARANTEE)) o.detail = (o.detail ? o.detail + ' ' : '') + NOT_A_SAFETY_GUARANTEE;
    }
    if (o.kind === 'no_route_found') o.headline = 'Planner found no compliant route';
    return o;
  }
  const status = (plan && plan.status) || (report && (report.plan_status === 'no_plan' ? 'no_plan' : report.plan_status)) || '';
  const reason = (plan && plan.reason) || (report && report.plan_reason) || '';
  if (status === 'ok' || (!status && report && report.status !== 'planning_failed')) return { kind: 'planned', headline: 'Planned', detail: '', assumptions: [], not_covered: [] };
  if (status === 'invalid_spec') return { kind: 'invalid_specification', headline: 'The specification cannot be planned as written', detail: reason, assumptions: [], not_covered: [] };
  if (status === 'error') return { kind: 'error', headline: 'The planner failed with an error', detail: reason, assumptions: [], not_covered: [] };
  if (certificateIsValid(cert)) {
    return { kind: 'proven_infeasible', headline: 'Proven infeasible under these stated assumptions',
      detail: cert.statement + ' ' + NOT_A_SAFETY_GUARANTEE, assumptions: [...cert.assumptions], not_covered: [...(cert.not_covered || [])], certificate: cert };
  }
  return { kind: 'no_route_found', headline: 'Planner found no compliant route',
    detail: 'The planner searched and found none; this is not a proof that none exists.' + (reason ? ' ' + reason : ''), assumptions: [], not_covered: [] };
}

// Interpretation outcomes (apply | revert | clarify | refuse) from a proposal in either the
// structured form (outcome, reason_code) or the older one (question / rejected / changed).
const CLARIFY_TITLE = {
  absent_object: 'That object is not in this layout. Which one do you mean?',
  unknown_object: 'MIRROR does not know that word. Which object do you mean?',
  ambiguous_referent: 'Which object do you mean?',
  vague: 'Which measurable requirement matters?',
  needs_value: 'What value should the rule use?',
  unsupported_rule: 'That rule is not supported. Did you mean one of these?',
  contradiction: 'That asks for two different things. Which one?',
  unclear_order: 'Which object should come first?',
  unclear_polarity: 'Should the rule be added or removed?',
  unknown_version: 'That version does not exist. Which one do you mean?',
  model_question: 'MIRROR needs to know:',
  other: 'MIRROR needs to know:',
};
const REFUSE_TITLE = {
  not_supported: 'MIRROR cannot do that',
  no_change: 'The specification already says this',
  unchanged: 'That did not change the specification',
  invalid: 'That could not be turned into a valid rule',
};

export function interpretationOutcome(p) {
  if (!p) return null;
  let kind = p.outcome || (p.question ? 'clarify' : p.rejected ? 'refuse' : p.changed ? 'apply' : 'refuse');
  if (kind === 'asks') kind = 'clarify';
  if (kind === 'rejects') kind = 'refuse';
  const code = p.reason_code || p.code || (kind === 'refuse' && /already says/i.test(p.rejected || '') ? 'no_change'
    : kind === 'refuse' && /did not change/i.test(p.rejected || '') ? 'unchanged' : kind === 'refuse' ? 'not_supported' : 'other');
  const message = p.message || p.question || p.rejected || '';
  const options = (p.options || []).map((o) => (typeof o === 'string' ? { label: o, text: o } : { label: o.label || o.text || String(o.answer || ''), text: o.text || o.label || '', answer: o.answer }));
  const title = kind === 'clarify' ? (CLARIFY_TITLE[code] || CLARIFY_TITLE.other) : kind === 'refuse' ? (REFUSE_TITLE[code] || REFUSE_TITLE.not_supported) : '';
  return { kind, code, title, message, options, supported: p.supported_changes || [], alias: p.alias || null, acceptable: (kind === 'apply' || kind === 'revert') && !!p.changed };
}

// Per-step provenance (mirror/studio/provenance.py step_provenance), read by a person.
export const STEP_KIND = {
  fresh_model: { label: 'Fresh model inference', tone: 'live', group: 'model' },
  replayed_cassette: { label: 'Recorded model response, replayed', tone: 'recorded', group: 'model' },
  recorded_model: { label: 'Recorded model output', tone: 'recorded', group: 'model' },
  offline_fallback: { label: 'Offline-rule interpreter', tone: 'offline', group: 'model' },
  new_simulation: { label: 'New simulation run', tone: 'live', group: 'sim' },
  recorded_playback: { label: 'Recorded simulation playback', tone: 'recorded', group: 'sim' },
  new_check: { label: 'Checked now from the recorded step log', tone: 'live', group: 'check' },
  fixture: { label: 'Interface fixture (no physics, no model)', tone: 'offline', group: 'fixture' },
  manual_edit: { label: 'Manual edit (no model)', tone: 'recorded', group: 'model' },
};
// Older single-word provenance values map onto the step kinds.
const LEGACY = { fresh_model_call: 'fresh_model', offline_interpreter: 'offline_fallback', replayed_cassette: 'replayed_cassette',
  recorded: 'recorded_model', simulated: 'new_simulation', fixture: 'fixture', live: 'fresh_model' };

export function stepProvenance(sp, legacy = null, { recordedView = false } = {}) {
  let p = sp && sp.kind ? { ...sp } : (legacy ? { kind: LEGACY[legacy] || legacy } : null);
  if (!p) return null;
  // Viewed from a recording, nothing ran now.
  if (recordedView) p.kind = { fresh_model: 'recorded_model', replayed_cassette: 'recorded_model', new_simulation: 'recorded_playback', new_check: 'recorded_playback' }[p.kind] || p.kind;
  const k = STEP_KIND[p.kind] || { label: p.label || p.kind, tone: 'recorded', group: 'other' };
  const who = p.model || p.interpreter || p.checker || null;
  const parts = [];
  if (who) parts.push(who);
  if (p.request_status && p.request_status !== 'ok') parts.push(`request ${p.request_status}`);
  if (p.latency_s != null) parts.push(`${Number(p.latency_s).toFixed(p.latency_s < 10 ? 2 : 1)} s${p.latency_source && p.latency_source !== 'measured_now' ? ' (as recorded)' : ''}`);
  if (p.cost_usd != null) parts.push(`$${Number(p.cost_usd).toFixed(4)}${p.cost_note ? ' est.' : ''}`);
  const fellBack = p.kind === 'offline_fallback' && !!p.fallback_reason && !/configured/.test(p.fallback_reason);
  return { kind: p.kind, label: k.label + (fellBack ? ' (fallback)' : ''), tone: fellBack ? 'warn' : k.tone, group: k.group,
    detail: parts.join(' · '), fallback: p.fallback_reason || null, fellBack, recordedFrom: p.recorded_from || null };
}
