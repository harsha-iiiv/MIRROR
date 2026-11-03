// Step panels: the story from demonstration to evidence.
import { h } from 'preact';
import { useState, useMemo, useEffect } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { VideoDemoCard, VideoObservations, MicButton } from './media.js';
import { MiniMap } from './minimap.js';
import { fmtT, fmtNum, statusLabel, constraintText, selectorText, provText, sceneLabel, latest, cupOutcome, cap, isAux, isBlockedScene, humanRoute } from './util.js';
export { humanRoute };
import { rolloutCacheKey } from './identity.js';
import { demoSource, videoMediaKind } from './provenance.js';

const html = htm.bind(h);
const TOL = 15;

export const STEPS = [
  { key: 'watch', short: 'Watch', title: 'Watch me: the demonstration' },
  { key: 'interpret', short: 'Interpret', title: 'What MIRROR thinks matters' },
  { key: 'run', short: 'Run', title: 'Now your turn: execute in simulation' },
  { key: 'world', short: 'Change world', title: 'Change the layout and replan' },
  { key: 'correct', short: 'Correct', title: 'Correct one misunderstanding' },
  { key: 'evidence', short: 'Evidence', title: 'Test on layouts it has not seen' },
];

export function correctionVersion(snap) {
  const spec = snap.specs[snap.specs.length - 1];
  if (!spec) return null;
  const recs = (spec.history || []).filter((r) => r.kind === 'correction' || r.kind === 'clarification_answer');
  return recs.length ? recs[recs.length - 1].version : null;
}

export function stepState(snap) {
  const rs = snap.rollouts || [];
  const demoScene = snap.demo?.scene_id;
  const nSpecs = snap.specs.length;
  return {
    watch: { enabled: true, done: nSpecs > 0 },
    interpret: { enabled: !!snap.demo || nSpecs > 0, done: rs.length > 0 },
    run: { enabled: nSpecs > 0, done: rs.some((r) => r.scene_id === demoScene) },
    world: { enabled: rs.length > 0, done: rs.some((r) => r.scene_id !== demoScene) },
    correct: { enabled: rs.length > 0, done: !!correctionVersion(snap) },
    evidence: { enabled: nSpecs >= 2 || !!snap.evaluation, done: !!snap.evaluation },
  };
}

const currentSpec = (snap) => snap.specs[snap.specs.length - 1];
const specV = (snap, v) => snap.specs.find((s) => s.version === v);

// ------------------------------------------------------------ shared bits

const ORD = (n) => n + (n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th');

function JobLine({ job }) {
  if (!job) return null;
  if (job.status === 'queued' && job.queue_position > 0) {
    return html`<div class="job queued" role="status" aria-live="polite">Waiting for a free simulator: you are ${ORD(job.queue_position)} in line.
      <span class="faint">Other visitors' runs are in progress.</span>
      <div class="progress"><div class="indet"></div></div></div>`;
  }
  return html`<div class="job" role="status" aria-live="polite">${job.message ? job.message.split('\n')[0] : job.kind}…
    <div class="progress"><div style=${`width:${Math.round(100 * (job.progress || 0.04))}%`}></div></div></div>`;
}

// The recording behind a spoken correction (recorded sessions keep the audio).
function SpokenNote({ snap, text }) {
  const tr = (snap.transcripts || []).slice().reverse().find((t) => t.text && (t.text === text || (snap.models || []).some((m) => m.input === t.id)));
  if (!tr) return null;
  return html`<div class="spoken"><span class="muted" style="font-size:12px">Spoken, transcribed by <b>${tr.model}</b> (${tr.source}, ${(tr.latency_s || 0).toFixed(1)} s)${tr.text !== text ? '; the text was edited before applying' : ''}.</span>
    ${tr.audio_file ? html`<audio controls preload="none" src=${api.mediaUrl(tr.audio_file)}></audio>` : ''}</div>`;
}

// Says when a step fell back to the offline interpreter, and why.
function FallbackNote({ snap, steps }) {
  const fb = (snap.fallbacks || []).filter((f) => steps.includes(f.step));
  if (!fb.length) return null;
  const last = fb[fb.length - 1];
  const why = (last.used || '').replace(/^offline-rules\s*\(?/, '').replace(/\)$/, '');
  return html`<div class="callout" role="status" style="margin:8px 0">
    <b>Offline interpreter used${last.version ? ` for v${last.version}` : ''}.</b>
    The rule-based interpreter produced this instead of ${snap.llm?.model || 'the model'}${why ? `: ${why}` : ''}.</div>`;
}

function Head({ n, kicker, title, lede }) {
  return html`<div><p class="kicker">${n} · ${kicker}</p><h2>${title}</h2>${lede ? html`<p class="lede">${lede}</p>` : ''}</div>`;
}

function StaticNote({ ctx }) {
  return ctx.isStatic ? html`<p class="faint" style="font-size:12px">Recorded session: actions replay what was recorded in the live studio.</p>` : '';
}

function Chip({ status, children }) { return html`<span class=${'chip ' + (status || '')}>${children || status}</span>`; }

export function ReportView({ r, compact = false }) {
  if (!r) return null;
  const rep = r.report;
  const plan = r.plan;
  return html`<div>
    <div class="row" style="margin-bottom:8px">
      <${Chip} status=${rep.status}>${statusLabel(rep)}<//>
      <span class="muted" style="font-size:12px">${rep.condition || r.id} · spec v${r.spec_version} · ${sceneLabel(r.scene_id)}</span>
    </div>
    ${rep.status === 'planning_failed' ? html`<${NoPlan} rep=${rep} plan=${plan} />` : ''}
    ${(rep.outcomes || []).length ? html`<table class="t">
      <thead><tr><th>Object</th><th>In tray</th><th class="n">Max carry tilt</th><th class="n">Beads kept</th></tr></thead>
      <tbody>${rep.outcomes.map((o) => html`<tr>
        <td>${o.object_id}</td>
        <td>${o.in_target && o.released && o.settled ? html`<span class="ok-t">yes</span>` : html`<span class="bad-t">${!o.in_target ? 'no' : !o.released ? 'not released' : 'not settled'}</span>`}</td>
        <td class=${'n ' + (o.max_transport_tilt_deg > TOL ? 'warn-t' : '')}>${fmtNum(o.max_transport_tilt_deg)}°</td>
        <td class=${'n ' + (o.beads_total && o.beads_in_container < o.beads_total ? 'bad-t' : '')}>${o.beads_total ? `${o.beads_in_container}/${o.beads_total}` : '–'}</td>
      </tr>`)}</tbody></table>` : ''}
    ${(rep.violations || []).map((v) => html`<div class="callout bad" style="margin-top:8px">
      <b>${v.constraint_id}</b> violated by ${v.object_id}: peak ${fmtNum(v.peak)}${v.constraint_id.includes('clear') ? ' m' : '°'},
      ${fmtT(v.t_start)}–${fmtT(v.t_end)}${v.detail ? html`<div class="muted">${v.detail}</div>` : ''}</div>`)}
    ${(rep.forbidden_contacts || []).length ? html`<div class="callout bad" style="margin-top:8px">${rep.forbidden_contacts.length} forbidden contact event(s).</div>` : ''}
    ${!compact && plan ? html`<${RoutesView} plan=${plan} />` : ''}
    ${!compact ? html`<dl class="kv" style="margin-top:10px">
      <dt>Routes</dt><dd>${routesText(r) || '–'}</dd>
      <dt>Planning</dt><dd class="num">${fmtNum(rep.planning_time_s, 2)} s${plan?.planner ? ' · ' + plan.planner : ''}</dd>
      <dt>Simulated</dt><dd class="num">${fmtNum(rep.execution_time_s, 1)} s · ${rep.physics_steps || 0} physics steps</dd>
      <dt>Checker</dt><dd>${rep.checker || '–'}</dd>
      ${(rep.ground_truth_use || []).length ? html`<dt>Ground truth</dt><dd>${rep.ground_truth_use.join('; ')}</dd>` : ''}
      ${(rep.notes || []).length ? html`<dt>Notes</dt><dd>${rep.notes.join('; ')}</dd>` : ''}
    </dl>` : ''}
  </div>`;
}

export function Route({ raw, short = false }) {
  if (!raw) return '–';
  return html`<span class="route" title=${raw}>${humanRoute(raw, short)}</span>`;
}

// Distinct object-carrying routes of a run (transit moves of the empty gripper left out).
function carriedRoutes(rep) {
  const out = [];
  for (const x of rep?.route_labels || []) {
    const raw = String(x).replace(/^route=/, '');
    if (/^(?:(?:cup|block|marker)_\d+:\s*)?transit:/.test(raw)) continue;
    if (!out.includes(raw)) out.push(raw);
  }
  return out;
}

export function routeOf(r, objId) {
  const t = (r?.plan?.transport || []).find((x) => x.object_id === objId);
  return t ? t.route : null;
}

function routesText(r) {
  const t = r?.plan?.transport;
  if (t?.length) return html`${t.map((x, i) => html`${i ? ' · ' : ''}<span title=${x.route}>${x.object_id.replace(/_\d+$/, '')}: ${humanRoute(x.route)}</span>`)}`;
  const c = carriedRoutes(r?.report);
  return c.length ? html`${c.map((x, i) => html`${i ? ' · ' : ''}<${Route} raw=${x} />`)}` : '';
}

function NoPlan({ rep, plan }) {
  const inf = plan?.infeasibility;
  const proven = !!(inf && (inf.proven === true || inf.certificate));
  const reason = rep.plan_reason || plan?.reason || '';
  const head = reason.split(/\s\[|;\s/)[0];
  return html`<div class="callout" style="margin-bottom:8px">
    <b>${proven ? 'No valid plan exists (proven).' : 'The planner found no valid plan.'}</b>
    ${inf?.statement ? html`<div style="margin-top:4px">${inf.statement}</div>` : html`<div style="margin-top:4px">${head}</div>`}
    ${!proven ? html`<div class="muted" style="margin-top:4px">This is not a proof that the layout is impossible.</div>` : ''}
    ${inf?.assumptions?.length ? html`<div class="muted" style="margin-top:6px">Assumes: ${inf.assumptions.join('; ')}.</div>` : ''}
    ${inf?.not_covered?.length ? html`<div class="muted" style="margin-top:2px">Not covered: ${inf.not_covered.join('; ')}.</div>` : ''}
    <div class="muted" style="margin-top:6px">The robot did not move: nothing was executed rather than breaking the rule.</div>
    <details style="margin-top:6px"><summary class="muted" style="cursor:pointer">Planner detail</summary>
      <div style="margin-top:4px;font-size:12px">${reason}</div>
      ${inf ? html`<pre class="num" style="white-space:pre-wrap;font-size:11px;margin:6px 0 0">${JSON.stringify(inf.quantities || inf, null, 1)}</pre>` : ''}
    </details>
  </div>`;
}

function RoutesView({ plan }) {
  if (!plan?.routes?.length) return null;
  return html`<div><h3>Routes considered</h3><ul class="list routes">
    ${plan.routes.map((r) => html`<li>
      <${Chip} status=${r.feasible ? 'success' : 'planning_failed'}>${r.feasible ? 'feasible' : 'rejected'}<//>
      <span class="lab" title=${r.label}>${r.object_id ? r.object_id.replace(/_\d+$/, '') + ' · ' : ''}${humanRoute(r.label)}</span>
      <span class="spacer"></span>
      ${r.feasible ? html`<span class="muted" style="font-size:12px">${r.cost != null ? `cost ${fmtNum(r.cost, 2)}` : ''}</span>`
        : html`<span class="why">${r.rejected_reason || ''}</span>`}
    </li>`)}</ul></div>`;
}

function RunButton({ ctx, label, tags = [], condition, spec_version = null, primary = true, busyKey = 'run' }) {
  const job = ctx.busy[busyKey];
  if (ctx.isStatic) return null;
  return html`<button class=${'btn ' + (primary && !ctx.story ? 'primary' : '')} disabled=${!!job || ctx.isStatic}
    title=${ctx.isStatic ? 'Needs the live studio' : ''}
    onClick=${() => ctx.action(busyKey, () => api.post('run', { spec_version, condition, tags })).then(() => ctx.setPick(null))}>${label}</button>`;
}

function RunsList({ ctx, filter = () => true, title = 'Runs in this session' }) {
  const rs = ctx.snap.rollouts.filter((r) => !isAux(r) && filter(r));
  if (!rs.length) return null;
  const cur = ctx.display?.id;
  return html`<div class="sec"><p class="kicker">${title}</p><ul class="list">${[...rs].reverse().map((r) => html`<li>
    <button class="btn ghost" style="width:100%;text-align:left;padding:0;border-radius:4px" aria-pressed=${cur === r.id} onClick=${() => ctx.setPick(r.id)}>
      <div class="row"><span class="num muted" style="font-size:11px;width:34px">${r.id}</span>
        <span style=${cur === r.id ? 'color:var(--accent);font-weight:550' : 'color:var(--text)'}>${r.report.condition || sceneLabel(r.scene_id)}</span>
        <span class="spacer"></span><${Chip} status=${r.report.status}>${statusLabel(r.report)}<//></div>
      <div class="muted" style="font-size:11px;margin-left:42px">spec v${r.spec_version} · ${sceneLabel(r.scene_id)}</div>
    </button></li>`)}</ul></div>`;
}

// Keyboard teleoperation of the simulated gripper (live studio only).
const KEYS = {
  KeyW: [[1, 0, 0], [0, 0, 0]], KeyS: [[-1, 0, 0], [0, 0, 0]], KeyA: [[0, 1, 0], [0, 0, 0]], KeyD: [[0, -1, 0], [0, 0, 0]],
  KeyR: [[0, 0, 1], [0, 0, 0]], KeyF: [[0, 0, -1], [0, 0, 0]], KeyQ: [[0, 0, 0], [0, 0, 1]], KeyE: [[0, 0, 0], [0, 0, -1]],
  KeyT: [[0, 0, 0], [0, 1, 0]], KeyG: [[0, 0, 0], [0, -1, 0]],
};

function TeleopCard({ ctx, onDone }) {
  const [state, setState] = useState({ t: 0, held: null, open: true });
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true, conn = null, timer = null, open = true, gripCmd = null, waiting = false;
    const down = new Set();
    const onKey = (e) => {
      if (/input|textarea/i.test(document.activeElement?.tagName)) return;
      if (KEYS[e.code]) { e.preventDefault(); e.type === 'keydown' ? down.add(e.code) : down.delete(e.code); }
      if (e.code === 'Space' && e.type === 'keydown' && !e.repeat) { e.preventDefault(); e.stopPropagation(); open = !open; gripCmd = open ? 0.09 : 0.0; }
    };
    (async () => {
      try {
        const init = await api.post('teleop/start');
        if (!alive) return;
        ctx.viewer.current.showLive(init.visual, init.scene, init.frame);
        conn = api.connect((m) => {
          if (m.type === 'teleop_frame') {
            waiting = false;
            ctx.viewer.current.applyBodies(m.frame.bodies);
            setState({ t: m.frame.t, held: m.frame.held, open });
          }
        });
        window.addEventListener('keydown', onKey, true);
        window.addEventListener('keyup', onKey, true);
        timer = setInterval(() => {
          if (waiting) return;
          const dp = [0, 0, 0], dr = [0, 0, 0];
          for (const k of down) { KEYS[k][0].forEach((v, i) => (dp[i] += v * 0.012)); KEYS[k][1].forEach((v, i) => (dr[i] += v * 0.06)); }
          waiting = true;
          conn.send({ type: 'teleop', dpos: dp, drot: dr, grip: gripCmd });
          gripCmd = null;
          setTimeout(() => { waiting = false; }, 400);
        }, 66);
      } catch (e) { setErr(String(e.message || e)); }
    })();
    return () => {
      alive = false;
      ctx.viewer.current?.endLive();
      clearInterval(timer);
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('keyup', onKey, true);
      conn && conn.close();
    };
  }, []);
  return html`<div class="callout" style="margin-top:12px">
    <b>Teleoperating the simulated gripper</b>
    <div class="muted" style="margin-top:4px">W/S and A/D move in the table plane, R/F up and down, Q/E yaw, T/G pitch, Space opens or closes the jaw.
      Physics runs as you drive; nothing is scripted.</div>
    <div class="row" style="margin-top:8px"><span class="num">${fmtT(state.t)}</span>
      <span class="muted">jaw ${state.open ? 'open' : 'closing'}</span>
      <span class="muted">${state.held ? 'holding ' + state.held : 'holding nothing'}</span></div>
    ${err ? html`<div class="err">${err}</div>` : ''}
    <div class="row" style="margin-top:10px">
      <button class="btn primary" onClick=${async () => { ctx.viewer.current?.endLive(); await ctx.action('teleop', () => api.post('teleop/finish')); onDone(); }}>Use as the demonstration</button>
      <button class="btn ghost" onClick=${async () => { ctx.viewer.current?.endLive(); await api.post('teleop/cancel'); onDone(); const pl = await api.getRollout('demo', rolloutCacheKey(ctx.snap, 'demo')); ctx.viewer.current?.loadRollout(pl, { sceneState: ctx.snap.demo?.scene }); }}>Cancel</button>
    </div></div>`;
}

