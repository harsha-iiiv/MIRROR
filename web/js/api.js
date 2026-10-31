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

export async function getRollout(id) {
  if (rolloutCache.has(id)) return rolloutCache.get(id);
  const p = (isStatic ? getJSON(`${base}rollouts/${encodeURIComponent(id)}.json`)
                      : getJSON(`api/rollouts/${encodeURIComponent(id)}`))
    .catch((e) => { rolloutCache.delete(id); throw e; });
  rolloutCache.set(id, p);
  return p;
}

export function forgetRollouts() { rolloutCache.clear(); }

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
