// Video demonstrations, spoken corrections and the Models panel.
import { h } from 'preact';
import { useState, useEffect, useRef, useMemo } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { fmtT } from './util.js';

const html = htm.bind(h);

export const OBJ_COLORS = { cup: '#3d63ff', block: '#e07b39', marker: '#16a394', tray: '#8a8a93', wall: '#6b6b74' };
export const colorFor = (id) => OBJ_COLORS[String(id || '').split('_')[0]] || '#b04fd6';

// Frame of a track at time t: the latest sample at or before t (no interpolation).
function frameAt(list, t) {
  if (!list || !list.length) return null;
  let lo = 0, hi = list.length - 1;
  if (t < list[0].t) return null;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (list[mid].t <= t) lo = mid; else hi = mid - 1; }
  return t - list[lo].t > 0.6 ? null : list[lo];
}

function drawOverlay(canvas, video, track, t) {
  const ctx = canvas.getContext('2d');
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  if (!track) return;
  const vw = track.width || video.videoWidth || W, vh = track.height || video.videoHeight || H;
  const s = Math.min(W / vw, H / vh), ox = (W - vw * s) / 2, oy = (H - vh * s) / 2;   // object-fit: contain
  const statics = new Set(track.static || []);
  for (const [id, list] of Object.entries(track.objects || {})) {
    const f = statics.has(id) ? list[0] : frameAt(list, t);
    if (!f) continue;
    const c = colorFor(id);
    ctx.fillStyle = c + '59';
    ctx.strokeStyle = c;
    ctx.lineWidth = 2;
    if (f.polygon && f.polygon.length > 2) {
      ctx.beginPath();
      f.polygon.forEach(([x, y], i) => (i ? ctx.lineTo(ox + x * s, oy + y * s) : ctx.moveTo(ox + x * s, oy + y * s)));
      ctx.closePath(); ctx.fill(); ctx.stroke();
    } else if (f.grid) {
      const cw = (vw / f.grid.w) * s, ch = (vh / f.grid.h) * s;
      for (const [r, x0, x1] of f.grid.runs) ctx.fillRect(ox + x0 * cw, oy + r * ch, (x1 - x0) * cw + 0.5, ch + 0.5);
    }
    if (f.box) {
      const [x0, y0, x1, y1] = f.box;
      if (!f.polygon && !f.grid) ctx.strokeRect(ox + x0 * s, oy + y0 * s, (x1 - x0) * s, (y1 - y0) * s);
      ctx.font = '600 11px system-ui, sans-serif';
      const label = id.replace(/_\d+$/, '') + (f.score != null ? ' ' + f.score.toFixed(2) : '');
      const tw = ctx.measureText(label).width + 8;
      const lx = ox + x0 * s, ly = Math.max(0, oy + y0 * s - 16);
      ctx.fillStyle = c; ctx.fillRect(lx, ly, tw, 15);
      ctx.fillStyle = '#fff'; ctx.fillText(label, lx + 4, ly + 11);
    }
  }
}

