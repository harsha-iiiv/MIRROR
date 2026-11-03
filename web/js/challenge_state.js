// Challenge mode: pure helpers (no DOM, no network) for the studio's challenge API
// (docs/CHALLENGE_API.md). The UI lives in challenge.js.

// The rule language the server says it supports. Only these are advertised to visitors;
// with no list from the server, only the upright rule (the one every version supports).
export const DEFAULT_SUPPORTED = [
  { kind: 'upright', phases: ['transport', 'final'], example: 'Keep the cup upright while carrying it.' },
];

export function supportedRules(options) {
  const raw = options && options.correction && options.correction.supported;
  if (!Array.isArray(raw) || !raw.length || raw.every((x) => typeof x === 'string' && x.startsWith('...'))) return DEFAULT_SUPPORTED;
  return raw.map((x) => (typeof x === 'string' ? { kind: x } : { ...x, kind: x.kind || x.type })).filter((x) => x && x.kind);
}

const KIND_TEXT = {
  status: 'say a proposed rule is required, or not required',
  upright: 'keep an object upright (while carried, when placed, or always)',
  keep_clearance: 'keep a distance from obstacles',
  forbidden_contact: 'do not touch something',
  ordering: 'do one object before another',
  goal: 'put an object somewhere',
};
export function ruleKindText(r) { return r.text || KIND_TEXT[r.kind] || r.kind.replace(/_/g, ' '); }

// Which step of the challenge flow a server record is in.
export const FLOW = ['scene', 'before', 'correction', 'review', 'execute', 'share'];
export function challengeStage(ch, proposal) {
  if (!ch) return 'scene';
  if (ch.record_id || ch.state === 'published') return 'share';
  if (ch.after || ch.state === 'executed') return 'share';
  if (ch.revised_spec_version_id || ch.state === 'reviewed') return 'execute';
  if (proposal || ch.proposal || ch.state === 'proposed') return 'review';
  if (ch.before || ch.state === 'scene_ready') return 'correction';
  return 'before';
}

// A proposal can be accepted only when it changed the specification and asks nothing.
export function proposalAcceptable(p) {
  return !!(p && p.changed && !p.question && !p.rejected && (p.ops || []).length);
}

// Plain scope line: which objects, phases and rule items a correction touches.
export function scopeText(scope) {
  if (!scope) return '';
  const parts = [];
  if ((scope.objects || []).length) parts.push(scope.objects.join(', '));
  if ((scope.phases || []).length) parts.push(scope.phases.map((p) => ({ transport: 'while carried', final: 'when placed', always: 'at all times' }[p] || p)).join(', '));
  if ((scope.items || []).length) parts.push('rules ' + scope.items.join(', '));
  return parts.join(' · ');
}

// Share link for a published record: the same page with ?challenge=<record id> and no
// session, token or live switch in it.
export function shareUrl(href, recordId) {
  const u = new URL(href);
  for (const k of [...u.searchParams.keys()]) u.searchParams.delete(k);
  u.searchParams.set('challenge', recordId);
  u.hash = '';
  return u.toString();
}

export function recordIdFromUrl(search) {
  const id = new URLSearchParams(search || '').get('challenge');
  return id && /^[A-Za-z0-9_-]{1,80}$/.test(id) ? id : null;
}

// Validate a seed against the families the server offers (the server still fences
// frozen evaluation seeds itself; this only keeps the form honest).
export function seedProblem(options, family, seed) {
  const fam = ((options && options.families) || []).find((f) => f.id === family);
  if (!fam) return 'Choose a layout family.';
  if (fam.presets) return null;
  if (!Number.isInteger(seed)) return 'The seed must be a whole number.';
  const [lo, hi] = fam.seed_range || [0, 8999];
  if (seed < lo || seed > hi) return `Development seeds for this family are ${lo} to ${hi}.`;
  return null;
}

export function randomSeed(options, family, rnd = Math.random) {
  const fam = ((options && options.families) || []).find((f) => f.id === family);
  const [lo, hi] = (fam && fam.seed_range) || [0, 8999];
  return lo + Math.floor(rnd() * (hi - lo + 1));
}
