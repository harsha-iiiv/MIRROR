// Immutable identities for caching and for dropping late responses.
// A generation is one demonstration and everything derived from it (see docs/CHALLENGE_API.md).
// Pure functions: no DOM, no network.

// The snapshot's generation id. Servers that predate generations get a stable stand-in built
// from the session, the demonstration and the export, so a new demonstration never reuses a key.
export function generationId(snap) {
  if (!snap) return '';
  if (snap.generation && snap.generation.id) return String(snap.generation.id);
  const d = snap.demo || {};
  return ['legacy', snap.session_id || '', d.demo_id || '', d.content_sha256 || d.duration || '', snap.export_id || ''].join(':');
}

function findRollout(snap, id) {
  const rs = (snap && snap.rollouts) || [];
  return rs.find((r) => r.id === id) || null;
}

// Cache key for a playback payload. Ids that already carry a generation and a content hash
// (`<generation>.v<n>.<scene>.<r|c><seq>.<hash8>`) are immutable on their own; bare run
// numbers (`r001`) are scoped to the generation and to the rollout's content hash.
export function rolloutCacheKey(snap, id) {
  if (!id) return '';
  if (id === 'demo') {
    const d = (snap && snap.demo) || {};
    const pb = snap && snap.generation && snap.generation.demo && snap.generation.demo.playback_id;
    if (pb) return 'demo|' + pb;
    return generationId(snap) + '|demo|' + (d.content_sha256 || d.demo_id || '') + '|' + (d.duration ?? '');
  }
  const r = findRollout(snap, id);
  const hash = (r && (r.content_sha256 || r.hash)) || '';
  if (isImmutableId(id)) return id + (hash ? '|' + hash : '');
  return generationId(snap) + '|' + id + '|' + hash + '|v' + (r ? r.spec_version : '');
}

export function isImmutableId(id) {
  return /^g[0-9a-f]+[0-9a-f]{0,4}\.v\d+\./i.test(String(id || '')) || /^demo\.g[0-9a-f]+/i.test(String(id || ''));
}

// True when a payload, job or snapshot names a generation other than the expected one.
// Payloads without a generation (older servers, static exports) are never called stale.
export function isStale(expectedGeneration, obj) {
  const g = obj && (obj.generation_id || (obj.generation && obj.generation.id));
  return !!(g && expectedGeneration && !String(expectedGeneration).startsWith('legacy:') && g !== expectedGeneration);
}

// Wrap an async function so that only the newest call's result is delivered; older calls that
// finish later resolve to the `dropped` marker instead of overwriting newer state.
export const DROPPED = Object.freeze({ dropped: true });
export function latestOnly(fn) {
  let seq = 0;
  return async (...args) => {
    const mine = ++seq;
    const v = await fn(...args);
    return mine === seq ? v : DROPPED;
  };
}
