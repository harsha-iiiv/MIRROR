// Phone capture wizard: a guide for recording the supported kitting task on real phones, and a
// review of a processed capture before MIRROR may propose rules from it. Content and thresholds
// come from the phone track (docs/CAMERA_CAPTURE.md); endpoints from docs/CHALLENGE_API.md §6.
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import * as K from './capture_state.js';

const html = htm.bind(h);
const pct = (x) => `${Math.round(100 * x)}%`;

// ------------------------------------------------------------------ illustrations
const svgProps = { class: 'cap-ill', viewBox: '0 0 320 160', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
function IllProps() {
  return html`<svg ...${svgProps} role="img" aria-label="Top-down drawing of the props: mug, box, pen, tray and an upright book">
    <rect x="6" y="6" width="308" height="148" rx="6" stroke-opacity=".35" />
    <circle cx="48" cy="62" r="20" /><path d="M68 56 q10 6 0 14" />${[0, 1, 2, 3, 4].map((i) => html`<circle cx=${40 + (i % 3) * 7} cy=${58 + Math.floor(i / 3) * 8} r="1.3" fill="currentColor" stroke="none" />`)}
    <rect x="100" y="48" width="28" height="28" rx="2" />
    <path d="M152 70 l40 -16" stroke-width="4" /><path d="M152 70 l6 -2.4" stroke-width="5" />
    <rect x="220" y="34" width="76" height="56" rx="4" /><rect x="228" y="42" width="60" height="40" rx="2" stroke-opacity=".5" />
    <rect x="196" y="100" width="6" height="40" rx="1" />
    <g class="cap-cap" stroke="none" fill="currentColor"><text x="48" y="102" text-anchor="middle">mug → cup</text><text x="48" y="114" text-anchor="middle" class="faint">(simulated beads)</text>
    <text x="114" y="102" text-anchor="middle">box → block</text><text x="172" y="88" text-anchor="middle">pen → marker</text>
    <text x="258" y="104" text-anchor="middle">tray → tray</text><text x="236" y="136" text-anchor="middle">book → wall</text></g>
  </svg>`;
}
function IllPrint() {
  return html`<svg ...${svgProps} role="img" aria-label="The printed board with a ruler along the 50 millimetre bar">
    <rect x="40" y="14" width="150" height="132" rx="3" />${[0, 1, 2, 3, 4].flatMap((c) => [0, 1, 2, 3].map((r) => ((c + r) % 2 ? '' : html`<rect x=${50 + c * 26} y=${24 + r * 22} width="26" height="22" fill="currentColor" fill-opacity=".18" stroke="none" />`)))}
    <path d="M60 128 h80" stroke-width="3" /><rect x="52" y="120" width="160" height="16" rx="2" stroke-opacity=".6" />
    ${[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => html`<path d=${`M${60 + i * 10} 120 v${i % 5 ? 4 : 7}`} stroke-opacity=".6" />`)}
    <g class="cap-cap" stroke="none" fill="currentColor"><text x="226" y="132">50 mm ± 1</text><text x="226" y="60">print at 100%</text><text x="226" y="74" class="faint">never "Fit to page"</text></g>
  </svg>`;
}
function IllRig() {
  return html`<svg ...${svgProps} role="img" aria-label="Side view of the rig: top phone 60 to 75 centimetres above, tilted 25 to 35 degrees; side phone 50 to 70 centimetres away, 30 to 40 degrees above the table">
    <path d="M20 130 h280" /><path d="M120 130 v-6 h80 v6" stroke-opacity=".6" />
    <rect x="132" y="16" width="26" height="12" rx="2" transform="rotate(-30 145 22)" /><path d="M145 28 L160 124" stroke-dasharray="3 4" stroke-opacity=".6" />
    <path d="M145 22 v40" stroke-opacity=".35" /><path d="M145 50 a12 12 0 0 0 6 -1" />
    <rect x="262" y="76" width="12" height="24" rx="2" transform="rotate(-35 268 88)" /><path d="M262 92 L190 124" stroke-dasharray="3 4" stroke-opacity=".6" />
    <g class="cap-cap" stroke="none" fill="currentColor"><text x="168" y="40">top: 60–75 cm, 25–35° from vertical</text><text x="168" y="72" text-anchor="start">side: 50–70 cm,</text><text x="168" y="84">30–40° above table</text></g>
  </svg>`;
}
function IllSweep() {
  const pos = [[40, 30, -12], [130, 26, 0], [220, 32, 14], [36, 76, 8], [130, 80, -20], [226, 78, 0], [44, 118, 0], [134, 120, 16], [222, 116, -10]];
  return html`<svg ...${svgProps} role="img" aria-label="Nine board positions covering the corners of the phone frame, some tilted">
    <rect x="16" y="10" width="288" height="140" rx="10" />${pos.map(([x, y, a]) => html`<rect x=${x} y=${y - 14} width="60" height="28" rx="2" transform=${`rotate(${a} ${x + 30} ${y})`} stroke-opacity=".55" />`)}
  </svg>`;
}
function IllTimeline() {
  const marks = [['board', 40], ['sync 1', 100], ['task', 170], ['sync 2', 250]];
  return html`<svg ...${svgProps} role="img" aria-label="Two film strips, top and side, offset in time, with matching board, sync 1, task and sync 2 markers">
    <rect x="20" y="30" width="270" height="26" rx="3" /><rect x="40" y="96" width="270" height="26" rx="3" />
    ${marks.map(([t, x]) => html`<path d=${`M${x} 30 v26`} /><path d=${`M${x + 20} 96 v26`} />${t.startsWith('sync') ? html`<path d=${`M${x} 56 L${x + 20} 96`} stroke-dasharray="3 3" />` : ''}
      <text x=${x + 3} y="24" class="cap-cap" stroke="none" fill="currentColor">${t}</text>`)}
    <text x="20" y="142" class="cap-cap" stroke="none" fill="currentColor">top</text><text x="40" y="90" class="cap-cap faint" stroke="none" fill="currentColor">side</text>
  </svg>`;
}
function IllTag() {
  return html`<svg ...${svgProps} role="img" aria-label="Mug with a small square tag on its side, arrow pointing to the opening">
    <path d="M120 40 v80 q0 14 14 14 h52 q14 0 14 -14 v-80" /><ellipse cx="160" cy="40" rx="40" ry="8" /><path d="M200 60 q22 4 0 40" />
    <rect x="146" y="74" width="28" height="28" /><path d="M160 98 v-18 m-6 6 l6 -6 6 6" />
  </svg>`;
}

const STEPS = [
  { k: 'Gather the props', t: '≈2 min', ill: IllProps, lines: ['Mug with dry rice or beads (never hot drinks)', 'Small box, pen, shallow tray', 'A book standing upright as the obstacle', 'Solid, different colours track best'],
    note: 'MIRROR replaces each object with a simulation proxy. Proxies are not measured physical models.' },
  { k: 'Print and check the target', t: '≈5 min', ill: IllPrint, print: true, lines: ['Print page 1 at 100% / Actual size. Never "Fit to page".', 'Measure the 50 mm bar. Off by more than 1 mm? Fix the printer and reprint.', 'Measure one square (about 30 mm). Keep both numbers.', 'Glue the sheet onto stiff card.'] },
  { k: 'Set up the phones', t: '≈3 min', ill: IllRig, lines: ['Top phone: 60-75 cm above, tilted 25-35° from straight down.', 'Side phone: 50-70 cm away, 30-40° above the table, sees the tray.', '1x lens. Do not zoom. Turn off cropping stabilisation. Lock exposure and focus.', 'One phone only? You can, with reduced results (no tilt, heights assumed while lifted).'] },
  { k: 'Calibrate each lens', t: '≈1 min per phone', ill: IllSweep, lines: ['Record 20-40 s per phone, same mode as the take.', 'Move the board slowly: tilted, near the corners, near and far.', 'Redo after any change of phone, lens, zoom or resolution.'],
    note: 'Accepted when: views ≥ 10, reprojection ≤ 1.0 px, coverage ≥ 60%, tilted views ≥ 3, focal uncertainty ≤ 1%.' },
  { k: 'Record the take', t: '≈1 min', ill: IllTimeline, ordered: true, lines: ['Start recording on both phones.', 'Lay the board flat in the work area for 3 seconds, then remove it.', 'Show the sync card to both phones, turn it face down.', 'Do the task: one object at a time, put each fully down.', 'Show the sync card again, turn it face down.', '(Optional) Lay the board flat again for 3 seconds.', 'Stop both phones. Copy the original files (not through a messaging app).'],
    note: 'Two sync events are required. MIRROR refuses to combine clips that do not share them - they could be two different performances.' },
  { k: 'Optional: tag the mug', t: '≈1 min', ill: IllTag, lines: ['Tape tag 95 flat on the side facing the side phone, arrow towards the opening.', "Without a tag, a round mug's tilt cannot be seen. MIRROR will ask instead of assuming it stayed upright."] },
  { k: 'Review before MIRROR proposes rules', t: '', lines: ['Confirm what each object is and which proxy stands in for it.', 'Confirm tracks that were lost and found again.', 'Check moments MIRROR could only bracket (for example while your hand hid the mug).'] },
];

function PrintCheck({ pc, setPc }) {
  const probs = K.printCheck(pc.bar, pc.square);
  const err = (f) => probs.find((p) => p.field === f);
  const touched = pc.bar !== '' || pc.square !== '';
  return html`<div class="ch-form cap-print">
    <label>Scale bar measured (mm)<input class="num-in" type="number" step="0.1" inputmode="decimal" value=${pc.bar} aria-invalid=${!!(touched && err('bar'))}
      aria-describedby="pc-bar" onInput=${(e) => setPc({ ...pc, bar: e.target.value })} /></label>
    <label>One square measured (mm)<input class="num-in" type="number" step="0.1" inputmode="decimal" value=${pc.square} aria-invalid=${!!(touched && err('square'))}
      aria-describedby="pc-sq" onInput=${(e) => setPc({ ...pc, square: e.target.value })} /></label>
  </div>
  <p id="pc-bar" class=${touched && err('bar') ? 'err' : 'faint'} role=${touched && err('bar') ? 'alert' : null}>${touched && err('bar') ? err('bar').message : 'Both numbers are required before uploading.'}</p>
  ${touched && err('square') ? html`<p id="pc-sq" class="err" role="alert">${err('square').message}</p>` : ''}
  ${touched && !probs.length ? html`<p class="ok-t" role="status">Print scale checked.</p>` : ''}`;
}

function Guide({ pc, setPc }) {
  const [open, setOpen] = useState(0);
  return html`<ol class="cap-steps">${STEPS.map((s, i) => html`<li class=${open === i ? 'open' : ''}>
    <button class="cap-step-h" aria-expanded=${open === i} aria-controls=${'cap-s' + i} onClick=${() => setOpen(open === i ? -1 : i)}>
      <span class="ix">${i + 1}</span><span class="grow">${s.k}</span>${s.t ? html`<span class="faint">${s.t}</span>` : ''}</button>
    ${open === i ? html`<div id=${'cap-s' + i} class="cap-step-b">
      ${s.ill ? html`<${s.ill} />` : ''}
      ${s.ordered ? html`<ol class="cap-timeline">${s.lines.map((l) => html`<li>${l}</li>`)}</ol>` : html`<ul class="plain">${s.lines.map((l) => html`<li>${l}</li>`)}</ul>`}
      ${s.note ? html`<p class="callout">${s.note}</p>` : ''}
      ${s.print ? html`<p class="row"><a class="btn small" href="calibration/mirror_charuco_v1_A4.pdf" download>Target, A4 (PDF)</a><a class="btn small" href="calibration/mirror_charuco_v1_Letter.pdf" download>Target, Letter (PDF)</a></p>
        <p class="faint">Page 2 holds the sync card and the optional tags.</p><${PrintCheck} pc=${pc} setPc=${setPc} />` : ''}
      <div class="row"><button class="btn ghost small" disabled=${i === 0} onClick=${() => setOpen(i - 1)}>Back</button><span class="spacer"></span>
        ${i < STEPS.length - 1 ? html`<button class="btn small" onClick=${() => setOpen(i + 1)}>Next: ${STEPS[i + 1].k}</button>` : ''}</div>
    </div>` : ''}</li>`)}</ol>
    <p class="faint">Processing without the studio: <code>python -m mirror.capture.ingest init my_take</code>, then <code>run my_take</code>.</p>`;
}

// ------------------------------------------------------------------ review of a processed session
function Cover({ statuses, label }) {
  const c = K.coverage(statuses);
  if (!c.n) return html`<p class="faint">${label}: no samples.</p>`;
  return html`<div class="cap-cover"><div class="faint">${label}</div>
    <div class="cap-bar" role="img" aria-label=${`${label}: ${K.COVER_KINDS.map((k) => `${k} ${pct(c[k])}`).join(', ')}`}>
      ${K.COVER_KINDS.map((k) => (c[k] ? html`<span class=${'seg ' + k} style=${`width:${100 * c[k]}%`}></span>` : ''))}</div>
    <div class="cap-legend">${K.COVER_KINDS.map((k) => html`<span><i class=${'seg ' + k}></i>${k} ${pct(c[k])}</span>`)}</div></div>`;
}

function Checks({ rows }) {
  if (!rows.length) return html`<p class="faint">No calibration results.</p>`;
  return html`<div class="cap-table-wrap"><table class="cap-table"><thead><tr><th>Camera</th><th>Check</th><th>Value</th><th>Limit</th><th>Result</th></tr></thead>
    <tbody>${rows.map((r) => html`<tr class=${r.passed === false ? 'fail' : ''}><td>${r.camera} · ${r.group}</td><td title=${r.note || ''}>${r.name}${r.passed === false && r.note ? html`<div class="faint">${r.note}</div>` : ''}</td>
      <td class="num">${r.value == null ? '–' : typeof r.value === 'number' ? +r.value.toFixed(3) : String(r.value)}</td><td class="num">${r.threshold == null ? '–' : r.threshold}</td>
      <td>${r.passed === true ? html`<span class="ok-t">pass</span>` : r.passed === false ? html`<span class="err">rejected</span>` : html`<span class="faint">n/a</span>`}</td></tr>`)}</tbody></table></div>`;
}

export function SessionReview({ session: s, live = false, busy = false, onSubmit = null, onPropose = null }) {
  const [res, setRes] = useState({});
  const [times, setTimes] = useState({});
  const [draft, setDraft] = useState({});
  const st = K.reviewState(s, res);
  const badge = K.evidenceBadge(s);
  const sync = K.syncText(s);
  const rows = K.calibrationRows(s);
  const rejected = rows.filter((r) => r.passed === false);
  const objs = s.reconstruction?.objects || {};
  const resolve = (r, text) => setRes({ ...res, [K.itemKey(r)]: text });
  const setTime = (e) => {
    const t = Number(draft[e.id] ?? e.t);
    setTimes({ ...times, [e.id]: t });
    const next = { ...res };
    for (const r of s.review || []) if (r.event === e.id) next[K.itemKey(r)] = `set to ${t.toFixed(2)} s by the person`;
    setRes(next);
  };
  return html`<div class="cap-review">
    <div class="sec"><div class="row"><span class=${'cap-badge ' + badge.tone}>${badge.text}</span><span class="spacer"></span><span class="faint num">${s.session_id || ''} · ${s.mode === 'two_view' ? 'two phones' : 'one phone'}</span></div>
      <p class="faint" style="margin-top:6px">${K.modelProvenance(s)}.</p></div>
    <div class="sec"><p class="ov-k">Calibration</p>
      ${rejected.length ? html`<div class="callout bad" role="alert"><b>${rejected.length} check${rejected.length === 1 ? '' : 's'} rejected.</b> MIRROR will not use a camera whose calibration was rejected.</div>` : html`<p class="ok-t">All calibration checks passed.</p>`}
      <details class="ov-details" open=${!!rejected.length}><summary>All checks (${rows.length})</summary><${Checks} rows=${rows} /></details>
      ${s.print_check ? html`<p class="faint">Print check: bar ${s.print_check.measured_bar_mm} mm, square ${s.print_check.measured_square_mm} mm (${s.print_check.measured_by || 'person'}).</p>` : ''}</div>
    <div class="sec"><p class="ov-k">${s.mode === 'two_view' ? 'Synchronisation' : 'Single view'}</p>
      <p class=${sync.ok === false ? 'err' : ''}>${sync.text}</p>
      ${s.mode === 'single_view' ? html`<p class="faint">Single-view fallback: tilt is not reported and positions while lifted use assumed heights.</p>` : ''}</div>
    <div class="sec"><p class="ov-k">Objects and their simulation proxies</p>
      ${(s.mappings || []).map((m) => {
        const o = objs[m.track_label] || {};
        return html`<div class="cap-obj"><div class="row"><b>${m.real_identity}</b><span class="faint">→</span><span class="chip">${m.proxy}</span><span class="spacer"></span>
          ${m.confirmed_by_person ? html`<span class="faint">confirmed by the person</span>` : html`<span class="err">not confirmed</span>`}</div>
          <p class="muted">${K.fidelityStatement(m)}</p>
          <${Cover} statuses=${o.pos_status} label="Position" /><${Cover} statuses=${o.tilt_status} label="Tilt" />
        </div>`;
      })}
      ${!(s.mappings || []).length ? html`<p class="err">No object mapping yet.</p>` : ''}</div>
    <div class="sec"><p class="ov-k">Pick-up and put-down</p>
      ${(s.events || []).map((e) => {
        const w = (e.t_hi ?? e.t) - (e.t_lo ?? e.t);
        const set = times[e.id];
        return html`<div class="cap-event"><div><b>${K.eventText(e)}</b>${e.placed_in ? html` <span class="faint">in the ${e.placed_in}</span>` : ''}</div>
          <div class="faint">${K.EVENT_LABEL}${e.occluded_frames ? ` · ${e.occluded_frames} frames hidden` : ''}</div>
          ${w > 0.05 ? html`<div class="row cap-scrub"><label class="sr-only" for=${'t-' + e.id}>Time for ${e.id}</label>
            <input id=${'t-' + e.id} type="range" min=${e.t_lo} max=${e.t_hi} step="0.05" value=${draft[e.id] ?? set ?? e.t} onInput=${(ev) => setDraft({ ...draft, [e.id]: ev.target.value })} />
            <span class="num">${Number(draft[e.id] ?? set ?? e.t).toFixed(2)} s</span><button class="btn small" onClick=${() => setTime(e)}>Set time</button></div>` : ''}
          ${set != null ? html`<div class="ok-t">Set to ${set.toFixed(2)} s by you.</div>` : ''}</div>`;
      })}</div>
    <div class="sec"><p class="ov-k">Needs your review</p>
      ${st.items.length ? '' : html`<p class="ok-t">Nothing to review.</p>`}
      ${st.items.map((r) => html`<div class=${'callout ' + (r.severity === 'abstain' && !r.resolved ? 'bad cap-block' : '')} role=${r.severity === 'abstain' && !r.resolved ? 'alert' : null}>
        ${r.severity === 'abstain' && !r.resolved ? html`<div class="faint">Needs your review before MIRROR proposes rules</div>` : ''}
        <b>${r.title}</b>${r.object ? html` <span class="faint">· ${r.object}</span>` : ''}<div>${r.message}</div>
        ${r.resolved ? html`<div class="ok-t">Resolved${r.resolution ? `: ${r.resolution}` : ''}.</div>`
          : K.NEEDS_NEW_TAKE[r.code] ? html`<div class="muted">${K.NEEDS_NEW_TAKE[r.code]}</div>`
          : r.code === 'ambiguous_event_interval' ? html`<div class="muted">Set the time on the event above.</div>`
          : K.RESOLVABLE.has(r.code) ? html`<div class="row" style="margin-top:6px"><button class="btn small" onClick=${() => resolve(r, 'checked by the person')}>I checked this</button></div>` : ''}
      </div>`)}
      <div class="row" style="margin-top:10px">
        ${st.ready ? html`<span class="ok-t">Ready: nothing blocks a proposal.</span>` : html`<span class="err">${st.blocking.length} item${st.blocking.length === 1 ? '' : 's'} must be resolved first.</span>`}
        <span class="spacer"></span>
        ${live && onSubmit ? html`<button class="btn" disabled=${busy} onClick=${() => onSubmit(K.reviewPayload(s, res, times))}>Save review</button>` : ''}
        <button class="btn primary" disabled=${!st.ready || !live || busy || badge.tone === 'synthetic'} onClick=${() => onPropose && onPropose()}>Propose rules from this capture</button></div>
      ${badge.tone === 'synthetic' ? html`<p class="faint">Synthetic sample: shown to explain the review. Nothing is proposed from it.</p>` : !live ? html`<p class="faint">Proposing needs the live studio.</p>` : ''}
    </div>
  </div>`;
}

// ------------------------------------------------------------------ upload (live studio only)
const ROLES = { two_view: ['top', 'side', 'calibration'], single_view: ['single', 'calibration'] };
function Upload({ ctx, pc }) {
  const [mode, setMode] = useState('two_view');
  const [files, setFiles] = useState({});
  const [sync, setSync] = useState({ method: 'visual_marker', top_t: '', side_t: '' });
  const [cap, setCap] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const printOk = !K.printCheck(pc.bar, pc.square).length;
  const roles = ROLES[mode];
  const ready = printOk && roles.every((r) => files[r]) && (mode === 'single_view' || (sync.top_t !== '' && sync.side_t !== ''));
  const run = async (label, fn) => {
    setBusy(true); setMsg(null);
    try { await fn(); } catch (e) { setMsg(`${label}: ${e.message || e}`); } finally { setBusy(false); }
  };
  const refresh = async (id) => setCap(await api.getJSONApi(`capture/${encodeURIComponent(id)}`));
  const send = () => run('Upload', async () => {
    const c = await api.post('capture', { mode, notes: '', print_check: { measured_bar_mm: Number(pc.bar), measured_square_mm: Number(pc.square), measured_by: 'person' } });
    const clips = {};
    for (const r of roles) {
      const up = await api.postBinary(`video/upload?view=${r}`, files[r], files[r].type || 'video/mp4');
      clips[r] = up.id || up.video_id;
    }
    await api.post(`capture/${encodeURIComponent(c.id)}/clips`, { clips, sync: mode === 'two_view' ? { ...sync, top_t: Number(sync.top_t), side_t: Number(sync.side_t) } : null });
    await refresh(c.id);
  });
  const step = (s) => run(s === 'calibrate' ? 'Calibration' : s === 'track' ? 'Tracking' : 'Proposal', async () => {
    const j = await api.post(`capture/${encodeURIComponent(cap.id)}/${s}`, {});
    if (j && j.status && j.id) await api.waitJob(j);
    await refresh(cap.id);
  });
  const review = (body) => run('Review', async () => { await api.post(`capture/${encodeURIComponent(cap.id)}/review`, body); await refresh(cap.id); });
  const hooks = cap?.hooks || {};
  return html`<div>
    ${!printOk ? html`<p class="callout">Check the print scale first (guide step 2).</p>` : ''}
    <div class="ch-form">
      <label>Phones<select value=${mode} onChange=${(e) => { setMode(e.target.value); setFiles({}); }}>
        <option value="two_view">Two phones (top and side)</option><option value="single_view">One phone (reduced results)</option></select></label>
      ${roles.map((r) => html`<label>${{ top: 'Top take', side: 'Side take', single: 'Take', calibration: 'Lens calibration clip' }[r]}
        <input type="file" accept="video/*" onChange=${(e) => setFiles({ ...files, [r]: e.target.files[0] })} /></label>`)}
    </div>
    ${mode === 'two_view' ? html`<div class="ch-form">
      <label>Sync event<select value=${sync.method} onChange=${(e) => setSync({ ...sync, method: e.target.value })}>
        <option value="visual_marker">sync card</option><option value="clap">clap</option><option value="flash">flash</option><option value="manual">manual</option></select></label>
      <label>First sync in top clip (s)<input class="num-in" type="number" step="0.01" value=${sync.top_t} onInput=${(e) => setSync({ ...sync, top_t: e.target.value })} /></label>
      <label>First sync in side clip (s)<input class="num-in" type="number" step="0.01" value=${sync.side_t} onInput=${(e) => setSync({ ...sync, side_t: e.target.value })} /></label></div>
      <p class="faint">The studio records the sync you declare and never calls it verified; clips that do not look like one take are refused.</p>` : html`<p class="faint">One phone: depth along the camera axis and tilt about it are not observed.</p>`}
    <div class="row"><span class="spacer"></span><button class="btn primary" disabled=${!ready || busy} onClick=${send}>Upload and check clips</button></div>
    ${msg ? html`<p class="err" role="alert">${msg}</p>` : ''}
    ${cap ? html`<div class="sec"><p class="faint num">capture ${cap.id} · ${cap.state}${cap.sync ? ` · sync ${cap.sync.method}, offset ${cap.sync.offset_s} s (${cap.sync.status})` : ''}</p>
      ${(cap.errors || []).map((e) => html`<p class="err">${e}</p>`)}
      <div class="row">
        <button class="btn small" disabled=${busy || !hooks.calibrate} onClick=${() => step('calibrate')}>Calibrate</button>
        <button class="btn small" disabled=${busy || !hooks.track || !['calibrated', 'tracked', 'reviewed'].includes(cap.state)} onClick=${() => step('track')}>Track</button></div>
      ${!hooks.calibrate || !hooks.track ? html`<p class="muted">Processing is not available on this server yet (${['calibrate', 'track', 'reconstruct'].filter((x) => !hooks[x]).join(', ')} missing). Use the command-line path in the guide.</p>` : ''}
      ${cap.calibration ? html`<p>${cap.calibration.ok ? html`<span class="ok-t">Calibration accepted.</span>` : html`<span class="err">Calibration rejected: ${(cap.calibration.errors || []).join('; ')}</span>`}</p>` : ''}
      ${cap.session ? html`<${SessionReview} session=${cap.session} live=${true} busy=${busy} onSubmit=${review} onPropose=${() => step('propose')} />` : ''}
    </div>` : ''}
  </div>`;
}

// ------------------------------------------------------------------ entry
export function CapturePanel({ ctx }) {
  const [tab, setTab] = useState(new URLSearchParams(location.search).get('capture') === 'sample' ? 'review' : 'guide');
  const [pc, setPc] = useState({ bar: '', square: '' });
  const [sample, setSample] = useState(undefined);
  useEffect(() => { api.getDataFile('capture/synthetic_session.json').then(setSample).catch(() => setSample(null)); }, []);
  return html`
    <div class="sec">
      <p class="kicker">Phone capture</p>
      <h2>Show MIRROR the task with real phones</h2>
      <div class="cap-honesty" role="note"><b>Honest status:</b> ${K.HONESTY} <span class="cap-status">Real-footage validation: ${K.REAL_FOOTAGE_STATUS}.</span></div>
      <div class="seg ov-seg" role="tablist" aria-label="Capture">
        <button role="tab" aria-selected=${tab === 'guide'} aria-pressed=${tab === 'guide'} onClick=${() => setTab('guide')}>Guide</button>
        <button role="tab" aria-selected=${tab === 'review'} aria-pressed=${tab === 'review'} onClick=${() => setTab('review')}>Upload & review</button></div>
    </div>
    ${tab === 'guide' ? html`<div class="sec"><${Guide} pc=${pc} setPc=${setPc} /></div>` : html`
      ${!api.isStatic ? html`<div class="sec"><p class="ov-k">Upload a capture</p><${Upload} ctx=${ctx} pc=${pc} /></div>`
        : html`<div class="sec"><p class="muted">Uploading needs the live studio. The guide works offline.</p></div>`}
      ${sample ? html`<div class="sec"><p class="ov-k">Sample review <span class="cap-badge synthetic">SYNTHETIC</span></p>
        <p class="muted">A rendered test take, processed by the offline pipeline, to show what you will review. It is not real footage and not evidence about phones.</p></div>
        <${SessionReview} session=${sample} live=${false} />` : sample === undefined ? html`<div class="sec"><p class="faint">Loading…</p></div>` : ''}`}`;
}
