// Step panels: the story from demonstration to evidence.
import { h } from 'preact';
import { useState, useMemo, useEffect } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { MiniMap } from './minimap.js';
import { fmtT, fmtNum, statusLabel, constraintText, selectorText, provText, sceneLabel, latest, cupOutcome, cap, isAux, isBlockedScene } from './util.js';

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

function JobLine({ job }) {
  if (!job) return null;
  return html`<div class="job" role="status" aria-live="polite">${job.message ? job.message.split('\n')[0] : job.kind}…
    <div class="progress"><div style=${`width:${Math.round(100 * (job.progress || 0.04))}%`}></div></div></div>`;
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

export function routeOf(r, objId) {
  const t = (r?.plan?.transport || []).find((x) => x.object_id === objId);
  return t ? t.route : null;
}

function routesText(r) {
  const t = r?.plan?.transport;
  if (t?.length) return t.map((x) => `${x.object_id}: ${x.route}`).join(' · ');
  return (r?.report?.route_labels || []).join(', ');
}

function NoPlan({ rep, plan }) {
  const inf = plan?.infeasibility;
  const proven = inf && (inf.proven === true || inf.certificate || inf.kind === 'certificate');
  return html`<div class=${'callout ' + (proven ? '' : '')} style="margin-bottom:8px">
    <b>${proven ? 'No valid plan exists (proven).' : 'The planner found no valid plan.'}</b>
    <div>${rep.plan_reason || plan?.reason}</div>
    ${inf ? html`<div class="muted" style="margin-top:4px">${proven ? 'Certificate' : 'Note'}: ${inf.detail || inf.reason || JSON.stringify(inf)}</div>`
      : html`<div class="muted" style="margin-top:4px">This is not a proof that the layout is impossible.</div>`}
    <div class="muted" style="margin-top:4px">The robot did not move. Nothing was executed rather than breaking the rule.</div>
  </div>`;
}

function RoutesView({ plan }) {
  if (!plan?.routes?.length) return null;
  return html`<div><h3>Routes considered</h3><ul class="list routes">
    ${plan.routes.map((r) => html`<li>
      <${Chip} status=${r.feasible ? 'success' : 'planning_failed'}>${r.feasible ? 'feasible' : 'rejected'}<//>
      <span class="lab">${r.object_id ? r.object_id + ' · ' : ''}${r.label}</span>
      <span class="spacer"></span>
      <span class="muted" style="font-size:12px">${r.feasible ? (r.cost != null ? `cost ${fmtNum(r.cost, 2)}` : '') : r.rejected_reason || ''}</span>
    </li>`)}</ul></div>`;
}

function RunButton({ ctx, label, tags = [], condition, spec_version = null, primary = true, busyKey = 'run' }) {
  const job = ctx.busy[busyKey];
  return html`<button class=${'btn ' + (primary ? 'primary' : '')} disabled=${!!job || ctx.isStatic}
    title=${ctx.isStatic ? 'Needs the live studio' : ''}
    onClick=${() => ctx.action(busyKey, () => api.post('run', { spec_version, condition, tags })).then(() => ctx.setPick(null))}>${label}</button>`;
}

// ---------------------------------------------------------------- 1 Watch

function WatchPanel({ ctx }) {
  const { snap } = ctx;
  const d = snap.demo;
  const sources = { teleop_scripted: 'Scripted teleoperation in simulation', teleop_keyboard: 'Keyboard teleoperation in simulation',
    replay: 'Replayed trace', video_grounded: 'Human video grounded to simulator objects' };
  return html`
    <div class="sec">
      <${Head} n="1" kicker="Watch me" title="The demonstration"
        lede="An operator packs the cup, the block and the marker into the tray, carrying everything around the wall." />
      ${d ? html`
        <dl class="kv"><dt>Input</dt><dd>${sources[d.source] || d.source}</dd>
          <dt>Duration</dt><dd class="num">${fmtT(d.duration)}</dd>
          <dt>Layout</dt><dd>${sceneLabel(d.scene_id)}</dd></dl>
        <p class="faint" style="font-size:12px;margin-top:8px">The demonstration is a recorded simulator trace (gripper and object poses), not video of a person.${d.source_note ? ' ' + d.source_note : ''}</p>
        <h3>Events</h3>
        <ol class="timeline">${(d.events || []).filter((e) => e.type === 'grasp' || e.type === 'release').map((e) => html`
          <li><button onClick=${() => { ctx.setPick('demo'); setTimeout(() => ctx.viewer.current?.setTime(e.t), 60); }}
            aria-label=${`${e.type} ${e.object_id} at ${fmtT(e.t)}, jump`}><span class="t">${fmtT(e.t)}</span>
            <span>${cap(e.type)} <b>${e.object_id}</b></span></button></li>`)}</ol>
        <div class="row" style="margin-top:16px">
          ${snap.specs.length ? html`<button class="btn primary" onClick=${() => ctx.setStep('interpret')}>See the interpretation</button>`
            : html`<button class="btn primary" disabled=${!!ctx.busy.interpret || ctx.isStatic}
              onClick=${async () => { await ctx.action('interpret', () => api.post('interpret')); ctx.setStep('interpret'); }}>Interpret this demonstration</button>`}
        </div>
        <${JobLine} job=${ctx.busy.interpret} />`
      : html`<p>No demonstration loaded.</p>
        <div class="row"><button class="btn primary" disabled=${ctx.isStatic || !!ctx.busy.demo}
          onClick=${() => ctx.action('demo', () => api.post('demo/load'))}>Load the reference demonstration</button></div>`}
      <${StaticNote} ctx=${ctx} />
    </div>`;
}

// ------------------------------------------------------------ 2 Interpret

function ItemRow({ id, title, sub, status, prov, staged, onSet, readonly }) {
  const eff = staged || status;
  return html`<li>
    <div class="item-h">
      <div class="grow">
        <div class="item-title">${title}</div>
        ${sub ? html`<div class="item-sub">${sub}</div>` : ''}
        <div class="prov">${prov}</div>
      </div>
      ${readonly ? html`<${Chip} status=${status}>${status === 'confirmed' ? 'enforced' : status}<//>` : html`<div class="seg" role="group" aria-label=${'Decision for ' + id}>
        <button class="c" aria-pressed=${eff === 'confirmed'} onClick=${() => onSet(id, 'confirmed')}>Required</button>
        <button class="r" aria-pressed=${eff === 'rejected'} onClick=${() => onSet(id, 'rejected')}>Not required</button>
      </div>`}
    </div>
    ${readonly ? '' : staged && staged !== status ? html`<div class="staged">${status} → ${staged} (not yet committed)</div>`
      : html`<div style="margin-top:4px"><${Chip} status=${status}>${status === 'confirmed' ? 'enforced' : status}<//></div>`}
  </li>`;
}

function InterpretPanel({ ctx }) {
  const { snap } = ctx;
  const spec = currentSpec(snap);
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
  const ro = ctx.isStatic;
  return html`
    <div class="sec">
      <${Head} n="2" kicker="Interpret" title="What MIRROR thinks matters" lede=${spec.summary} />
      <div class="row"><${Chip} status="accent">spec v${spec.version}<//>
        <span class="muted" style="font-size:12px">interpreter: ${spec.interpreter || 'unknown'}</span></div>
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
        <button class="btn primary" onClick=${() => ctx.setStep('run')}>Run it</button>
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
        <button class="btn primary" onClick=${() => ctx.setStep('world')}>Change the world</button></div>` : ''}`;
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
      <h3>Layouts</h3>
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
        <button class="btn small" disabled=${ro} onClick=${() => ctx.action('scene', () => api.post('scene', { edit: { remove_obstacle: o.id } }))}>Remove ${o.id}</button>`)}</div>` : ''}
      <div style="margin-top:14px"><${MiniMap} scene=${sc} label="current layout" /></div>
      <div class="row" style="margin-top:12px">
        <span class="muted" style="font-size:12px">${sceneLabel(sc?.scene_id)}</span><span class="spacer"></span>
        <${RunButton} ctx=${ctx} label=${`Replan and run v${spec?.version}`} condition=${'v' + spec?.version + ' · ' + (sc?.scene_id || '')} tags=${['world']} />
      </div>
      <${JobLine} job=${ctx.busy.run || ctx.busy.scene} />
    </div>
    ${r ? html`<div class="sec"><${ReportView} r=${r} /></div>` : ''}
    ${r && /slot|pitch|tilt/i.test(routeOf(r, cupOutcome(r.report)?.object_id) || (r.plan?.transport ? '' : (r.report.route_labels || []).join(' '))) ? html`<div class="sec">
      <p class="lede">It took the short way: through the slot, with the cup on its side.</p>
      <button class="btn primary" onClick=${() => ctx.setStep('correct')}>That is not what I meant</button></div>` : ''}`;
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
    const res = await ctx.action('correct', () => api.post('correct', { text: t }));
    if (res && (res.question || res.rejected)) setReply(res);
    else if (res && !res.changed) setReply({ rejected: 'That did not change the specification.' });
  };
  return html`
    <div class="sec">
      <${Head} n="5" kicker="You misunderstood this" title="Correct one rule"
        lede="Say what was wrong in plain words. MIRROR compiles it into a scoped change to the specification." />
      ${!ctx.isStatic ? html`
        <label class="sr-only" for="corr">Correction</label>
        <textarea id="corr" class="corr" placeholder=${snap.reference_correction} value=${text}
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) submit(text.trim()); }}></textarea>
        <div class="row" style="margin-top:8px">
          <button class="btn ghost small" onClick=${() => setText(snap.reference_correction)}>Use the example sentence</button>
          <span class="spacer"></span>
          <button class="btn primary" disabled=${!text.trim() || !!job} onClick=${() => submit(text.trim())}>Apply correction</button>
        </div>
        <${JobLine} job=${job} />
        ${reply?.question ? html`<div class="callout" style="margin-top:10px" role="status"><b>MIRROR needs to know:</b> ${reply.question}
          ${reply.options?.length ? html`<div class="row" style="margin-top:8px">${reply.options.map((o) => html`<button class="btn small"
            onClick=${() => { setText(o); }}>${o}</button>`)}</div>` : ''}
          <div class="muted" style="margin-top:4px">It asks instead of guessing. Nothing changed yet.</div></div>` : ''}
        ${reply?.rejected ? html`<div class="callout" style="margin-top:10px" role="status"><b>Not applied.</b> ${reply.rejected}</div>` : ''}` : rec ? html`<blockquote class="callout">“${rec.text}”</blockquote>` : ''}
    </div>
    ${cv && before && after ? html`<div class="sec">
      <p class="kicker">What changed</p>
      <p>“${rec?.text}”</p>
      <${SpecDiff} a=${before} b=${after} record=${rec} />
    </div>` : ''}
    ${shownRecheck ? html`<div class="sec">
      <p class="kicker">The old behaviour, re-checked</p>
      <${RecheckCard} item=${shownRecheck} snap=${snap} />
    </div>` : ''}
    ${cv ? html`<div class="sec">
      ${corrected ? html`<p class="kicker">New behaviour</p>
        ${ghost ? html`<${BeforeAfter} a=${ghost} b=${corrected} />` : ''}
        <div style="margin-top:12px"><${ReportView} r=${corrected} compact=${true} /></div>
        <div class="row" style="margin-top:12px"><button class="btn primary" onClick=${() => ctx.setStep('evidence')}>Test somewhere new</button></div>`
      : html`<p class="lede">Same planner, same controller, new rule. Run it in this layout.</p>
        <${RunButton} ctx=${ctx} label=${`Replan and run v${spec.version}`} condition=${'corrected v' + spec.version} tags=${['corrected']} />
        <${JobLine} job=${ctx.busy.run} />`}
    </div>` : ''}
    <div class="sec"><${ClarifyCard} ctx=${ctx} /></div>`;
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
    ${row('Cup route', routeOf(a, ca?.object_id) || (a.report.route_labels || [])[0] || '–', routeOf(b, cb?.object_id) || (b.report.route_labels || [])[0] || '–')}
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
        return html`<button class="cand" aria-pressed=${ctx.pick === k.rollout_id} onClick=${() => r && ctx.setPick(k.rollout_id)}
          aria-label=${'Watch candidate ' + (i + 1)}>
          <${MiniMap} scene=${r?.scene || snap.scene} trace=${r?.trace} />
          <div class="meta"><b>${k.label && k.label.length <= 2 ? 'Behaviour ' + k.label : (k.label || 'Behaviour ' + String.fromCharCode(65 + i))}</b>
            <span class="muted">${(k.routes || []).slice(0, 3).join(', ') || k.summary || ''}</span>
            ${r ? html`<span class="muted" style="display:block">simulated: cup tilt ${fmtNum(cupOutcome(r.report)?.max_transport_tilt_deg)}° · beads ${cupOutcome(r.report)?.beads_in_container ?? '–'}/${cupOutcome(r.report)?.beads_total ?? '–'}</span>` : ''}</div>
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
  const [n, setN] = useState(snap.mode === 'live' ? 8 : 12);
  const ev = useMemo(() => normalizeEval(snap.evaluation), [snap.evaluation]);
  const specs = snap.specs;
  const a = specs[0], b = specs[specs.length - 1];
  const job = ctx.busy.evaluate;
  const seeds = Array.from({ length: n }, (_, i) => 10000 + i);
  const rs = snap.rollouts;
  const blocked = latest(rs, (r) => isBlockedScene(r.scene_id) || r.tags.includes('blocked'));
  const count = (f) => ev ? ev.pairs.filter(f).length : 0;
  return html`
    <div class="sec">
      <${Head} n="6" kicker="Somewhere it has not seen" title="Paired test on fresh layouts"
        lede="Both specifications run from the identical initial state in every layout, with the same planner and controller. Every attempt is shown." />
      ${!ctx.isStatic ? html`<div class="row">
        <label class="muted" for="nseeds" style="font-size:12px">Layouts</label>
        <input id="nseeds" type="number" min="1" max="48" value=${n} onInput=${(e) => setN(Math.max(1, Math.min(48, parseInt(e.target.value || '1', 10))))}
          style="width:64px;border:1px solid var(--line-strong);border-radius:999px;padding:4px 10px;background:var(--surface)" />
        <span class="muted num" style="font-size:12px">seeds ${seeds[0]}–${seeds[seeds.length - 1]}</span>
        <span class="spacer"></span>
        <button class="btn primary" disabled=${!!job || specs.length < 2}
          onClick=${() => ctx.action('evaluate', () => api.post('evaluate', { seeds }))}>Run v${a?.version} vs v${b?.version}</button>
      </div><${JobLine} job=${job} />` : ''}
      ${ev ? html`
        <${EvalSummary} ev=${ev} va=${snap.evaluation.spec_a ?? a?.version} vb=${snap.evaluation.spec_b ?? b?.version} />
        <p class="faint" style="font-size:12px">Original runs are judged twice: under their own rules (left) and re-checked under the corrected rule (red outline).</p>
        <div class="ev-colh"><span>seed</span><span>original v${snap.evaluation.spec_a ?? a?.version}</span><span>corrected v${snap.evaluation.spec_b ?? b?.version}</span></div>
        <div class="ev-grid">${ev.pairs.map((p) => html`<div class="ev-row">
          <div class="seed">${p.seed}</div>
          ${['a', 'b'].map((k) => {
            const cell = p[k];
            const r = cell && rs.find((x) => x.id === cell.rollout_id);
            const rep = cell?.report;
            const broke = k === 'a' && p.a_under_b?.status === 'constraint_violated';
            const cup = cupOutcome(rep);
            return html`<button class="ev-cell" aria-pressed=${ctx.pick && ctx.pick === cell?.rollout_id}
              style=${broke ? 'border-color: color-mix(in srgb, var(--bad) 55%, transparent)' : ''}
              disabled=${!r} onClick=${() => r && ctx.setPick(r.id)} aria-label=${`seed ${p.seed} ${k === 'a' ? 'original' : 'corrected'}: ${statusLabel(rep)}`}>
              <${MiniMap} scene=${p.scene || r?.scene} trace=${r?.trace} />
              <div class="m"><${Chip} status=${rep?.status}>${statusLabel(rep)}<//>
                ${cup ? html`<span class="num muted">${fmtNum(cup.max_transport_tilt_deg, 0)}° · ${cup.beads_in_container}/${cup.beads_total}</span>` : ''}
                ${broke ? html`<span class="bad-t">breaks v${snap.evaluation.spec_b ?? b?.version}</span>` : ''}</div>
            </button>`;
          })}</div>`)}</div>`
      : html`<p class="muted">No evaluation yet.</p>`}
      ${ev?.real && ev.raw.scope?.verdict ? html`<h3>Scope</h3><p>${ev.raw.scope.verdict}.</p>
        <p class="faint" style="font-size:12px">Objects the correction does not mention may still tilt when that is cheaper.</p>` : ''}
      ${ev?.real ? html`<details style="margin-top:10px"><summary class="muted" style="cursor:pointer;font-size:12px">Freeze manifest and simplifications</summary>
        <dl class="kv" style="margin-top:8px"><dt>Split</dt><dd>${ev.raw.split}</dd><dt>Frozen</dt><dd>${ev.raw.manifest?.created_at}</dd>
          <dt>Code</dt><dd class="num">${(ev.raw.manifest?.code_sha256 || '').slice(0, 12)} · ${ev.raw.manifest?.code_files} files</dd>
          <dt>Checker</dt><dd>${ev.raw.config?.checker}</dd><dt>Wall time</dt><dd class="num">${fmtNum(ev.raw.wall_time_s, 0)} s</dd></dl>
        <ul style="font-size:12px;padding-left:18px">${(ev.raw.simplifications || []).map((x) => html`<li>${x}</li>`)}</ul></details>` : ''}
    </div>
    <div class="sec">
      <p class="kicker">The blocked case</p>
      <p class="lede">In this layout the slot is the only opening. Carrying the cup upright cannot fit through it. The robot should stop, not break the rule.</p>
      ${!ctx.isStatic ? html`<button class="btn" disabled=${!!ctx.busy.run}
        onClick=${async () => { await ctx.action('scene', () => api.post('scene', { preset: 'blocked' })); await ctx.action('run', () => api.post('run', { condition: 'blocked · v' + b?.version, tags: ['blocked'] })); }}>Try the blocked layout with v${b?.version}</button>
        <${JobLine} job=${ctx.busy.run} />` : ''}
      ${blocked ? html`<div style="margin-top:10px"><${ReportView} r=${blocked} compact=${true} />
        <button class="btn small" style="margin-top:8px" onClick=${() => ctx.setPick(blocked.id)}>Show in viewer</button></div>` : ''}
    </div>`;
}

export const PANELS = { watch: WatchPanel, interpret: InterpretPanel, run: RunPanel, world: WorldPanel, correct: CorrectPanel, evidence: EvidencePanel };