// Picture-in-picture video with the tracked masks, following the 3D playback clock.
export function VideoPane({ vd, t }) {
  const views = Object.keys(vd.views || {});
  const [view, setView] = useState(views[0] || 'top');
  const [masks, setMasks] = useState(true);
  const [big, setBig] = useState(false);
  const [err, setErr] = useState(null);
  const vref = useRef(null), cref = useRef(null);
  const src = api.videoUrl(vd.views[view]);
  const track = vd.tracks?.[view];
  const target = useRef(0);
  const seek = () => {
    const v = vref.current;
    if (!v || v.readyState < 1 || v.seeking) return;
    const tt = Math.max(0, Math.min(target.current, (v.duration || target.current) - 0.05));
    if (Math.abs(v.currentTime - tt) > 0.08) v.currentTime = tt;
  };
  useEffect(() => { if (isFinite(t)) { target.current = t; seek(); } }, [t, src]);
  useEffect(() => {
    const v = vref.current, c = cref.current;
    if (!v || !c) return;
    const redraw = () => { drawOverlay(c, v, masks ? track : null, v.currentTime); seek(); };   // catch up with the latest time
    v.addEventListener('loadedmetadata', seek);
    v.addEventListener('seeked', redraw);
    v.addEventListener('loadeddata', redraw);
    const ro = new ResizeObserver(redraw);
    ro.observe(c);
    redraw();
    return () => { v.removeEventListener('loadedmetadata', seek); v.removeEventListener('seeked', redraw); v.removeEventListener('loadeddata', redraw); ro.disconnect(); };
  }, [masks, track, src]);
  return html`<figure class=${'vpip' + (big ? ' big' : '')} aria-label="Demonstration video with tracked objects">
    <div class="vpip-media">
      <video ref=${vref} src=${src} muted playsinline preload="auto" onError=${() => setErr('The video could not be loaded.')}></video>
      <canvas ref=${cref} aria-hidden="true"></canvas>
      ${err ? html`<div class="vpip-err">${err}</div>` : ''}
    </div>
    <figcaption>
      <span class="vpip-title">${vd.fixture ? 'Fixture video' : 'Video'} · ${view} view</span>
      ${views.length > 1 ? views.map((k) => html`<button class="iconbtn" aria-pressed=${k === view} onClick=${() => setView(k)}>${k}</button>`) : ''}
      <button class="iconbtn" aria-pressed=${masks} onClick=${() => setMasks(!masks)}>Masks</button>
      <button class="iconbtn" aria-pressed=${big} onClick=${() => setBig(!big)} aria-label=${big ? 'Smaller video' : 'Larger video'}>${big ? '−' : '+'}</button>
    </figcaption>
  </figure>`;
}

// Grasp/release markers and care cues over the demonstration's duration.
export function ObservationTimeline({ obs, duration, onSeek }) {
  const D = Math.max(duration || 0, ...((obs.events || []).map((e) => e.t || 0)), ...((obs.cues || []).map((c) => c.t_end || c.t || 0)), 1);
  const x = (t) => `${(100 * Math.max(0, Math.min(D, t || 0))) / D}%`;
  const objs = [...new Set([...(obs.events || []).map((e) => e.object_id), ...(obs.cues || []).map((c) => c.object_id)].filter(Boolean))];
  return html`<div class="otl" role="group" aria-label="Observation timeline">
    ${objs.map((o) => html`<div class="otl-row">
      <span class="otl-lab" style=${`color:${colorFor(o)}`}>${String(o).replace(/_\d+$/, '')}</span>
      <div class="otl-track">
        ${(obs.cues || []).filter((c) => c.object_id === o).map((c) => html`<button class=${'otl-cue ' + c.kind} title=${`${c.kind === 'carry' ? 'carried' : c.text} (${fmtT(c.t)}${c.t_end != null ? '–' + fmtT(c.t_end) : ''})${c.evidence ? '\n' + c.evidence : ''}`}
          style=${`left:${x(c.t)};width:${c.t_end != null ? `calc(${x(c.t_end)} - ${x(c.t)})` : '6px'};background:${colorFor(o)}33;border-color:${colorFor(o)}`}
          onClick=${() => onSeek(c.t)} aria-label=${`care cue at ${fmtT(c.t)}: ${c.text}`}></button>`)}
        ${(obs.events || []).filter((e) => e.object_id === o).map((e) => html`<button class=${'otl-ev ' + e.kind} title=${`${e.kind} ${o} at ${fmtT(e.t)}`}
          style=${`left:${x(e.t)};--c:${colorFor(o)}`} onClick=${() => onSeek(e.t)} aria-label=${`${e.kind} ${o} at ${fmtT(e.t)}, jump`}></button>`)}
      </div></div>`)}
    <div class="otl-axis"><span>0:00</span><span>${fmtT(D)}</span></div>
  </div>`;
}