// ---------------------------------------------------------------- 1 Watch

function WatchPanel({ ctx }) {
  const { snap } = ctx;
  const [teleop, setTeleop] = useState(false);
  const d = snap.demo;
  const vd = snap.video_demo && snap.demo && snap.video_demo.trace_source === snap.demo.source ? snap.video_demo : null;

  return html`
    <div class="sec">
      <${Head} n="1" kicker="Watch me" title="The demonstration"
        lede=${vd ? (videoMediaKind(snap) === 'rendered_simulation' ? 'A demonstration video rendered from the simulator: the objects are tracked in its pixels, reconstructed on the table, and replayed in 3D beside the video.' : 'A demonstration video: the objects are tracked in its pixels, reconstructed on the table, and replayed in 3D beside the video.')
          : 'An operator packs the cup, the block and the marker into the tray, carrying everything around the wall.'} />
      ${d ? html`
        <dl class="kv"><dt>Input</dt><dd>${demoSource(snap).short}</dd>
          <dt>Duration</dt><dd class="num">${fmtT(d.duration)}</dd>
          <dt>Layout</dt><dd>${sceneLabel(d.scene_id)}</dd></dl>
        ${vd ? html`<p class="faint" style="font-size:12px;margin-top:8px">The 3D replay is reconstructed from the video${vd.views.side ? ' (top view for positions, side view for tilt)' : ' (top view)'}: object poses come from the tracked masks, not from the simulator.${vd.fixture ? ' Fixture data.' : ''}</p>
          <${VideoObservations} ctx=${ctx} vd=${vd} models=${snap.models} />`
          : html`<p class="faint" style="font-size:12px;margin-top:8px">The demonstration is a recorded simulator trace (gripper and object poses), not video of a person.${d.source_note ? ' ' + d.source_note : ''}</p>`}
        <h3>Events</h3>
        <ol class="timeline">${(d.events || []).filter((e) => e.type === 'grasp' || e.type === 'release').map((e) => html`
          <li><button onClick=${() => { ctx.setPick('demo'); setTimeout(() => ctx.viewer.current?.setTime(e.t), 60); }}
            aria-label=${`${e.type} ${e.object_id} at ${fmtT(e.t)}, jump`}><span class="t">${fmtT(e.t)}</span>
            <span>${cap(e.type)} <b>${e.object_id}</b></span></button></li>`)}</ol>
        <div class="row" style="margin-top:16px">
          ${snap.specs.length && snap.specs[0].demo_id === d.demo_id ? html`<button class=${ctx.story ? 'btn' : 'btn primary'} onClick=${() => ctx.setStep('interpret')}>See the interpretation</button>`
            : html`<button class=${ctx.story ? 'btn' : 'btn primary'} disabled=${!!ctx.busy.interpret || ctx.isStatic}
              onClick=${async () => { await ctx.action('interpret', () => api.post('interpret')); ctx.setStep('interpret'); }}>Interpret this demonstration</button>`}
        </div>
        <${JobLine} job=${ctx.busy.interpret} />
        ${snap.teleop_available && !ctx.isStatic ? (teleop ? html`<${TeleopCard} ctx=${ctx} onDone=${() => setTeleop(false)} />`
          : html`<div class="row" style="margin-top:8px"><button class="btn ghost small" onClick=${() => setTeleop(true)}>Record your own with the keyboard</button></div>`) : ''}`
      : html`<p>No demonstration loaded.</p>
        <div class="row"><button class="btn primary" disabled=${ctx.isStatic || !!ctx.busy.demo}
          onClick=${() => ctx.action('demo', () => api.post('demo/load'))}>Load the reference demonstration</button></div>`}
      <${StaticNote} ctx=${ctx} />
    </div>
    <div class="sec"><${VideoDemoCard} ctx=${ctx} />
      ${!ctx.isStatic && vd ? html`<div class="row" style="margin-top:8px"><button class="btn ghost small" disabled=${!!ctx.busy.demo}
        onClick=${() => ctx.action('demo', () => api.post('demo/load'))}>Back to the recorded teleoperation</button></div>` : ''}</div>`;
}

