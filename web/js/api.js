// Data source: a recorded session (static export) or a live studio server.
// A recorded page can switch to a live server (?live=1) when config.js names one;
// if it cannot be reached the page keeps showing the recordings and says so.
import { resolveConfig, apiUrl, wsUrl, withLive } from './config.js';
import { isStale } from './identity.js';

export const config = resolveConfig(window.MIRROR_CONFIG, window.location);
export let isStatic = config.recordings;          // final value is set by init()
export const repoUrl = config.repo;
const base = config.base;
const rolloutCache = new Map();
const SKEY = 'mirror-session:' + (config.apiBase || location.origin);
let session = null;
let token = null;        // capability token for the session (never put in share URLs)
let generation = '';     // generation of the latest applied snapshot
export function setGeneration(g) { generation = g || ''; }
export function currentGeneration() { return generation; }

// ------------------------------------------------------------ live status
// state: off | connecting | waking | live | unavailable | busy
export const live = { requested: config.wantLive, state: config.wantLive ? 'connecting' : 'off', detail: '', health: null, notice: '' };
const statusListeners = new Set();
export function onLiveStatus(fn) { statusListeners.add(fn); return () => statusListeners.delete(fn); }
function setLive(patch) { Object.assign(live, patch); statusListeners.forEach((fn) => fn({ ...live })); }
export function liveHref(on) { return withLive(location.href, on); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchTimeout(url, opts = {}, ms = 0) {
  if (!ms) return fetch(url, opts);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ctl.signal }); } finally { clearTimeout(timer); }
}

class ApiError extends Error {
  constructor(status, code, detail) { super(detail || `${status}`); this.status = status; this.code = code; }
}

async function readError(r) {
  let data = null;
  try { data = await r.json(); } catch (_) { /* not JSON */ }
  return new ApiError(r.status, data && data.error, (data && data.detail) || `${r.status} ${r.statusText}`);
}

async function newSession() {
  const r = await fetchTimeout(apiUrl(config, 'session'), { method: 'POST' }, 15000);
  if (!r.ok) throw await readError(r);
  const body = await r.json();
  session = body.session;
  token = body.token || null;
  try {
    sessionStorage.setItem(SKEY, session);
    if (token) sessionStorage.setItem(SKEY + ':token', token); else sessionStorage.removeItem(SKEY + ':token');
  } catch (_) { /* storage disabled */ }
  return session;
}

const resetListeners = new Set();
export function onSessionReset(fn) { resetListeners.add(fn); return () => resetListeners.delete(fn); }
async function resetSession(reason) {
  await newSession();
  rolloutCache.clear();
  setLive({ notice: reason });
  resetListeners.forEach((fn) => fn(reason));
}

// One request to the live server, with the session header. A session that has
// ended is replaced once, and the caller is told through onSessionReset.
async function request(path, opts = {}, retry = true) {
  const headers = { ...(opts.headers || {}), 'X-Mirror-Session': session || '' };
  if (token) headers['X-Mirror-Token'] = token;
  const method = (opts.method || 'GET').toUpperCase();
  // Mutations name the generation they were made against; the server refuses stale ones (409).
  if (method !== 'GET' && generation && !generation.startsWith('legacy:')) headers['X-Mirror-Generation'] = generation;
  const r = await fetch(apiUrl(config, path), { ...opts, headers, cache: 'no-store' });
  if (r.ok) return r;
  const err = await readError(r);
  if (retry && ((r.status === 404 && err.code === 'session_expired') || (r.status === 401 && err.code === 'token_invalid'))) {
    await resetSession('Your live session ended after a period of inactivity, so a new one was started.');
    return request(path, opts, false);
  }
  if (err.code === 'server_busy') err.message = 'The live server is busy. ' + err.message;
  if (err.code === 'rate_limited') {
    const s = Number(r.headers.get('Retry-After')) || 0;
    err.message = 'Too many requests from this connection.' + (s ? ` Try again in ${s} s.` : ' Try again shortly.');
  }
  if (err.code === 'stale_generation') {
    err.message = 'The demonstration changed since this page last updated, so nothing was applied. The current one is shown now.';
    staleListeners.forEach((fn) => fn(err));
  }
  if (err.code === 'evaluation_fenced') err.message = 'That layout belongs to a frozen evaluation set, so it cannot be used for a challenge. ' + err.message;
  throw err;
}

const staleListeners = new Set();
export function onStaleGeneration(fn) { staleListeners.add(fn); return () => staleListeners.delete(fn); }

