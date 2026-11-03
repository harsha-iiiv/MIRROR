// Pure builder for manual structured edits (no DOM, no network): form fields -> SpecOps.
export const KINDS = ['cup', 'block', 'marker'];
export const PHASES = { transport: 'while carried', final: 'when placed', always: 'at all times' };
export const CONTACT_WITH = { obstacle: 'obstacles', table_outside_tray: 'the table outside the tray', other_objects: 'other objects' };

const sel = (who) => (who === 'all' ? { kind: null, role: null, ids: null } : { kind: who, role: null, ids: null });
const same = (a, who) => (who === 'all' ? !a?.kind && !a?.ids : a?.kind === who && !a?.ids);
const slug = (s) => String(s).replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '').toLowerCase();

// form: { type, who, phase, max_tilt_deg, min_m, with: [..], status, before, after }
export function manualOps(spec, form) {
  const version = (spec?.version || 0) + 1;
  const prov = { source: 'manual', evidence: 'manual structured edit', version_introduced: version, model: null };
  const status = form.status === 'rejected' ? 'rejected' : 'confirmed';
  if (form.type === 'ordering') {
    if (!form.before || !form.after || form.before === form.after) return { ops: [], error: 'Choose two different objects.' };
    const hit = (spec?.ordering || []).find((o) => same(o.before, form.before) && same(o.after, form.after));
    if (hit) return { ops: [{ op: 'set_status', target_id: hit.id, value: { status }, rationale: 'manual edit' }], error: null };
    const id = `o_${form.before}_before_${form.after}_manual`;
    return { ops: [{ op: 'add_ordering', target_id: id, rationale: 'manual edit', value: {
      id, before: sel(form.before), after: sel(form.after), status, provenance: prov,
      description: `Place the ${form.before} before picking up the ${form.after}.` } }], error: null };
  }
  const params = form.type === 'upright' ? { max_tilt_deg: Number(form.max_tilt_deg) }
    : form.type === 'keep_clearance' ? { min_m: Number(form.min_m) } : { with: form.with || [] };
  if (form.type === 'upright' && !(params.max_tilt_deg > 0 && params.max_tilt_deg <= 90)) return { ops: [], error: 'The tilt limit must be between 0 and 90 degrees.' };
  if (form.type === 'keep_clearance' && !(params.min_m > 0 && params.min_m <= 0.2)) return { ops: [], error: 'The distance must be between 0 and 0.2 m.' };
  if (form.type === 'forbidden_contact' && !params.with.length) return { ops: [], error: 'Choose what it must not touch.' };
  const hit = (spec?.constraints || []).find((c) => c.type === form.type && c.phase === form.phase && same(c.applies_to, form.who));
  if (hit) {
    const ops = [];
    if (JSON.stringify(hit.params || {}) !== JSON.stringify({ ...(hit.params || {}), ...params })) {
      ops.push({ op: 'modify_constraint', target_id: hit.id, value: { params: { ...(hit.params || {}), ...params } }, rationale: 'manual edit' });
    }
    if (hit.status !== status) ops.push({ op: 'set_status', target_id: hit.id, value: { status }, rationale: 'manual edit' });
    return { ops, error: ops.length ? null : 'The specification already says this.' };
  }
  if (status === 'rejected') return { ops: [], error: 'There is no such rule to mark as not required.' };
  const id = `c_${form.type}_${form.phase}_${form.who}`;   // same id a correction would produce
  return { ops: [{ op: 'add_constraint', target_id: id, rationale: 'manual edit', value: {
    id, type: form.type, applies_to: sel(form.who), phase: form.phase, params, status, provenance: prov, description: '' } }], error: null };
}


// The edit object the studio's manual form endpoint takes (mirror/studio/manual.py).
export function manualEdit(form) {
  if (form.type === 'ordering') return { action: 'add_ordering', before: form.before, after: form.after };
  const e = { action: 'set_rule', type: form.type, object: form.who, phase: form.phase, status: form.status === 'rejected' ? 'rejected' : 'confirmed' };
  if (form.type === 'upright') e.max_tilt_deg = Number(form.max_tilt_deg);
  if (form.type === 'keep_clearance') e.min_m = Number(form.min_m);
  if (form.type === 'forbidden_contact') e.with = form.with || [];
  return e;
}
