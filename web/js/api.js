// Data source: the live studio server, or a static export (no backend).
const cfg = window.MIRROR_CONFIG || {};
export const isStatic = !!cfg.static;
const base = cfg.base || 'data/';
const rolloutCache = new Map();

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${url}`);
  return r.json();
}

export async function getState() {
  return isStatic ? getJSON(base + 'session.json') : getJSON('api/state');
}

// Fetch JSON while reporting received bytes (for large rollout files).
async function getJSONProgress(url, onProgress) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${url}`);
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

export async function getRollout(id) {
  if (rolloutCache.has(id)) return rolloutCache.get(id);
  const url = isStatic ? `${base}rollouts/${encodeURIComponent(id)}.json` : `api/rollouts/${encodeURIComponent(id)}`;
  const report = (got, total) => listeners.forEach((fn) => fn(id, got, total));
  const p = getJSONProgress(url, report)
    .then((v) => { report(-1, 0); return v; })
    .catch((e) => { rolloutCache.delete(id); report(-1, 0); throw e; });
  rolloutCache.set(id, p);
  return p;
}

// Warm the cache in the background (static site: fetch the next step's rollouts while idle).
export function prefetch(ids) {
  const run = () => ids.filter((x) => x && !rolloutCache.has(x)).slice(0, 2).forEach((x) => getRollout(x).catch(() => {}));
  (window.requestIdleCallback || ((f) => setTimeout(f, 800)))(run);
}

export const repoUrl = cfg.repo || null;

export function forgetRollouts() { rolloutCache.clear(); }
export function forget(id) { rolloutCache.delete(id); }

export async function post(path, body = {}) {
  if (isStatic) throw new Error('This is a static export: actions need the live studio.');
  const r = await fetch('api/' + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

// Websocket with automatic reconnect. Returns a send function.
export function connect(onMessage) {
  if (isStatic) return { send() {}, close() {} };
  let ws = null, closed = false, delay = 500;
  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const path = location.pathname.replace(/[^/]*$/, '');
    ws = new WebSocket(`${proto}//${location.host}${path}ws`);
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (_) { /* ignore */ } };
    ws.onopen = () => { delay = 500; };
    ws.onclose = () => { if (!closed) setTimeout(open, delay = Math.min(delay * 2, 8000)); };
  };
  open();
  return {
    send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); },
    close() { closed = true; ws && ws.close(); },
  };
}

// Wait for a background job; progress arrives over the websocket, with polling as a fallback.
export async function waitJob(job, onUpdate) {
  let cur = job;
  while (cur.status === 'queued' || cur.status === 'running') {
    await new Promise((r) => setTimeout(r, 300));
    cur = await getJSON(`api/jobs/${job.id}`);
    onUpdate && onUpdate(cur);
  }
  if (cur.status === 'error') throw new Error(cur.error || 'job failed');
  return cur;
}