// Decide recorded or live before the first render.
export async function init() {
  if (!config.wantLive) { isStatic = true; return live; }
  const t0 = Date.now();
  let attempt = 0;
  for (;;) {
    try {
      const r = await fetchTimeout(apiUrl(config, 'health'), { cache: 'no-store' }, config.healthTimeoutMs);
      if (!r.ok) throw new Error(`${r.status}`);
      live.health = await r.json();
      break;
    } catch (e) {
      attempt += 1;
      if (Date.now() - t0 > config.wakeTimeoutMs) {
        if (!config.recordings) throw new Error('the studio server is not answering');
        isStatic = true;
        setLive({ state: 'unavailable', detail: 'Live server unavailable, showing recordings.' });
        return live;
      }
      setLive({ state: 'waking', detail: 'Live server waking up…' });
      await sleep(Math.min(1500 * attempt, 5000));
    }
  }
  try {
    session = (() => { try { return sessionStorage.getItem(SKEY); } catch (_) { return null; } })();
    token = (() => { try { return sessionStorage.getItem(SKEY + ':token'); } catch (_) { return null; } })();
    if (!session) await newSession();
  } catch (e) {
    if (!config.recordings) throw e;
    isStatic = true;
    setLive({ state: e.code === 'server_busy' ? 'busy' : 'unavailable',
      detail: e.code === 'server_busy' ? 'Live server busy with other visitors, showing recordings.' : 'Live server unavailable, showing recordings.' });
    return live;
  }
  isStatic = false;
  setLive({ state: 'live', detail: '' });
  return live;
}

// ------------------------------------------------------------------ data

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) { r.body?.cancel?.().catch(() => {}); throw new Error(`${r.status} ${r.statusText}: ${url}`); }
  return r.json();
}

let exportId = '';
export async function getState() {
  if (!isStatic) return (await request('state')).json();
  const s = await getJSON(base + 'session.json');
  exportId = s.export_id || '';
  return s;
}

// Read a JSON body while reporting received bytes (large rollout files).
async function readProgress(r, onProgress) {
  const total = Number(r.headers.get('content-length')) || 0;
  if (!r.body || !onProgress) return r.json();
  const reader = r.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onProgress(got, total);
  }
  const buf = new Uint8Array(got);
  let o = 0;
  for (const c of chunks) { buf.set(c, o); o += c.length; }
  return JSON.parse(new TextDecoder().decode(buf));
}

