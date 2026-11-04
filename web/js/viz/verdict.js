// Per-frame verdicts from the checker's records. The checker evaluates every physics step and
// reports violation intervals [t_start, t_end] per rule and object; the viewer samples those
// intervals at playback frames. Nothing here re-judges a run.

export function intervalsFor(report, objectId) {
  const pick = (arr, kind) => (arr || []).filter((v) => !objectId || v.object_id === objectId).map((v) => ({ ...v, kind }));
  return [...pick(report?.violations, 'violated'), ...pick(report?.advisory_violations, 'advisory'),
    ...((report?.forbidden_contacts || []).filter((c) => !objectId || (c.object_id || c.object) === objectId)
      .map((c) => ({ constraint_id: c.constraint_id || 'forbidden_contact', object_id: c.object_id || c.object,
        t_start: +c.t_start, t_end: +c.t_end, peak: +(c.max_depth ?? 0), detail: c.detail || `contact with ${c.other || '?'}`, kind: 'contact' })))];
}

// 'violated' | 'advisory' | 'ok' for time t (enforced violations win over advisory ones).
export function verdictAt(ints, t, pad = 0) {
  let v = 'ok';
  for (const x of ints) {
    if (t >= x.t_start - pad && t <= x.t_end + pad) {
      if (x.kind === 'violated' || x.kind === 'contact') return 'violated';
      v = 'advisory';
    }
  }
  return v;
}

// Executor phases in which an object is held (grasp until release).
export function carrying(phase, objectId) {
  if (!phase) return false;
  const [kind, obj] = phase.split(':');
  return obj === objectId && (kind === 'grasp' || kind === 'lift' || kind === 'transport' || kind === 'place' || kind === 'carry');
}

export function events(payload) {
  const out = [];
  if (!payload?.phase) return out;
  let prev = '';
  payload.phase.forEach((ph, i) => {
    const [kind, obj] = (ph || '').split(':');
    if ((kind === 'grasp' || kind === 'release') && ph !== prev) out.push({ type: kind, object_id: obj, t: payload.t[i], frame: i });
    prev = ph;
  });
  return out;
}