export function VideoObservations({ ctx, vd, models }) {
  const obs = vd.observations || {};
  const seek = (t) => { ctx.setPick('demo'); setTimeout(() => { const v = ctx.viewer.current; if (v) { v.pause(); v.setTime(t); } }, 60); };
  const dur = ctx.snap.demo?.duration;
  const tm = (models || []).find((m) => m.artifact.startsWith('object tracks'));
  const om = (models || []).find((m) => m.artifact === 'video observations');
  return html`<div>
    <p class="credits">${tm ? html`Tracked by <b>${tm.model}</b>` : ''}${om ? html` · observed by <b>${om.model}</b>` : ''}
      ${tm ? html` · <span class=${'src ' + tm.source}>${tm.source}</span>` : ''}</p>
    ${vd.metrics && vd.metrics.centroid_err_cm != null ? html`<p class="metrics-line">Checked against the simulator's ground truth for this rendered video:
      objects found in ${(100 * vd.metrics.found_rate).toFixed(1)}% of frames, mean position error ${vd.metrics.centroid_err_cm.toFixed(2)} cm, ${vd.metrics.events_matched}/${vd.metrics.events_true} grasps and releases (mean ${vd.metrics.event_err_s.toFixed(2)} s off)${vd.metrics.cup_tilt_err_deg != null ? `, cup tilt within ${vd.metrics.cup_tilt_err_deg.toFixed(1)}° on average` : ''}.</p>` : ''}
    ${obs.summary ? html`<p class="lede" style="margin-top:6px">${obs.summary}</p>` : ''}
    <${ObservationTimeline} obs=${obs} duration=${dur} onSeek=${seek} />
    ${(obs.cues || []).some((c) => c.kind === 'care') ? html`<h3>Cues of care</h3><ol class="timeline">${obs.cues.filter((c) => c.kind === 'care').map((c) => html`
      <li><button onClick=${() => seek(c.t)} aria-label=${`${c.text} at ${fmtT(c.t)}, jump`}><span class="t">${fmtT(c.t)}</span>
        <span>${c.text || 'care'}${c.object_id ? html` <b>${c.object_id}</b>` : ''}</span></button></li>`)}</ol>` : ''}
    ${(obs.objects || []).length ? html`<h3>Objects</h3><ul class="objs">${obs.objects.map((o) => html`<li>
      <i style=${`background:${colorFor(o.id || o.kind)}`}></i><b>${o.id || o.kind}</b>
      <span class="muted">${o.matters === false ? 'does not matter' : o.matters ? 'matters' : ''}${o.note ? ' · ' + o.note : ''}</span></li>`)}</ul>` : ''}
    ${(obs.uncertainties || []).length ? html`<h3>Open questions from the video</h3><ul class="plain">${obs.uncertainties.map((u) => html`<li>${u}</li>`)}</ul>` : ''}
    ${(obs.issues || []).length ? html`<p class="faint" style="font-size:12px">Checks on the model's output: ${obs.issues.join('; ')}</p>` : ''}
    ${vd.metrics ? html`<details><summary class="muted" style="cursor:pointer;font-size:12px">Perception metrics per object</summary>
      <pre class="num" style="white-space:pre-wrap;font-size:11px">${JSON.stringify(vd.metrics, null, 1)}</pre></details>` : ''}
  </div>`;
}

export function VideoDemoCard({ ctx }) {
  const { snap } = ctx;
  const cap = snap.capabilities || {};
  const [top, setTop] = useState(null);
  const [side, setSide] = useState(null);
  const [msg, setMsg] = useState(null);
  const job = ctx.busy.video;
  if (ctx.isStatic || !cap.video) {
    return snap.video_demo ? '' : html`<p class="faint" style="font-size:12px">${ctx.isStatic ? 'Video demonstrations run on the live server.' : 'Video demonstrations are not available on this server.'}</p>`;
  }
  const upload = async (file, view) => {
    if (file.size > 40 * 1024 * 1024) throw new Error('The video is larger than 40 MB.');
    const r = await api.postBinary(`video/upload?view=${view}`, file, 'video/mp4');
    return r.id;
  };
  const run = async (bundled) => {
    setMsg(null);
    try {
      let body = { top: 'bundled' };
      if (!bundled) {
        setMsg('Uploading…');
        body = { top: await upload(top, 'top'), side: side ? await upload(side, 'side') : null };
        setMsg(null);
      }
      await ctx.action('video', () => api.post('video/process', body));
      ctx.setPick('demo');
    } catch (e) { setMsg(String(e.message || e)); }
  };
  return html`<div class="vdc">
    <p class="kicker">Use a video demonstration</p>
    <p class="muted" style="font-size:13px">SAM 3.1 tracks the objects, a Muse model reads what happens, and Muse Spark proposes the task from the reconstruction.</p>
    <div class="row" style="margin-top:8px">
      ${cap.bundled_video ? html`<button class="btn" disabled=${!!job} onClick=${() => run(true)}>Use the bundled demo video</button>` : ''}
    </div>
    <div class="vdc-files">
      <label>Top view (MP4)<input type="file" accept="video/mp4" onChange=${(e) => setTop(e.target.files[0] || null)} /></label>
      <label>Side view, optional<input type="file" accept="video/mp4" onChange=${(e) => setSide(e.target.files[0] || null)} /></label>
      <button class="btn small" disabled=${!top || !!job} onClick=${() => run(false)}>Process my video</button>
    </div>
    <p class="faint" style="font-size:11px">Up to 40 MB and 90 s. The table and tray must be visible from above.</p>
    ${msg ? html`<div class="callout" role="status" style="margin-top:6px">${msg}</div>` : ''}
    ${job ? html`<${MiniJob} job=${job} />` : ''}
  </div>`;
}

function MiniJob({ job }) {
  if (job.status === 'queued' && job.queue_position > 0) return html`<div class="job" role="status">Waiting for a free slot: ${job.queue_position} ahead.</div>`;
  return html`<div class="job" role="status" aria-live="polite">${(job.message || job.kind).split('\n')[0]}…
    <div class="progress"><div style=${`width:${Math.round(100 * (job.progress || 0.04))}%`}></div></div></div>`;
}

// ------------------------------------------------------------- voice

// Recorded audio -> 16 kHz mono 16-bit PCM WAV.
export async function toWav16k(blob) {
  const buf = await blob.arrayBuffer();
  const AC = window.AudioContext || window.webkitAudioContext;
  const ac = new AC();
  const audio = await ac.decodeAudioData(buf.slice(0));
  ac.close && ac.close();
  const len = Math.max(1, Math.ceil(audio.duration * 16000));
  const off = new OfflineAudioContext(1, len, 16000);
  const src = off.createBufferSource();
  src.buffer = audio;
  src.connect(off.destination);
  src.start();
  const out = (await off.startRendering()).getChannelData(0);
  const data = new DataView(new ArrayBuffer(44 + out.length * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i++) data.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); data.setUint32(4, 36 + out.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  data.setUint32(16, 16, true); data.setUint16(20, 1, true); data.setUint16(22, 1, true);
  data.setUint32(24, 16000, true); data.setUint32(28, 32000, true); data.setUint16(32, 2, true); data.setUint16(34, 16, true);
  w(36, 'data'); data.setUint32(40, out.length * 2, true);
  for (let i = 0; i < out.length; i++) data.setInt16(44 + i * 2, Math.max(-1, Math.min(1, out[i])) * 0x7fff, true);
  return new Blob([data.buffer], { type: 'audio/wav' });
}

// Push to talk: hold the button (or hold Space/Enter on it) to record, release to transcribe.
export function MicButton({ ctx, onTranscript, disabled }) {
  const [state, setState] = useState('idle');   // idle | recording | working | error
  const [secs, setSecs] = useState(0);
  const [err, setErr] = useState(null);
  const rec = useRef(null), chunks = useRef([]), timer = useRef(null), stream = useRef(null);
  const supported = !!(navigator.mediaDevices && window.MediaRecorder);
  const stopTracks = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };
  useEffect(() => () => { clearInterval(timer.current); stopTracks(); }, []);
  const start = async () => {
    if (state !== 'idle' && state !== 'error') return;
    setErr(null);
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const r = new MediaRecorder(stream.current);
      chunks.current = [];
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = finish;
      r.start();
      rec.current = r;
      setSecs(0); setState('recording');
      const t0 = Date.now();
      timer.current = setInterval(() => { const s = (Date.now() - t0) / 1000; setSecs(s); if (s >= 29.5) stop(); }, 200);
    } catch (e) { setErr(e.name === 'NotAllowedError' ? 'Microphone permission was denied.' : 'No microphone available.'); setState('error'); stopTracks(); }
  };
  const stop = () => { clearInterval(timer.current); if (rec.current && rec.current.state === 'recording') rec.current.stop(); };
  async function finish() {
    stopTracks();
    setState('working');
    try {
      const blob = new Blob(chunks.current, { type: rec.current?.mimeType || 'audio/webm' });
      const wav = await toWav16k(blob);
      const job = await ctx.action('voice', () => api.postBinary('voice', wav, 'audio/wav'));
      if (job && job.text != null) onTranscript(job);
      setState('idle');
    } catch (e) { setErr(String(e.message || e)); setState('error'); }
  }
  if (!supported) return html`<span class="faint" style="font-size:12px">This browser cannot record audio.</span>`;
  const label = state === 'recording' ? `Recording ${secs.toFixed(0)} s, release to send` : state === 'working' ? 'Transcribing…' : 'Hold to speak';
  return html`<span class="mic-wrap">
    <button class=${'btn mic ' + state} disabled=${disabled || state === 'working'} aria-pressed=${state === 'recording'}
      onPointerDown=${(e) => { e.preventDefault(); start(); }} onPointerUp=${stop} onPointerLeave=${() => state === 'recording' && stop()}
      onKeyDown=${(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); start(); } }}
      onKeyUp=${(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); stop(); } }}
      aria-label="Push to talk: hold to record a spoken correction">
      <svg viewBox="0 0 16 16" aria-hidden="true" width="14" height="14"><rect x="5.5" y="1.5" width="5" height="8" rx="2.5" fill="currentColor"/><path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.5" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>
      ${label}</button>
    ${err ? html`<span class="bad-t" role="alert" style="font-size:12px">${err}</span>` : ''}
  </span>`;
}

// ------------------------------------------------------------- models

const SRC_TEXT = { live: 'live call', recorded: 'recorded response', fixture: 'fixture (no model)', 'offline rules': 'offline rules (no model)',
  'offline rules (fallback)': 'offline rules (model unavailable)', unknown: 'unknown' };

export function ModelsPanel({ ctx, onClose }) {
  const { snap } = ctx;
  const [budgets, setBudgets] = useState(null);
  useEffect(() => {
    if (!ctx.isStatic) api.getJSONApi('models').then((m) => setBudgets(m.budgets)).catch(() => {});
    const k = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [snap.revision]);
  const rows = snap.models || [];
  return html`<div class="about models" role="dialog" aria-label="Models used in this session">
    <div class="about-h"><b>Models in this session</b><button class="iconbtn" onClick=${onClose} aria-label="Close">✕</button></div>
    ${rows.length ? html`<table class="mtab"><thead><tr><th>Artifact</th><th>Model</th><th class="num">Latency</th><th>Source</th></tr></thead>
      <tbody>${rows.map((r) => html`<tr title=${r.role}><td><div>${r.artifact}</div><div class="faint">${r.role}</div></td>
        <td class="num">${r.model}</td><td class="num">${r.latency_s ? r.latency_s.toFixed(r.latency_s < 10 ? 2 : 1) + ' s' : '–'}${r.tokens ? html`<div class="faint">${r.tokens.toLocaleString()} tok</div>` : ''}</td>
        <td><span class=${'src ' + String(r.source).split(' ')[0]}>${SRC_TEXT[r.source] || r.source}</span></td></tr>`)}</tbody></table>`
      : html`<p class="muted">Nothing produced by a model yet.</p>`}
    <p class="faint" style="font-size:11px;margin-top:8px">Latency of a live call is measured around the step; for a recorded response it is the latency measured when it was recorded. Recorded responses replay exactly what the model returned${ctx.isStatic ? '; this page makes no model calls' : ''}. No model moves the robot or grades a run.</p>
    ${budgets ? html`<h3>Budgets on this server</h3><ul class="plain" style="font-size:12px">${Object.entries(budgets).filter(([, b]) => b).map(([k, b]) => {
      const left = b.remaining_session ?? b.calls_remaining_session, hour = b.remaining_hour ?? b.calls_remaining_hour;
      if (left == null && hour == null) return html`<li><b>${k}</b>: ${b.mode === 'replay' ? 'recorded responses only' : b.mode === 'offline' ? 'offline rules, no model calls' : 'no limit set'}</li>`;
      return html`<li><b>${k}</b>: ${left ?? '–'} left in this session, ${hour ?? '–'} this hour${b.pays_for ? ` (${b.pays_for})` : ''}</li>`;
    })}</ul>` : ''}
  </div>`;
}