// ------------------------------------------------------------ 2 Interpret

function ItemRow({ id, title, sub, status, prov, staged, onSet, readonly }) {
  const eff = staged || status;
  const label = (x) => (x === 'confirmed' ? 'enforced' : x);
  return html`<li>
    <div class="item-h">
      <div class="grow">
        <div class="item-title">${title}</div>
        <div class="item-sub">${sub ? html`<span class="num">${sub}</span> · ` : ''}${staged && staged !== status
          ? html`<span class="staged">${label(status)} → ${label(staged)}, not committed</span>`
          : html`<span class=${'st-' + status}>${label(status)}</span>`}</div>
        <div class="prov">${prov}</div>
      </div>
      ${readonly ? '' : html`<div class="seg" role="group" aria-label=${'Decision for ' + id}>
        <button class="c" aria-pressed=${eff === 'confirmed'} onClick=${() => onSet(id, 'confirmed')}>Required</button>
        <button class="r" aria-pressed=${eff === 'rejected'} onClick=${() => onSet(id, 'rejected')}>Not required</button>
      </div>`}
    </div>
  </li>`;
}

function InterpretPanel({ ctx }) {
  const { snap } = ctx;
  const [ver, setVer] = useState(null);
  const latestSpec = currentSpec(snap);
  // A recorded session opens on the proposal itself; the live studio on the current version.
  const want = ver ?? (ctx.isStatic ? snap.specs[0]?.version : latestSpec?.version);
  const spec = specV(snap, want) || latestSpec;
  const isLatest = spec && latestSpec && spec.version === latestSpec.version;
  const [staged, setStaged] = useState({});
  useEffect(() => setStaged({}), [spec?.version]);
  if (!spec) {
    return html`<div class="sec"><${Head} n="2" kicker="Interpret" title="What matters here?" lede="Interpret the demonstration to see the proposed task." />
      <button class="btn primary" disabled=${!!ctx.busy.interpret || ctx.isStatic}
        onClick=${() => ctx.action('interpret', () => api.post('interpret'))}>Interpret this demonstration</button>
      <${JobLine} job=${ctx.busy.interpret} /></div>`;
  }
  const set = (id, st) => setStaged((s) => ({ ...s, [id]: s[id] === st ? undefined : st }));
  const changes = Object.entries(staged).filter(([k, v]) => v);
  const ro = ctx.isStatic || !isLatest;
  return html`
    <div class="sec">
      <${Head} n="2" kicker="Interpret" title="What MIRROR thinks matters" lede=${spec.summary} />
      <div class="row">
        ${snap.specs.length > 1 ? html`<div class="seg" role="group" aria-label="Specification version">${snap.specs.map((sp) => html`
          <button class="c" aria-pressed=${sp.version === spec.version} onClick=${() => setVer(sp.version)}>v${sp.version}</button>`)}</div>`
          : html`<${Chip} status="accent">spec v${spec.version}<//>`}
        <span class="muted" style="font-size:12px">interpreter: ${spec.interpreter || 'unknown'}</span></div>
      ${spec.version === 1 && snap.specs.length > 1 ? html`<p class="faint" style="font-size:12px;margin-top:6px">v1 is the proposal as the interpreter made it.</p>` : ''}
      <${FallbackNote} snap=${snap} steps=${['interpret']} />
      <h3>Goals</h3>
      <ul class="list">${spec.goals.map((g) => html`<${ItemRow} id=${g.id} readonly=${true}
        title=${`${cap(selectorText(g.object))} ends inside the ${g.target} tray`} status=${g.status} prov=${provText(g.provenance)} />`)}</ul>
      <h3>Rules</h3>
      <ul class="list">${spec.constraints.map((c) => html`<${ItemRow} id=${c.id} readonly=${ro}
        title=${constraintText(c)} sub=${c.id} status=${c.status} staged=${staged[c.id]} onSet=${set} prov=${provText(c.provenance)} />`)}</ul>
      ${spec.ordering.length ? html`<h3>Order</h3><ul class="list">${spec.ordering.map((o) => html`<${ItemRow} id=${o.id} readonly=${ro}
        title=${`${cap(selectorText(o.before))} before ${selectorText(o.after)}`} sub=${o.id} status=${o.status} staged=${staged[o.id]} onSet=${set}
        prov=${provText(o.provenance)} />`)}</ul>` : ''}
      ${spec.uncertainties.length ? html`<h3>Open questions</h3><ul class="list">${spec.uncertainties.map((u) => html`<li>
        <div class="item-h"><div class="grow"><div>${u.question}</div>
          ${u.related_constraint ? html`<div class="item-sub">settles ${u.related_constraint}</div>` : ''}</div>
          <${Chip} status=${u.status}>${u.status === 'resolved' ? 'resolved: ' + (u.resolution || '') : 'open'}<//></div></li>`)}</ul>` : ''}
      <p class="faint" style="font-size:12px;margin-top:10px">Only rules marked enforced restrict planning. Proposed rules are shown but not enforced. Hover a row for its provenance.</p>
      <div class="row" style="margin-top:12px">
        ${!ro ? html`<button class="btn" disabled=${!changes.length}
          onClick=${() => ctx.action('decide', () => api.post('spec/decisions', { decisions: Object.fromEntries(changes) }))}>
          Commit ${changes.length || ''} decision${changes.length === 1 ? '' : 's'}${changes.length ? ` → v${spec.version + 1}` : ''}</button>` : ''}
        <span class="spacer"></span>
        <button class=${ctx.story ? 'btn' : 'btn primary'} onClick=${() => ctx.setStep('run')}>Run it</button>
      </div>
      <${SpecHistory} spec=${spec} />
    </div>`;
}

function SpecHistory({ spec }) {
  if (!spec.history?.length) return null;
  return html`<h3>History</h3><ul class="list">${spec.history.map((r) => html`<li>
    <div class="row"><${Chip} status="accent">v${r.version}<//><span class="muted" style="font-size:12px">${r.kind.replace('_', ' ')}</span></div>
    <div style="margin-top:4px">“${r.text}”</div></li>`)}</ul>`;
}

// ------------------------------------------------------------------ 3 Run

function RunPanel({ ctx }) {
  const { snap, display } = ctx;
  const spec = currentSpec(snap);
  const r = display.kind === 'rollout' ? display.rollout : null;
  const onDemoScene = snap.scene?.scene_id === snap.demo?.scene_id;
  return html`
    <div class="sec">
      <${Head} n="3" kicker="Now your turn" title="Execute in simulation"
        lede="The planner turns the enforced rules into a plan; MuJoCo executes it with actuators and contacts; an independent checker reads the physics log." />
      <div class="row">
        ${onDemoScene ? html`<${RunButton} ctx=${ctx} label=${`Plan and run spec v${spec?.version}`} condition="demonstration layout" tags=${['run']} />`
          : html`<button class="btn" disabled=${ctx.isStatic} onClick=${() => ctx.action('scene', () => api.post('scene', { preset: 'demo' }))}>Back to the demonstration layout</button>`}
      </div>
      <${JobLine} job=${ctx.busy.run} />
    </div>
    ${r ? html`<div class="sec"><${ReportView} r=${r} /></div>
      <div class="sec"><p class="lede">Success here could be a recording. Change the world and see whether it replans.</p>
        <button class=${ctx.story ? 'btn' : 'btn primary'} onClick=${() => ctx.setStep('world')}>Change the world</button></div>` : ''}
    <${RunsList} ctx=${ctx} />`;
}

// ---------------------------------------------------------------- 4 World

function WorldPanel({ ctx }) {
  const { snap, display, tool, setTool } = ctx;
  const spec = currentSpec(snap);
  const sc = snap.scene;
  const wall = sc?.obstacles?.find((o) => o.kind === 'wall');
  const hasSlot = !!wall?.slots?.length;
  const r = display.kind === 'rollout' ? display.rollout : null;
  const ro = ctx.isStatic;
  const preset = (name) => ctx.action('scene', () => api.post('scene', { preset: name }));
  const presets = [
    ['demo', 'Demonstration', 'Wall without a slot'],
    ['shortcut', 'Shortcut', 'Tray moved, slot on the direct line'],
    ['blocked', 'Blocked', 'Slot is the only opening'],
  ];
  return html`
    <div class="sec">
      <${Head} n="4" kicker="Change the world" title="Rearrange, then replan"
        lede="Move the tray, open a slot in the wall or add an obstacle. The robot cannot replay the demonstration; it has to plan again." />
      ${ro ? html`<p class="faint" style="font-size:12px">Recorded session: the layout editor needs the live studio. The recorded runs are listed below.</p>` : html`<h3>Layouts</h3>
      <div class="tools-grid">${presets.map(([k, t, s]) => html`<button class="tool" aria-pressed=${sc?.scene_id === k} disabled=${ro}
        onClick=${() => preset(k)}><b>${t}</b><span>${s}</span></button>`)}</div>
      <h3>Edit</h3>
      <div class="tools-grid">
        <button class="tool" aria-pressed=${tool === 'drag'} disabled=${ro} onClick=${() => setTool(tool === 'drag' ? null : 'drag')}>
          <b>Move</b><span>Drag the tray or an object</span></button>
        <button class="tool" aria-pressed=${tool === 'obstacle'} disabled=${ro} onClick=${() => setTool(tool === 'obstacle' ? null : 'obstacle')}>
          <b>Add obstacle</b><span>Click the table</span></button>
        <button class="tool" aria-pressed=${hasSlot} disabled=${ro || !wall}
          onClick=${() => ctx.action('scene', () => api.post('scene', { edit: { slot: hasSlot ? 'off' : 'on' } }))}>
          <b>Slot ${hasSlot ? 'on' : 'off'}</b><span>${hasSlot ? 'Close the slot' : 'Cut a slot on the direct line'}</span></button>
      </div>
      ${sc?.obstacles?.some((o) => o.kind === 'box') ? html`<div class="row" style="margin-top:8px">${sc.obstacles.filter((o) => o.kind === 'box').map((o) => html`
        <button class="btn small" disabled=${ro} onClick=${() => ctx.action('scene', () => api.post('scene', { edit: { remove_obstacle: o.id } }))}>Remove ${o.id}</button>`)}</div>` : ''}`}
      <div style="margin-top:14px"><${MiniMap} scene=${r?.scene || sc} trace=${r?.trace} label="current layout" /></div>
      <div class="row" style="margin-top:12px">
        <span class="muted" style="font-size:12px">${sceneLabel(sc?.scene_id)}</span><span class="spacer"></span>
        <${RunButton} ctx=${ctx} label=${`Replan and run v${spec?.version}`} condition=${'v' + spec?.version + ' · ' + (sc?.scene_id || '')} tags=${['world']} />
      </div>
      <${JobLine} job=${ctx.busy.run || ctx.busy.scene} />
    </div>
    ${r ? html`<div class="sec"><${ReportView} r=${r} /></div>` : ''}
    ${r && /slot|pitch|tilt/i.test(routeOf(r, cupOutcome(r.report)?.object_id) || (r.plan?.transport ? '' : (r.report.route_labels || []).join(' '))) ? html`<div class="sec">
      <p class="lede">It took the short way: through the slot, with the cup on its side.</p>
      <button class=${ctx.story ? 'btn' : 'btn primary'} onClick=${() => ctx.setStep('correct')}>That is not what I meant</button></div>` : ''}
    <${RunsList} ctx=${ctx} />`;
}

// -------------------------------------------------------------- 5 Correct

function SpecDiff({ a, b, record }) {
  const [showAll, setShowAll] = useState(false);
  if (!a || !b) return null;
  const ids = [...new Set([...a.constraints.map((c) => c.id), ...b.constraints.map((c) => c.id)])];
  const touched = new Set((record?.diff || []).map((o) => o.target_id));
  const ord = [...new Set([...a.ordering.map((o) => o.id), ...b.ordering.map((o) => o.id)])];
  const cell = (c, other) => {
    if (!c) return html`<div class="faint">not present</div>`;
    const diffStatus = other && other.status !== c.status;
    const diffPhase = other && other.phase !== c.phase;
    const diffParams = other && JSON.stringify(other.params) !== JSON.stringify(c.params);
    const st = c.status === 'confirmed' ? 'enforced' : c.status;
    return html`<div><div class="id">${c.id}</div>
      <div>${constraintText(c)}</div>
      <div style="margin-top:4px">${diffStatus ? html`<mark>${st}</mark>` : html`<span class="muted">${st}</span>`}
      · ${diffPhase ? html`<mark>${c.phase}</mark>` : html`<span class="muted">${c.phase}</span>`}
      ${diffParams ? html` · <mark>${JSON.stringify(c.params)}</mark>` : ''}</div></div>`;
  };
  const rows = ids.map((id) => {
    const ca = a.constraints.find((c) => c.id === id), cb = b.constraints.find((c) => c.id === id);
    const changed = JSON.stringify(ca) !== JSON.stringify(cb) || touched.has(id);
    return { id, ca, cb, changed };
  });
  const changed = rows.filter((r) => r.changed), same = rows.filter((r) => !r.changed);
  const row = (r) => html`<div class=${'diff-r' + (r.changed ? ' changed' : '')} role="row">
    <div role="cell">${cell(r.ca, null)}</div><div role="cell">${r.cb ? cell(r.cb, r.ca) : html`<div class="faint">removed</div>`}</div></div>`;
  return html`<div class="diff" role="table" aria-label="Specification change">
    <div class="diff-h" role="row"><div role="columnheader">v${a.version}</div><div role="columnheader">v${b.version}</div></div>
    ${changed.map(row)}
    ${ord.map((id) => {
      const oa = a.ordering.find((o) => o.id === id), ob = b.ordering.find((o) => o.id === id);
      if (JSON.stringify(oa) === JSON.stringify(ob)) return '';
      return html`<div class="diff-r changed" role="row"><div role="cell"><div class="id">${id}</div>${oa ? oa.status : '—'}</div>
        <div role="cell"><div class="id">${id}</div>${ob ? html`<mark>${ob.status}</mark>` : '—'}</div></div>`;
    })}
    ${showAll ? same.map(row) : ''}
  </div>
  ${same.length ? html`<button class="btn ghost small" style="margin-top:4px" onClick=${() => setShowAll(!showAll)}>
    ${showAll ? 'Hide' : 'Show'} ${same.length} unchanged rule${same.length === 1 ? '' : 's'}</button>` : ''}
  ${record?.diff?.length ? html`<div class="ops"><h3>Operations</h3><ul class="list">${record.diff.map((o) => html`<li>
    <code>${o.op}</code> <code>${o.target_id || ''}</code>${o.rationale ? html`<div class="item-sub" style="margin-top:4px">${o.rationale}</div>` : ''}</li>`)}</ul>
    ${record.model ? html`<p class="faint" style="font-size:12px">compiled by ${record.model}; validated against the schema before planning</p>` : ''}</div>` : ''}`;
}

function CorrectPanel({ ctx }) {
  const { snap, display } = ctx;
  const spec = currentSpec(snap);
  const cv = correctionVersion(snap);
  const rec = cv ? (spec.history || []).find((r) => r.version === cv) : null;
  const [text, setText] = useState('');
  const [spoken, setSpoken] = useState(null);       // transcript from the mic, if the text came from speech
  const [again, setAgain] = useState(false);
  const job = ctx.busy.correct;
  const before = cv ? specV(snap, cv - 1) || specV(snap, specV(snap, cv)?.parent_version) : null;
  const after = cv ? specV(snap, cv) : null;
  const rechecks = (snap.rechecks || []).filter((x) => x.spec_version === cv && x.report && x.report.status === 'constraint_violated');
  const rs = snap.rollouts;
  const corrected = display.kind === 'rollout' && cv && display.rollout.spec_version >= cv ? display.rollout : null;
  const ghost = corrected && display.ghost ? rs.find((x) => x.id === display.ghost) : null;
  const shownRecheck = rechecks.find((x) => x.rollout_id === ghost?.id) || rechecks[rechecks.length - 1];
  const [reply, setReply] = useState(null);
  const submit = async (t) => {
    setReply(null);
    const res = await ctx.action('correct', () => api.post('correct', { text: t, transcript_id: spoken ? spoken.id : null }));
    if (res && (res.question || res.rejected)) setReply(res);
    else if (res && !res.changed) setReply({ rejected: 'That did not change the specification.' });
    else setAgain(false);
  };
  const input = html`
    <label class="sr-only" for="corr">Correction</label>
    <textarea id="corr" class="corr" placeholder=${snap.reference_correction} value=${text}
      onInput=${(e) => setText(e.target.value)}
      onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) submit(text.trim()); }}></textarea>
    ${spoken ? html`<p class="faint" style="font-size:12px;margin:4px 0 0">${spoken.text === text ? 'Transcribed' : 'Transcribed, then edited'} · ${spoken.model} · ${spoken.source} · ${(spoken.latency_s || 0).toFixed(1)} s for ${spoken.audio_s} s of audio</p>` : ''}
    <div class="row" style="margin-top:8px">
      ${snap.capabilities?.voice ? html`<${MicButton} ctx=${ctx} disabled=${!!job} onTranscript=${(tr) => { setSpoken(tr); setText(tr.text); }} />` : ''}
      <button class="btn ghost small" onClick=${() => { setSpoken(null); setText(snap.reference_correction); }}>Use the example sentence</button>
      <span class="spacer"></span>
      <button class="btn primary" disabled=${!text.trim() || !!job} onClick=${() => submit(text.trim())}>Apply correction</button>
    </div>
    ${ctx.busy.voice ? html`<${JobLine} job=${ctx.busy.voice} />` : ''}
    <${JobLine} job=${job} />
    ${reply?.question ? html`<div class="callout" style="margin-top:10px" role="status"><b>MIRROR needs to know:</b> ${reply.question}
      ${reply.options?.length ? html`<div class="row" style="margin-top:8px">${reply.options.map((o) => html`<button class="btn small"
        onClick=${() => { setText(o); }}>${o}</button>`)}</div>` : ''}
      <div class="muted" style="margin-top:4px">It asks instead of guessing. Nothing changed yet.</div></div>` : ''}
    ${reply?.rejected ? html`<div class="callout" style="margin-top:10px" role="status"><b>Not applied.</b> ${reply.rejected}</div>` : ''}`;
  return html`
    <div class="sec">
      <${Head} n="5" kicker="You misunderstood this" title="Correct one rule"
        lede=${cv ? '' : 'Say what was wrong in plain words. MIRROR compiles it into a scoped change to the specification.'} />
      ${cv && rec ? html`<blockquote class="quote">“${rec.text}”<span class="faint"> → spec v${cv}</span></blockquote>` : ''}
      ${cv && rec ? html`<${SpokenNote} snap=${snap} text=${rec.text} />` : ''}
      <${FallbackNote} snap=${snap} steps=${['correction', 'answer']} />
      ${!ctx.isStatic && (!cv || again) ? input : ''}
      ${!ctx.isStatic && cv && !again ? html`<button class="btn ghost small" onClick=${() => setAgain(true)}>Correct something else</button>` : ''}
    </div>
    ${corrected && ghost ? html`<div class="sec"><${BeforeAfterStrip} a=${ghost} b=${corrected} /></div>` : ''}
    ${cv && !corrected ? html`<div class="sec">
      <p class="lede">Same planner, same controller, new rule. Run it in this layout.</p>
      <${RunButton} ctx=${ctx} label=${`Replan and run v${spec.version}`} condition=${'corrected v' + spec.version} tags=${['corrected']} />
      <${JobLine} job=${ctx.busy.run} />
      ${ctx.isStatic ? html`<p class="muted">No corrected run was recorded in this layout.</p>` : ''}
    </div>` : ''}
    ${cv && before && after ? html`<div class="sec">
      <p class="kicker">What changed in the specification</p>
      <${SpecDiff} a=${before} b=${after} record=${rec} />
    </div>` : ''}
    ${shownRecheck ? html`<div class="sec">
      <p class="kicker">The old behaviour, re-checked</p>
      <${RecheckCard} item=${shownRecheck} snap=${snap} />
    </div>` : ''}
    ${corrected ? html`<div class="sec"><${ReportView} r=${corrected} compact=${true} />
      <div class="row" style="margin-top:12px"><button class="btn" onClick=${() => ctx.setStep('evidence')}>Test somewhere new</button></div></div>` : ''}
    <div class="sec"><${ClarifyCard} ctx=${ctx} /></div>`;
}

// Tilt of the container over time, old behaviour against new, with the tolerance band.
export function TiltChart({ a, b, tol = TOL, height = 132 }) {
  const sa = a?.trace?.tilt || [], sb = b?.trace?.tilt || [];
  if (!sa.length && !sb.length) return null;
  const W = 360, H = height, L = 30, R = 8, T = 10, B = 22;
  const tmax = Math.max(1, ...sa.map((p) => p[0]), ...sb.map((p) => p[0]));
  const ymax = 90;
  const x = (t) => L + ((W - L - R) * t) / tmax;
  const y = (v) => T + (H - T - B) * (1 - Math.min(v, ymax) / ymax);
  const path = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
  const ticks = [0, 15, 45, 90];
  const tstep = tmax > 60 ? 20 : 10;
  return html`<svg class="chart" viewBox=${`0 0 ${W} ${H}`} role="img"
      aria-label=${`Cup tilt over time. Before: peak ${fmtNum(Math.max(0, ...sa.map((p) => p[1])))} degrees. After: peak ${fmtNum(Math.max(0, ...sb.map((p) => p[1])))} degrees. Tolerance ${tol} degrees.`}>
    <rect x=${L} y=${y(tol)} width=${W - L - R} height=${y(0) - y(tol)} fill="var(--accent-soft)" />
    ${ticks.map((v) => html`<g><line x1=${L} x2=${W - R} y1=${y(v)} y2=${y(v)} stroke="var(--line)" stroke-width="1" />
      <text x=${L - 6} y=${y(v) + 3.5} text-anchor="end" class="ax">${v}°</text></g>`)}
    ${Array.from({ length: Math.floor(tmax / tstep) + 1 }, (_, i) => i * tstep).map((t) => html`
      <text x=${x(t)} y=${H - 6} text-anchor="middle" class="ax">${t}s</text>`)}
    <text x=${W - R} y=${y(tol) - 4} text-anchor="end" class="ax">tolerance ${tol}°</text>
    <path d=${path(sa)} fill="none" stroke="var(--bad)" stroke-width="1.6" stroke-linejoin="round" opacity="0.9" />
    <path d=${path(sb)} fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" />
  </svg>`;
}

function BeforeAfterStrip({ a, b }) {
  const ca = cupOutcome(a.report), cb = cupOutcome(b.report);
  const stat = (label, x, y, bad) => html`<div class="ba-stat"><div class="lbl">${label}</div>
    <div class="ba-val"><span class=${bad ? 'bad-t' : ''}>${x}</span><span class="faint"> → </span><span>${y}</span></div></div>`;
  return html`<p class="kicker">Before and after, same layout</p>
    <div class="ba-legend"><span><i style="background:var(--bad)"></i>v${a.spec_version} · ${a.id}</span>
      <span><i style="background:var(--accent)"></i>v${b.spec_version} · ${b.id}</span></div>
    <${TiltChart} a=${a} b=${b} />
    <div class="ba-stats">
      ${stat('Cup max tilt while carried', fmtNum(ca?.max_transport_tilt_deg) + '°', fmtNum(cb?.max_transport_tilt_deg) + '°', (ca?.max_transport_tilt_deg || 0) > TOL)}
      ${stat('Beads kept', ca ? `${ca.beads_in_container}/${ca.beads_total}` : '–', cb ? `${cb.beads_in_container}/${cb.beads_total}` : '–', ca && ca.beads_in_container < ca.beads_total)}
      ${routeOf(a, ca?.object_id) || routeOf(b, cb?.object_id) ? stat('Cup route', html`<${Route} raw=${routeOf(a, ca?.object_id)} />`, html`<${Route} raw=${routeOf(b, cb?.object_id)} />`, false) : ''}
    </div>
    <p class="faint" style="font-size:12px;margin-top:8px">Measured by the checker from the simulator log of each run, not reported by the model.</p>`;
}

function RecheckCard({ item, snap }) {
  const rep = item.report;
  const v = (rep.violations || [])[0];
  const cup = cupOutcome(rep);
  return html`<div class="callout bad">
    <b>${item.rollout_id}</b>, recorded under v${snap.rollouts.find((r) => r.id === item.rollout_id)?.spec_version}, fails v${item.spec_version}:
    ${v ? html` ${v.constraint_id} exceeded, peak <span class="num">${fmtNum(v.peak)}°</span> from ${fmtT(v.t_start)} to ${fmtT(v.t_end)}.` : ' ' + statusLabel(rep)}
    ${cup ? html`<div style="margin-top:4px">${cup.beads_total - cup.beads_in_container} of ${cup.beads_total} beads left the cup.</div>` : ''}
    <div class="muted" style="margin-top:4px">Same physics log, new rule: the checker reads the recorded steps, not the model.</div>
  </div>`;
}

function BeforeAfter({ a, b }) {
  const ca = cupOutcome(a.report), cb = cupOutcome(b.report);
  const row = (lab, x, y, bad) => html`<tr><td>${lab}</td><td class=${'n ' + (bad ? 'bad-t' : '')}>${x}</td><td class="n">${y}</td></tr>`;
  return html`<table class="t"><thead><tr><th></th><th class="n">before (v${a.spec_version})</th><th class="n">after (v${b.spec_version})</th></tr></thead><tbody>
    ${row('Cup route', html`<${Route} raw=${routeOf(a, ca?.object_id) || carriedRoutes(a.report)[0]} />`, html`<${Route} raw=${routeOf(b, cb?.object_id) || carriedRoutes(b.report)[0]} />`)}
    ${row('Cup max carry tilt', fmtNum(ca?.max_transport_tilt_deg) + '°', fmtNum(cb?.max_transport_tilt_deg) + '°', (ca?.max_transport_tilt_deg || 0) > TOL)}
    ${row('Beads kept', ca ? `${ca.beads_in_container}/${ca.beads_total}` : '–', cb ? `${cb.beads_in_container}/${cb.beads_total}` : '–', ca && ca.beads_in_container < ca.beads_total)}
    ${row('Simulated time', fmtNum(a.report.execution_time_s) + ' s', fmtNum(b.report.execution_time_s) + ' s')}
  </tbody></table>`;
}

function ClarifyCard({ ctx }) {
  const { snap } = ctx;
  const c = snap.clarification;
  const rs = snap.rollouts;
  const job = ctx.busy.clarify || ctx.busy.answer;
  const digests = c?.candidates || [];
  const cands = c ? (c.executed?.length ? c.executed.map((e, i) => ({ ...(digests[i] || {}), ...e })) : digests) : [];
  const answers = c ? (c.answers || (c.options || []).map((t) => ({ text: t }))) : [];
  return html`
    <p class="kicker">Or let MIRROR ask</p>
    ${!c ? html`<p class="lede">MIRROR can find two behaviours that both satisfy the current rules and ask whether both count.</p>
      <button class="btn" disabled=${!!job || ctx.isStatic || !snap.specs.length} onClick=${() => ctx.action('clarify', () => api.post('clarify'))}>Ask a clarifying question</button>
      <${JobLine} job=${job} />`
    : html`
      <div class="question">${c.question || 'Would both of these still count as doing the task correctly?'}</div>
      <div class="cands">${cands.map((k, i) => {
        const r = rs.find((x) => x.id === k.rollout_id);
        const focus = (c.differences || []).find((d) => d.object_id)?.object_id;
        const tr = focus && r?.trace?.objects?.[focus] ? { points: r.trace.objects[focus] } : r?.trace;
        return html`<button class="cand" aria-pressed=${ctx.pick === k.rollout_id} onClick=${() => r && ctx.setPick(k.rollout_id)}
          aria-label=${'Watch candidate ' + (i + 1)}>
          <${MiniMap} scene=${r?.scene || snap.scene} trace=${tr} />
          ${focus ? html`<div class="faint" style="font-size:11px;padding:2px 10px 0">path of ${focus}</div>` : ''}
          <div class="meta"><b>${k.label && k.label.length <= 2 ? 'Behaviour ' + k.label : (k.label || 'Behaviour ' + String.fromCharCode(65 + i))}</b>
            <span class="muted">${(k.routes || []).length ? html`${(k.routes || []).slice(0, 3).map((x, j) => html`${j ? ' · ' : ''}<${Route} raw=${x} />`)}` : (k.summary || '')}</span>
            ${r ? (() => {
              const fo = (r.report.outcomes || []).find((o) => o.object_id === focus);
              const co = cupOutcome(r.report);
              return html`<span class="muted" style="display:block">simulated: ${fo && fo !== co ? `${focus} tilt ${fmtNum(fo.max_transport_tilt_deg)}° · ` : ''}cup ${fmtNum(co?.max_transport_tilt_deg)}° · beads ${co?.beads_in_container ?? '–'}/${co?.beads_total ?? '–'}</span>`;
            })() : ''}</div>
        </button>`;
      })}</div>
      ${(c.differences || []).length ? html`<ul class="list" style="font-size:12px;margin-bottom:10px">${c.differences.map((d) => html`<li>${d.statement
        || `${d.object_id} ${(d.metric || d.feature || '').replace(/_/g, ' ')}: ${(d.values || []).join(' vs ')}`}</li>`)}</ul>` : ''}
      <div class="row">${answers.map((a, i) => html`
        <button class=${'btn ' + (i === 0 ? 'primary' : '')} disabled=${!!job || ctx.isStatic}
          onClick=${() => ctx.action('answer', () => api.post('clarify/answer', { text: a.text }))}>${a.text}</button>`)}</div>
      <p class="faint" style="font-size:12px;margin-top:8px">Neither behaviour is called a failure until you say which one you meant.</p>
      <${JobLine} job=${job} />`}`;
}

// ------------------------------------------------------------- 6 Evidence

export function normalizeEval(ev) {
  if (!ev) return null;
  if (ev.trials && ev.studio) {
    const pairs = ev.trials.map((t) => {
      const ids = ev.studio.rollout_ids?.[String(t.seed)] || {};
      return { seed: t.seed, scene_id: t.scene_id, scene: ev.studio.scenes?.[String(t.seed)] || null,
        a: { report: t.a.report, rollout_id: ids.a, trial: t.a }, b: { report: t.b.report, rollout_id: ids.b, trial: t.b },
        a_under_b: { status: t.a.corrected_violations?.length ? 'constraint_violated' : t.a.report.status } };
    });
    return { pairs, raw: ev, real: true };
  }
  const rows = ev.pairs || ev.rows || ev.trials || [];
  const pick = (row, key) => {
    const v = row[key] || row[key === 'original' ? 'a' : 'b'];
    if (!v) return null;
    const report = v.report || v;
    return { report, rollout_id: row[key + '_rollout_id'] || v.rollout_id || report.rollout_id };
  };
  const pairs = rows.map((r) => ({ seed: r.seed, scene: r.scene || null, scene_id: r.scene_id || r.scene?.scene_id,
    a: pick(r, 'original'), b: pick(r, 'corrected'), a_under_b: r.original_under_corrected || null }));
  return { pairs, raw: ev };
}

const vfs = (rep) => rep && rep.status === 'success';

export function evalMetrics(ev) {
  const P = ev.pairs, n = P.length;
  if (ev.real && ev.raw.metrics) {
    const conds = Object.keys(ev.raw.metrics);
    const A = ev.raw.metrics[conds[0]], B = ev.raw.metrics[conds[1]];
    const vfs = ev.raw.paired?.violation_free_success;
    return { n, vfs, power: vfs?.power_note, rows: [
      { label: 'Task completed', a: A.completion, b: B.completion, of: n, good: 'high' },
      { label: 'Completed without breaking the corrected rule', a: A.violation_free_success, b: B.violation_free_success, of: n, good: 'high' },
      { label: 'Breaks the corrected rule', a: A.corrected_rule_violations, b: B.corrected_rule_violations, of: n, good: 'low' },
      { label: 'Beads lost', a: A.beads_lost_total, b: B.beads_lost_total, good: 'low' },
      { label: 'Max tilt under the corrected rule (mean)', a: A.max_tilt_deg_mean == null ? '–' : fmtNum(A.max_tilt_deg_mean) + '°',
        b: B.max_tilt_deg_mean == null ? '–' : fmtNum(B.max_tilt_deg_mean) + '°' },
      { label: 'No plan', a: A.planning_failures, b: B.planning_failures, good: 'low' },
      { label: 'Execution failed or timed out', a: A.execution_failures + A.timeouts, b: B.execution_failures + B.timeouts, good: 'low' },
    ] };
  }
  const beadsLost = (rep) => (rep?.outcomes || []).reduce((s, o) => s + Math.max(0, (o.beads_total || 0) - (o.beads_in_container || 0)), 0);
  const brokeA = (p) => (p.a_under_b ? p.a_under_b.status === 'constraint_violated' : p.a?.report?.status === 'constraint_violated');
  const brokeB = (p) => p.b?.report?.status === 'constraint_violated';
  const done = (rep) => !!rep?.completion;
  const fail = (rep, st) => rep?.status === st;
  return {
    n,
    rows: [
      { label: 'Task completed', a: P.filter((p) => done(p.a?.report)).length, b: P.filter((p) => done(p.b?.report)).length, of: n, good: 'high' },
      { label: 'Breaks the corrected rule', a: P.filter(brokeA).length, b: P.filter(brokeB).length, of: n, good: 'low',
        hint: 'original runs re-checked under the corrected specification' },
      { label: 'Beads lost', a: P.reduce((s, p) => s + beadsLost(p.a?.report), 0), b: P.reduce((s, p) => s + beadsLost(p.b?.report), 0), good: 'low' },
      { label: 'No plan', a: P.filter((p) => fail(p.a?.report, 'planning_failed')).length, b: P.filter((p) => fail(p.b?.report, 'planning_failed')).length, good: 'low' },
      { label: 'Execution failed or timed out', a: P.filter((p) => fail(p.a?.report, 'execution_failed') || fail(p.a?.report, 'timeout')).length,
        b: P.filter((p) => fail(p.b?.report, 'execution_failed') || fail(p.b?.report, 'timeout')).length, good: 'low' },
    ],
  };
}

function EvalSummary({ ev, va, vb }) {
  const m = evalMetrics(ev);
  return html`<table class="t" style="margin:14px 0 6px"><thead><tr><th>${m.n} paired layouts</th>
    <th class="n">original v${va}</th><th class="n">corrected v${vb}</th></tr></thead><tbody>
    ${m.rows.map((r) => html`<tr title=${r.hint || ''}><td>${r.label}</td>
      <td class=${'n ' + (r.good === 'low' && r.a > 0 ? 'bad-t' : '')}>${r.a}${r.of ? html`<span class="faint">/${r.of}</span>` : ''}</td>
      <td class=${'n ' + (r.good === 'low' && r.b > 0 ? 'bad-t' : '')}>${r.b}${r.of ? html`<span class="faint">/${r.of}</span>` : ''}</td></tr>`)}
  </tbody></table>
  ${m.vfs ? html`<p class="muted" style="font-size:12px">Paired, same seeds: ${m.vfs.only_b} layout(s) improved, ${m.vfs.only_a} got worse;
    exact McNemar p = ${m.vfs.mcnemar_p_exact == null ? '–' : Number(m.vfs.mcnemar_p_exact).toPrecision(2)}. ${m.power || ''}</p>` : ''}`;
}

function EvidencePanel({ ctx }) {
  const { snap } = ctx;
  const seedCap = snap.limits?.max_eval_seeds || 24;
  const [n, setN] = useState(Math.min(8, seedCap));
  const raw = snap.evaluation;
  const strataNames = raw?.strata ? Object.keys(raw.strata) : null;
  const [stratum, setStratum] = useState(null);
  const cur = strataNames ? (stratum && raw.strata[stratum] ? stratum : strataNames[0]) : null;
  const evRaw = cur ? raw.strata[cur] : raw;
  const ev = useMemo(() => normalizeEval(evRaw), [evRaw]);
  const specs = snap.specs;
  const a = specs[0], b = specs[specs.length - 1];
  const va = evRaw?.spec_a ?? a?.version, vb = evRaw?.spec_b ?? b?.version;
  const job = ctx.busy.evaluate;
  const seeds = Array.from({ length: n }, (_, i) => 9000 + i);
  const rs = snap.rollouts;
  const blocked = latest(rs, (r) => !isAux(r) && (isBlockedScene(r.scene_id) || r.tags.includes('blocked')));
  const src = raw?.source?.kind;
  const evInfo = evalKind(raw);
  const final = evInfo.kind === 'final';
  return html`
    <div class="sec">
      <${Head} n="6" kicker="Somewhere it has not seen" title=${final ? 'Pre-registered evaluation' : evInfo.kind === 'rehearsal' ? 'Evaluation rehearsal' : 'Paired test on fresh layouts'}
        lede="Both specifications run from the identical initial state in every layout, with the same planner and controller. Every attempt is shown." />
      ${raw ? html`<div class="row" style="margin-bottom:6px"><${Chip} status=${final ? 'accent' : ''}>${evInfo.label}<//></div>` : ''}
      ${!ctx.isStatic && !raw?.strata && evInfo.kind !== 'final' ? html`<div class="row">
        <label class="muted" for="nseeds" style="font-size:12px">Showcase layouts</label>
        <input id="nseeds" class="num-in" type="number" min="1" max=${seedCap} value=${n} onInput=${(e) => setN(Math.max(1, Math.min(seedCap, parseInt(e.target.value || '1', 10))))} />
        <span class="muted num" style="font-size:12px">dev seeds ${seeds[0]}–${seeds[seeds.length - 1]}</span>
        <span class="spacer"></span>
        <button class=${ctx.story ? 'btn' : 'btn primary'} disabled=${!!job || specs.length < 2}
          onClick=${() => ctx.action('evaluate', () => api.post('evaluate', { seeds, split: 'dev' }))}>Run v${a?.version} vs v${b?.version}</button>
      </div>
      <p class="faint" style="font-size:12px;margin-top:6px">The studio only runs development seeds. Pre-registered evaluation results are produced separately and loaded here unchanged.</p>
      <${JobLine} job=${job} />` : ''}
      ${strataNames ? html`<div class="tabs" role="tablist" aria-label="Strata">${strataNames.map((nm) => html`
        <button role="tab" aria-selected=${nm === cur} onClick=${() => setStratum(nm)}>${cap(nm)}
          <span class="faint num"> ${(raw.strata[nm].trials || []).length}</span></button>`)}</div>
        ${raw.roles?.[cur] ? html`<p class="faint" style="font-size:12px">${roleText(raw.roles[cur], raw.strata[cur])}</p>` : ''}` : ''}
      ${ev ? html`
        <${EvalSummary} ev=${ev} va=${va} vb=${vb} />
        <p class="faint" style="font-size:12px">Original runs are judged twice: under their own rules and re-checked under the corrected rule (red outline).</p>
        <div class="ev-colh"><span>seed</span><span>original v${va}</span><span>corrected v${vb}</span></div>
        <div class="ev-grid">${ev.pairs.map((p) => html`<div class="ev-row">
          <div class="seed">${p.seed}</div>
          ${['a', 'b'].map((k) => {
            const cell = p[k];
            const r = cell && rs.find((x) => x.id === cell.rollout_id);
            const rep = cell?.report;
            const broke = k === 'a' && p.a_under_b?.status === 'constraint_violated';
            const cup = cupOutcome(rep);
            return html`<button class=${'ev-cell' + (broke ? ' broke' : '')} aria-pressed=${ctx.pick && ctx.pick === cell?.rollout_id}
              disabled=${!r?.has_frames} onClick=${() => r && ctx.setPick(r.id)} aria-label=${`seed ${p.seed} ${k === 'a' ? 'original' : 'corrected'}: ${statusLabel(rep)}`}>
              ${p.scene || r?.scene ? html`<${MiniMap} scene=${p.scene || r?.scene} trace=${r?.trace} />`
                : html`<div class="mini-none"><span>report only</span></div>`}
              <div class="m"><${Chip} status=${rep?.status}>${statusLabel(rep)}<//>
                ${cup ? html`<span class="num muted">${fmtNum(cup.max_transport_tilt_deg, 0)}° · ${cup.beads_in_container}/${cup.beads_total}</span>` : ''}
                ${broke ? html`<span class="bad-t">breaks v${vb}</span>` : ''}
                ${cell?.trial?.feasibility === 'proven_infeasible' ? html`<span class="ok-t">proven impossible</span>` : ''}
                ${carriedRoutes(rep).length ? html`<span class="ev-route" title=${carriedRoutes(rep).join('\n')}>${[...new Set(carriedRoutes(rep).map((x) => humanRoute(x, true)))].join(' · ')}</span>` : ''}
</div>
            </button>`;
          })}</div>`)}</div>
        ${ev.pairs.some((p) => rs.find((x) => x.id === p.a?.rollout_id)?.sketch) ? html`<p class="faint" style="font-size:12px">Maps are drawn from each recorded rollout's own geometry.</p>` : ''}
        ${raw?.rollout_selection?.rule && ev.pairs.some((p) => !rs.find((x) => x.id === p.a?.rollout_id)?.has_frames) ? html`<p class="faint" style="font-size:12px">"Report only": ${raw.rollout_selection.rule}</p>` : ''}`
      : html`<p class="muted">No evaluation yet.</p>`}
      ${ev?.real && ev.raw.scope?.verdict ? html`<h3>Scope</h3><p>${cap(ev.raw.scope.verdict)}.</p>
        <p class="faint" style="font-size:12px">Objects the correction does not mention may still tilt when that is cheaper.</p>` : ''}
      ${ev?.real ? html`<details style="margin-top:10px"><summary class="muted" style="cursor:pointer;font-size:12px">Manifest and simplifications</summary>
        <dl class="kv" style="margin-top:8px"><dt>Split</dt><dd>${ev.raw.split}</dd><dt>Manifest</dt><dd>${(raw.manifest || ev.raw.manifest)?.created_at}</dd>
          <dt>Code</dt><dd class="num">${((raw.manifest || ev.raw.manifest)?.code_sha256 || '').slice(0, 12)} · ${(raw.manifest || ev.raw.manifest)?.code_files} files</dd>
          <dt>Checker</dt><dd>${ev.raw.config?.checker}</dd><dt>Wall time</dt><dd class="num">${fmtNum(ev.raw.wall_time_s, 0)} s</dd></dl>
        <ul style="font-size:12px;padding-left:18px">${(ev.raw.simplifications || []).map((x) => html`<li>${x}</li>`)}</ul></details>` : ''}
      ${final && raw.ledger ? html`<${LedgerNote} raw=${raw} />` : ''}
    </div>
    ${final && raw.blocked_reference?.length ? html`<div class="sec"><${BlockedProofs} raw=${raw} /></div>` : ''}
    <div class="sec" id="blocked-case">
      <p class="kicker">The blocked case</p>
      <p class="lede">In this layout the slot is the only opening. Carrying the cup upright cannot fit through it. The robot should stop, not break the rule.</p>
      ${!ctx.isStatic ? html`<button class="btn" disabled=${!!ctx.busy.run}
        onClick=${async () => { await ctx.action('scene', () => api.post('scene', { preset: 'blocked' })); await ctx.action('run', () => api.post('run', { condition: 'blocked · v' + b?.version, tags: ['blocked'] })); }}>Try the blocked layout with v${b?.version}</button>
        <${JobLine} job=${ctx.busy.run} />` : ''}
      ${blocked ? html`<div style="margin-top:10px"><${ReportView} r=${blocked} compact=${true} />
        <button class="btn small" style="margin-top:8px" onClick=${() => ctx.setPick(blocked.id)}>Show in viewer</button></div>` : ''}
    </div>`;
}