const listeners = new Set();
export function onRolloutProgress(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export class StaleResponse extends Error {
  constructor(what) { super(`Ignored a late ${what} from an earlier demonstration.`); this.stale = true; }
}

// Playback payload of a rollout. `key` is its immutable cache key (identity.rolloutCacheKey);
// `expect` is the generation the caller is showing: a payload from another generation is
// rejected and not cached, so an old demonstration can never appear as the new one's result.
export async function getRollout(id, key = id, expect = '') {
  if (rolloutCache.has(key)) return rolloutCache.get(key);
  const report = (got, total) => listeners.forEach((fn) => fn(id, got, total));
  const fetchIt = async () => {
    if (isStatic) {
      const url = `${base}rollouts/${encodeURIComponent(id)}.json` + (exportId ? `?v=${exportId}` : '');
      const r = await fetch(url);
      if (!r.ok) { r.body?.cancel?.().catch(() => {}); throw new Error(`${r.status} ${r.statusText}: ${url}`); }
      return readProgress(r, report);
    }
    return readProgress(await request(`rollouts/${encodeURIComponent(id)}`), report);
  };
  const p = fetchIt()
    .then((v) => {
      report(-1, 0);
      if (isStale(expect, v)) throw new StaleResponse('rollout');
      return v;
    })
    .catch((e) => { rolloutCache.delete(key); report(-1, 0); throw e; });
  rolloutCache.set(key, p);
  return p;
}

// Warm the cache in the background (recorded pages: the next step's rollouts).
// Keys whose background fetch failed are not retried in the background (each render calls
// prefetch; retrying a missing file on every render would loop). A direct load still retries.
const prefetchFailed = new Set();
export function prefetch(ids, keyOf = (x) => x) {
  const run = () => ids.filter((x) => x && !rolloutCache.has(keyOf(x)) && !prefetchFailed.has(keyOf(x))).slice(0, 2)
    .forEach((x) => getRollout(x, keyOf(x)).catch(() => prefetchFailed.add(keyOf(x))));
  (window.requestIdleCallback || ((f) => setTimeout(f, 800)))(run);
}

export function forgetRollouts() { rolloutCache.clear(); }
export function forget(key) { rolloutCache.delete(key); }

export async function postBinary(path, blob, type) {
  if (isStatic) throw new Error('These are recordings: actions need the live simulation.');
  const r = await request(path, { method: 'POST', headers: { 'Content-Type': type }, body: blob });
  return r.json();
}

export async function getJSONApi(path) { return (await request(path)).json(); }

// Playable URL of a demonstration video: a file in the recorded site, or the session's file on the server.
export function videoUrl(v) {
  if (!v) return '';
  if (isStatic) return v.file ? base + 'media/' + v.file : '';
  return apiUrl(config, `video/file/${encodeURIComponent(v.id)}`) + '?session=' + encodeURIComponent(session || '')
    + (token ? '&token=' + encodeURIComponent(token) : '');
}

// A recorded audio file (static site only).
export function mediaUrl(file) { return file ? base + 'media/' + file : ''; }

export async function post(path, body = {}) {
  if (isStatic) throw new Error('These are recordings: actions need the live simulation.');
  const r = await request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
}

// Websocket with automatic reconnect and keep-alive. Returns {send, close}.
export function connect(onMessage) {
  if (isStatic) return { send() {}, close() {} };
  let ws = null, closed = false, delay = 500, ping = null;
  const open = () => {
    ws = new WebSocket(wsUrl(config, location, session) + (token ? '&token=' + encodeURIComponent(token) : ''));
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (_) { /* ignore */ } };
    ws.onopen = () => {
      delay = 500;
      clearInterval(ping);
      ping = setInterval(() => { if (ws.readyState === 1) ws.send('{"type":"ping"}'); }, 25000);
    };
    ws.onclose = async (e) => {
      clearInterval(ping);
      if (closed) return;
      if (e.code === 4404) {
        try { await resetSession('Your live session ended after a period of inactivity, so a new one was started.'); } catch (_) { /* retry below */ }
      }
      setTimeout(open, delay = Math.min(delay * 2, 8000));
    };
  };
  open();
  return {
    send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); },
    close() { closed = true; clearInterval(ping); ws && ws.close(); },
  };
}

// Wait for a background job; progress arrives over the websocket, with polling as a fallback.
export async function waitJob(job, onUpdate) {
  let cur = job;
  while (cur.status === 'queued' || cur.status === 'running') {
    await new Promise((r) => setTimeout(r, 400));
    cur = await (await request(`jobs/${job.id}`)).json();
    onUpdate && onUpdate(cur);
  }
  if (cur.status === 'error') {
    const e = new Error(cur.error || 'job failed');
    if (/stale_generation/.test(cur.error || '')) e.stale = true;
    throw e;
  }
  // A result computed for an earlier demonstration is not shown as the current one's.
  if ((cur.result && cur.result.stale) || isStale(generation, cur)) throw new StaleResponse('job result');
  return cur;
}

// ---------------------------------------------------------- challenge records
// Published records need no session; recorded pages carry the tour under `tour/`.
export async function getRecord(recordId) {
  if (isStatic) return getJSON(`${base}records/${encodeURIComponent(recordId)}.json`);
  const r = await fetch(apiUrl(config, `records/${encodeURIComponent(recordId)}`), { cache: 'no-store' });
  if (!r.ok) throw await readError(r);
  return r.json();
}

export async function getTour() {
  if (isStatic) return getJSON(`${base}tour/record.json`);
  return (await request('tour')).json();
}

// Playback stored with a record, keyed by the record and the rollout's content hash.
export function getRecordRollout(record, rolloutId, sha = '') {
  const key = `record|${record.record_id || 'tour'}|${rolloutId}|${sha}`;
  if (rolloutCache.has(key)) return rolloutCache.get(key);
  const url = record.__tour && isStatic ? `${base}tour/rollouts/${encodeURIComponent(rolloutId)}.json`
    : isStatic ? `${base}records/${encodeURIComponent(record.record_id)}/rollouts/${encodeURIComponent(rolloutId)}.json`
      : apiUrl(config, `records/${encodeURIComponent(record.record_id)}/rollouts/${encodeURIComponent(rolloutId)}`);
  const p = getJSON(url).catch((e) => { rolloutCache.delete(key); throw e; });
  rolloutCache.set(key, p);
  return p;
}
