// Runtime configuration, resolved from window.MIRROR_CONFIG (set by config.js)
// and the page URL. Pure functions: no DOM, no network.
//
// window.MIRROR_CONFIG fields (all optional):
//   static   true when the page carries a recorded session in `base` (static export)
//   base     folder of the recorded session, default "data/"
//   apiBase  origin of a live studio server, e.g. "https://studio.example.org";
//            empty means the same origin as the page
//   wsBase   websocket origin; defaults to apiBase with http(s) -> ws(s)
//   repo     link to the source code
// The page URL parameter ?live=1 asks a recorded page to use the live server.

const trimSlash = (s) => s.replace(/\/+$/, '');

function cleanOrigin(value, schemes) {
  if (typeof value !== 'string') return '';
  const v = trimSlash(value.trim());
  if (!v) return '';
  let u;
  try { u = new URL(v); } catch (_) { return ''; }
  if (!schemes.includes(u.protocol)) return '';
  if (u.username || u.password || u.search || u.hash) return '';
  return trimSlash(u.origin + u.pathname);
}

export function resolveConfig(raw, loc) {
  const c = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const recordings = c.static === true;
  const apiBase = cleanOrigin(c.apiBase, ['http:', 'https:']);
  let wsBase = cleanOrigin(c.wsBase, ['ws:', 'wss:']);
  if (!wsBase && apiBase) wsBase = apiBase.replace(/^http/i, 'ws');
  const params = new URLSearchParams((loc && loc.search) || '');
  const askLive = params.get('live') === '1';
  // A page served by the studio server is always live; a recorded page can switch
  // to a live server only when one is configured.
  const liveAvailable = !recordings || !!apiBase;
  const wantLive = !recordings || (askLive && !!apiBase);
  const base = typeof c.base === 'string' && c.base && !/^[a-z]+:/i.test(c.base) ? c.base.replace(/\/?$/, '/') : 'data/';
  return {
    recordings, base, apiBase, wsBase, liveAvailable, wantLive,
    repo: typeof c.repo === 'string' && /^https:\/\//.test(c.repo) ? c.repo : null,
    healthTimeoutMs: Number.isFinite(c.healthTimeoutMs) ? c.healthTimeoutMs : 4000,
    wakeTimeoutMs: Number.isFinite(c.wakeTimeoutMs) ? c.wakeTimeoutMs : 90000,
  };
}

// URL of an API path ("state", "jobs/j1") for this configuration.
export function apiUrl(cfg, path) {
  return (cfg.apiBase ? cfg.apiBase + '/api/' : 'api/') + path;
}

// Websocket URL for a session. Same-origin pages derive it from the page location.
export function wsUrl(cfg, loc, session) {
  const q = session ? '?session=' + encodeURIComponent(session) : '';
  if (cfg.wsBase) return cfg.wsBase + '/ws' + q;
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  const path = loc.pathname.replace(/[^/]*$/, '');
  return `${proto}//${loc.host}${path}ws${q}`;
}

// The same page with the live switch on or off (?live=1), other parameters kept.
export function withLive(href, on) {
  const u = new URL(href);
  if (on) u.searchParams.set('live', '1');
  else u.searchParams.delete('live');
  return u.toString();
}