// What kind of evidence this is, stated plainly.
export function evalKind(raw) {
  if (!raw) return { kind: 'none', label: '' };
  if (raw.kind === 'fixture') return { kind: 'fixture', label: 'fixture data, not evidence' };
  const m = raw.manifest || {};
  const split = raw.split || m.split || Object.values(raw.strata || {})[0]?.split;
  if (raw.source?.kind === 'showcase') return { kind: 'showcase', label: 'showcase run on development seeds' };
  if (m.kind === 'final' && split !== 'dev') return { kind: 'final', label: 'pre-registered, frozen before layouts were drawn' };
  if (m.kind === 'rehearsal' || /rehears/i.test(raw.title || '') || split === 'dev')
    return { kind: 'rehearsal', label: 'rehearsal on development seeds, not the pre-registered result' };
  return { kind: 'loaded', label: `saved ${split || ''} report` };
}

function LedgerNote({ raw }) {
  const L = raw.ledger, m = raw.manifest || {};
  const fe = L.frozen_seed_entries || [];
  const eb = L.entries_before_freeze;
  const before = Array.isArray(eb) ? eb.length : typeof eb === 'number' ? eb : fe.filter((e) => e.unix < m.created_unix).length;
  const clean = !before;
  return html`<div class=${'callout ' + (clean ? 'ok' : 'bad')} style="margin-top:12px">
    <b>${clean ? 'Ledger clean.' : 'Ledger shows layouts drawn before the freeze.'}</b>
    ${` Every evaluation layout ever generated is logged: ${L.entries} entries, ${fe.length} for frozen seeds, ${clean ? 'all generated after the manifest was written' : `${before} before it`} (${(m.created_at || '').replace('T', ' ').slice(0, 19)} UTC).`}
    <div class="muted num" style="margin-top:4px;font-size:11px">ledger sha256 ${(L.sha256 || '').slice(0, 16)} · manifest code ${(m.code_sha256 || '').slice(0, 12)}</div>
  </div>`;
}

function BlockedProofs({ raw }) {
  const strat = raw.strata?.blocked;
  const proven = strat ? strat.trials.filter((t) => t.b.feasibility === 'proven_infeasible').length : null;
  return html`<p class="kicker">Blocked layouts: stop, with proof</p>
    ${strat ? html`<p><span class="num">${proven}/${strat.trials.length}</span> blocked layouts: the corrected specification found no plan,
      and an independent argument proves no motion can keep the cup upright there. The original specification still plans (it may tilt).</p>` : ''}
    ${raw.blocked_reference.map((b) => html`<div class=${'callout' + (b.failure_class === 'proven_infeasible' ? ' ok' : '')} style="margin-top:6px">
      <b>${b.scene_id} · ${b.condition}:</b> ${statusLabel({ status: b.status, plan_status: b.status === 'planning_failed' ? 'no_plan' : '' })}
      ${b.independent?.verdict === 'proven_infeasible' ? html`<div style="margin-top:4px">${b.independent.detail?.argument || ''}</div>
        ${(b.independent.detail?.assumptions || []).length ? html`<div class="muted" style="margin-top:4px">Assumes: ${b.independent.detail.assumptions.join('; ')}.</div>` : ''}`
        : html`<div class="muted" style="margin-top:4px">${b.status === 'success' ? 'Plans and completes without the transport rule.' : (b.plan_reason || '').slice(0, 160)}</div>`}
    </div>`)}`;
}

function roleText(role, rep) {
  if (role === 'primary') return 'Primary result: the standard layout sampler, unbiased mix of layouts.';
  if (role === 'stratum') return 'Disclosed stratum: layouts with a wall slot near the straight cup-to-tray line, chosen by geometry when drawn, never by outcome.';
  if (role === 'blocked') return 'Blocked layouts: the slot is the only opening, used for the infeasibility check.';
  return rep?.title || role;
}

// ------------------------------------------------------------- story mode

const STORY = {
  watch: { title: 'Watch me', text: 'A recorded teleoperation in simulation packs the cup, the block and the marker, carrying everything around the wall.' },
  interpret: { title: 'What does it think matters?', text: 'MIRROR proposes the task: goals, rules and open questions. Only rules marked required are enforced.' },
  run: { title: 'Now your turn', text: 'The planner turns the enforced rules into a plan, MuJoCo executes it, and a separate checker reads the physics log.' },
  world: { title: 'Change the world', text: 'The tray moves and the wall gets a slot. Replaying the demonstration is impossible; the robot replans and takes the short way, cup on its side.' },
  correct: { title: 'You misunderstood this', text: 'One sentence becomes a scoped rule. The old run is re-checked under it and the new plan takes a different route.' },
  evidence: { title: 'Somewhere it has not seen', text: 'Both versions start from identical states on layouts the correction never saw. Every attempt is shown, failures included.' },
};

function storyAction(ctx) {
  const { snap, step, isStatic } = ctx;
  const order = STEPS.map((x) => x.key);
  const next = order[order.indexOf(step) + 1];
  const go = (k) => () => ctx.setStep(k);
  const nextLabel = next ? `Next: ${STEPS.find((x) => x.key === next).short}` : null;
  if (isStatic) return next ? { label: nextLabel, run: go(next) } : { label: 'Back to the start', run: go('watch') };
  const rs = snap.rollouts.filter((r) => !isAux(r));
  const demoScene = snap.demo?.scene_id;
  const specFresh = snap.specs.length && snap.specs[0].demo_id === snap.demo?.demo_id;
  const cv = correctionVersion(snap);
  const post = (k, path, body) => ctx.action(k, () => api.post(path, body));
  switch (step) {
    case 'watch':
      return specFresh ? { label: nextLabel, run: go('interpret') }
        : { label: 'Interpret the demonstration', busy: 'interpret', run: async () => { await post('interpret', 'interpret'); ctx.setStep('interpret'); } };
    case 'interpret':
      return rs.some((r) => r.scene_id === demoScene) ? { label: nextLabel, run: go('run') }
        : { label: 'Run it in simulation', busy: 'run', run: async () => {
          ctx.setStep('run');
          if (snap.scene?.scene_id !== demoScene) await post('scene', 'scene', { preset: 'demo' });
          await post('run', 'run', { condition: 'demonstration layout', tags: ['run'] });
        } };
    case 'run':
      return rs.some((r) => r.tags.includes('world')) ? { label: nextLabel, run: go('world') }
        : { label: 'Change the world and replan', busy: 'run', run: async () => {
          ctx.setStep('world');
          await post('scene', 'scene', { preset: 'shortcut' });
          await post('run', 'run', { condition: 'changed world', tags: ['world'] });
        } };
    case 'world':
      return { label: 'That is not what I meant', run: go('correct') };
    case 'correct':
      if (!cv) return { label: 'Apply the example correction', busy: 'correct', run: async () => {
        const res = await post('correct', 'correct', { text: snap.reference_correction });
        if (res?.changed) await post('run', 'run', { condition: 'corrected', tags: ['corrected'] });
      } };
      if (!rs.some((r) => r.spec_version >= cv && r.scene_id === snap.scene?.scene_id))
        return { label: 'Replan with the correction', busy: 'run', run: () => post('run', 'run', { condition: 'corrected', tags: ['corrected'] }) };
      return { label: nextLabel, run: go('evidence') };
    case 'evidence':
      if (!snap.evaluation) {
        const k = Math.min(8, snap.limits?.max_eval_seeds || 8);
        return { label: `Run ${k} fresh showcase layouts`, busy: 'evaluate',
          run: () => post('evaluate', 'evaluate', { seeds: Array.from({ length: k }, (_, i) => 9000 + i), split: 'dev' }) };
      }
      if (!rs.some((r) => isBlockedScene(r.scene_id))) return { label: 'Try the blocked layout', busy: 'run', run: async () => {
        await post('scene', 'scene', { preset: 'blocked' });
        await post('run', 'run', { condition: 'blocked layout', tags: ['blocked'] });
        document.getElementById('blocked-case')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } };
      return { label: 'Back to the start', run: go('watch') };
    default:
      return null;
  }
}

// One line of results per stratum, from the loaded report's own metrics.
function StoryResult({ raw }) {
  if (!raw) return null;
  const strata = raw.strata ? Object.entries(raw.strata) : [['', raw]];
  const parts = [];
  for (const [name, rep] of strata) {
    const role = raw.roles?.[name];
    if (role === 'blocked') {
      const n = (rep.trials || []).length;
      const k = (rep.trials || []).filter((t) => t.b?.feasibility === 'proven_infeasible').length;
      if (n) parts.push(html`<span>blocked layouts: <b class="num">${k}/${n}</b> stopped with proof</span>`);
      continue;
    }
    const m = rep.metrics || {};
    const a = m.original?.violation_free_success, b = m.corrected?.violation_free_success;
    const n = (rep.trials || []).length;
    if (a == null || b == null || !n) continue;
    const label = role === 'stratum' ? 'shortcut layouts' : role === 'primary' ? 'fresh layouts' : `${name || 'paired'} layouts`;
    parts.push(html`<span>${label}: <b class="num">${a}/${n} → ${b}/${n}</b></span>`);
  }
  if (!parts.length) return null;
  return html`<div class="story-res">Done without breaking the rule, original → corrected: ${parts.map((x, i) => html`${i ? ' · ' : ''}${x}`)}</div>`;
}

export function StoryBar({ ctx }) {
  const order = STEPS.map((x) => x.key);
  const ix = order.indexOf(ctx.step);
  const st = ctx.step === 'watch' && ctx.snap.video_demo && ctx.snap.demo?.source === 'video_grounded'
    ? { title: 'Watch me', text: (videoMediaKind(ctx.snap) === 'rendered_simulation' ? 'A demonstration video rendered from the simulator' : 'A demonstration video') + ': SAM 3.1 tracks the objects, the trace is reconstructed on the table, and Muse Spark describes what it sees.' }
    : STORY[ctx.step];
  const act = storyAction(ctx);
  const busy = act?.busy && ctx.busy[act.busy];
  const enabled = stepState(ctx.snap);
  return html`<div class="story" role="region" aria-label="Guided story">
    <div class="story-ix num" aria-label=${`Step ${ix + 1} of ${order.length}`}>${ix + 1}<span class="faint">/${order.length}</span></div>
    <div class="story-text"><b>${st.title}${/[.?!]$/.test(st.title) ? '' : '.'}</b> <span>${st.text}</span>
      ${ctx.step === 'evidence' ? html`<${StoryResult} raw=${ctx.snap.evaluation} />` : ''}</div>
    <div class="story-act">
      <button class="btn ghost small" disabled=${ix === 0} onClick=${() => ctx.setStep(order[ix - 1])}>Back</button>
      ${act ? html`<button class="btn primary" disabled=${!!busy || (act.label.startsWith('Next') && !enabled[order[ix + 1]]?.enabled)}
        onClick=${act.run}>${busy ? (busy.status === 'queued' && busy.queue_position ? `Waiting: ${ORD(busy.queue_position)} in line` : (busy.message?.split('\n')[0] || 'Working') + '…') : act.label}</button>` : ''}
      <button class="iconbtn" aria-label="Hide the guided story" title="Explore freely" onClick=${() => ctx.toggleStory(false)}>✕</button>
    </div>
  </div>`;
}

// ------------------------------------------------------------- about

export function AboutPanel({ ctx, onClose }) {
  const { snap } = ctx;
  const spec = snap.specs[0];
  const interp = spec?.interpreter || 'not yet interpreted';
  const isOffline = /offline|rules/.test(interp);
  const ev = snap.evaluation;

  useEffect(() => {
    const k = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, []);
  return html`<div class="about" role="dialog" aria-label="What you are looking at">
    <div class="about-h"><b>What you are looking at</b><button class="iconbtn" onClick=${onClose} aria-label="Close">✕</button></div>
    <ul>
      <li><b>Simulation only.</b> Everything runs in MuJoCo. Nothing here was executed on a physical robot or shows simulation-to-reality transfer.</li>
      <li><b>The robot is a free-flying gripper.</b> Six position-controlled axes and two fingers; no arm is modelled. Objects move only through actuators and contact physics.</li>
      <li><b>Privileged state.</b> Planning and checking read simulator state (poses, contacts, bead positions). It is not visual perception.</li>
      <li><b>The demonstration</b> is ${demoSource(snap).long}.</li>
      <li><b>The specification</b> was produced by <span class="num">${interp}</span>${isOffline ? ', the rule-based interpreter that needs no model' : ''}${spec?.history?.find((h) => h.model)?.model ? `; corrections compiled by ${spec.history.find((h) => h.model).model}` : ''}. ${snap.llm_mode === 'replay' ? ' Model responses are replayed from recordings made with that model; nothing calls a model while you browse.' : ''}${(snap.fallbacks || []).length ? ` These steps used the offline interpreter instead: ${snap.fallbacks.map((f) => f.step).join(', ')}.` : ''}${snap.llm?.mode === 'live' ? ` Live model requests (${snap.llm.model}) are limited to ${snap.llm.calls_per_session} per visitor and ${snap.llm.calls_per_hour} per hour for the server${snap.llm.calls_remaining_session != null ? `; ${snap.llm.calls_remaining_session} left in this session` : ''}. Past that, or if the model fails, the offline interpreter is used and the step says so.` : ''} Model output is validated against a schema before planning and never judges success.</li>
      <li><b>Success is measured</b> by an independent checker from the physics log at every step: placement, tilt per phase, bead retention, contacts.</li>
      <li><b>Evidence:</b> ${ev ? ({ final: 'the pre-registered evaluation, loaded unchanged from the evaluation output.',
        rehearsal: 'a rehearsal of the evaluation pipeline on development seeds, not the pre-registered result.',
        showcase: 'a showcase run on development seeds, not the pre-registered evaluation.',
        fixture: 'fixture data, not evidence.' }[evalKind(ev).kind] || 'a saved evaluation report.') : 'no evaluation in this session yet.'}</li>
      ${snap.mode === 'fixture' ? html`<li class="bad-t"><b>Fixture data:</b> key-framed placeholder motion, not physics.</li>` : ''}
    </ul>
    ${api.repoUrl ? html`<a class="btn small" href=${api.repoUrl} target="_blank" rel="noopener">Source code and claims</a>` : ''}
  </div>`;
}

export const PANELS = { watch: WatchPanel, interpret: InterpretPanel, run: RunPanel, world: WorldPanel, correct: CorrectPanel, evidence: EvidencePanel };
