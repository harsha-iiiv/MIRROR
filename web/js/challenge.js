// "Test MIRROR": an instrumented, guided test of the whole loop on development layouts.
// 1 demonstration and its provenance · 2 inferred requirements · 3 change a layout · 4 correct in
// your own words · 5 review the interpreted object, phase and rule · 6 re-check the old behaviour ·
// 7 run the revised rules · 8 try another layout. Every result carries its own provenance.
// Server API: docs/CHALLENGE_API.md.
import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { MicButton } from './media.js';
import { fmtNum, fmtT, statusLabel, constraintText } from './util.js';
import { demoSource } from './provenance.js';
import { interpretationOutcome } from './outcomes.js';
import { ProvChip, PlanOutcome } from './bits.js';
import * as C from './challenge_state.js';

const html = htm.bind(h);
const short = (s, n = 10) => (s ? String(s).slice(0, n) : '–');
const FLOW_LABELS = ['Demonstration', 'Requirements', 'Layout', 'Correction', 'Review', 'Re-check', 'Run', 'Another layout'];

function Violations({ list }) {
  if (!list || !list.length) return '';
  return html`<ul class="plain">${list.map((v) => html`<li><b>${v.constraint_id || v.id || 'rule'}</b>${v.object_id ? ` · ${v.object_id}` : ''}${v.peak != null ? html` · peak <span class="num">${fmtNum(v.peak)}</span>` : ''}${v.t_start != null ? ` · ${fmtT(v.t_start)}–${fmtT(v.t_end)}` : ''}</li>`)}</ul>`;
}

function beadsText(b) {
  const c = (b?.beads || [])[0];
  return c ? ` · beads ${c.beads_in_container}/${c.beads_total}` : '';
}

// One run's outcome with the plan wording and where the run came from.
function RunResult({ label, st, recordedView = false, expectFresh = false }) {
  if (!st) return '';
  const blocked = st.status === 'planning_failed' || st.plan_status === 'no_plan';
  const bad = st.status && st.status !== 'success';
  return html`<div class=${'callout ' + (blocked ? '' : bad ? 'bad' : 'ok')}>
    <b>${label}:</b> ${blocked ? 'not executed' : statusLabel({ status: st.status, plan_status: st.plan_status })}${beadsText(st)}
    <${Violations} list=${st.violations} />
    ${blocked ? html`<div style="margin-top:6px"><${PlanOutcome} report=${st} plan=${st.plan || null} outcome=${st.planning_outcome || st.outcome || null} compact=${true} /></div>` : ''}
    <div class="step-prov"><${ProvChip} sp=${st.step_provenance} legacy=${st.provenance} recordedView=${recordedView} expectFresh=${expectFresh} /></div>
  </div>`;
}

function ScopeBlock({ proposal }) {
  const d = proposal.diff || {};
  const n = (k) => (d[k] || []).length;
  const io = interpretationOutcome(proposal);
  return html`<div class="ch-scope">
    ${proposal.interpretation || proposal.message ? html`<p>${proposal.interpretation || proposal.message}</p>` : ''}
    <dl class="kv">
      <dt>Objects</dt><dd>${(proposal.scope?.objects || []).join(', ') || '–'}${(proposal.scope?.unaffected_objects || []).length ? html` <span class="muted">(unchanged: ${proposal.scope.unaffected_objects.join(', ')})</span>` : ''}</dd>
      <dt>Phase</dt><dd>${(proposal.scope?.phases || []).map((p) => ({ transport: 'while carried', final: 'when placed', always: 'at all times' }[p] || p)).join(', ') || '–'}</dd>
      <dt>Rules</dt><dd class="num">${(proposal.scope?.items || []).join(', ') || '–'}</dd>
      <dt>Change</dt><dd>${n('added')} added · ${n('changed')} changed · ${n('removed')} removed</dd>
      ${io?.alias ? html`<dt>Read as</dt><dd>${typeof io.alias === 'string' ? io.alias : `“${io.alias.word || io.alias.term || '?'}” → ${io.alias.object_id || io.alias.kind || '?'}${io.alias.basis ? ` (${io.alias.basis})` : ''}`}</dd>` : ''}
      <dt>Input</dt><dd>${proposal.input || 'typed'}</dd>
    </dl>
    <div class="step-prov"><${ProvChip} sp=${proposal.step_provenance} legacy=${proposal.provenance} /></div>
    ${(proposal.ops || []).length ? html`<details class="ov-details"><summary>Operations (${proposal.ops.length})</summary>
      <ul class="list ops">${proposal.ops.map((o) => html`<li><code>${o.op}</code> <code>${o.target_id || ''}</code>${o.rationale ? html`<div class="item-sub">${o.rationale}</div>` : ''}</li>`)}</ul></details>` : ''}
  </div>`;
}

// Questions and refusals: say why, offer measurable choices, change nothing.
function Clarify({ proposal, onPick }) {
  const io = interpretationOutcome(proposal);
  if (!io || io.acceptable) return '';
  if (io.kind === 'apply' && !proposal.changed) return html`<div class="callout" role="status">That did not change the specification.</div>`;
  return html`<div class="callout clarify" role="status">
    <b>${io.title}</b>${io.message && io.message !== io.title ? html`<div style="margin-top:4px">${io.message}</div>` : ''}
    ${io.options.length ? html`<div class="row" style="margin-top:8px">${io.options.map((o) => html`<button class="btn small" onClick=${() => onPick(o)}>${o.label}</button>`)}</div>` : ''}
    ${io.supported.length ? html`<details class="ov-details"><summary>Supported changes</summary><ul class="plain">${io.supported.map((x) => html`<li>${x}</li>`)}</ul></details>` : ''}
    <div class="muted" style="margin-top:4px">${io.kind === 'clarify' ? 'It asks instead of guessing.' : ''} Nothing changed.</div>
    <div class="step-prov"><${ProvChip} sp=${proposal.step_provenance} legacy=${proposal.provenance} /></div>
  </div>`;
}

function RuleLanguage({ options }) {
  const rules = C.supportedRules(options);
  return html`<details class="ov-details ch-rules"><summary>What you can say</summary>
    <p class="muted">Corrections are compiled into a small rule language. These rule kinds are supported by the interpreter, planner and checker:</p>
    <ul class="plain">${rules.map((r) => html`<li>${C.ruleKindText(r)}${(r.phases || []).length ? html` <span class="faint">(${r.phases.join(', ')})</span>` : ''}${r.example ? html`<div class="faint">e.g. “${r.example}”</div>` : ''}</li>`)}</ul>
    <p class="faint">${options?.correction?.note || 'Anything else is answered with a question or refused; nothing changes until you accept a proposal.'}</p>
  </details>`;
}

function Requirements({ snap }) {
  const spec = (snap.specs || [])[snap.specs.length - 1];
  if (!spec) return html`<p class="muted">Not interpreted yet: MIRROR interprets the demonstration when you build the first layout.</p>`;
  const st = (s) => (s === 'confirmed' ? 'required' : s === 'rejected' ? 'not required' : s);
  return html`<div>
    <p class="muted">${spec.summary || ''} <span class="faint num">spec v${spec.version}${spec.version_id ? ' · ' + spec.version_id : ''} · ${spec.interpreter || ''}</span></p>
    <ul class="req-list">${(spec.constraints || []).map((c) => html`<li><span class="grow">${constraintText(c)}</span><span class=${'chip ' + c.status}>${st(c.status)}</span></li>`)}
      ${(spec.ordering || []).map((o) => html`<li><span class="grow">${o.description || `${o.before} before ${o.after}`}</span><span class=${'chip ' + o.status}>${st(o.status)}</span></li>`)}</ul>
    <p class="faint">Only required rules are planned for and checked; proposed ones are suggestions until someone confirms them.</p>
  </div>`;
}

// Study mode (facilitated trials only): explicit opt-in; nothing is recorded without it.
function useStudy() {
  const q = new URLSearchParams(location.search);
  const participant = q.get('study');
  const valid = participant && /^(P\d\d|DEV)$/.test(participant);
  return valid ? { participant, task: q.get('task') || null, condition: q.get('condition') || 'mirror' } : null;
}

function Consent({ study, options, consent, setConsent }) {
  const [rec, setRec] = useState(false), [text, setText] = useState(false), [audio, setAudio] = useState(false);
  const [msg, setMsg] = useState(null);
  if (consent) {
    return html`<p class="faint">${consent.recorded ? `Trial ${study.participant}${study.task ? ' · ' + study.task : ''} is being recorded anonymously${consent.text ? ', with correction text' : ', without correction text'}${consent.audio ? ' and audio' : ', without audio'}.` : 'Nothing from this session is recorded.'}${consent.note ? ' ' + consent.note : ''}</p>`;
  }
  const submit = async (yes) => {
    const c = { recorded: yes && rec, text: yes && rec && text, audio: yes && rec && audio };
    if (!c.recorded) { setConsent({ ...c, note: '' }); return; }
    try {
      await api.post('study/consent', { participant: study.participant, task: study.task, condition: study.condition, consent: c });
      setConsent({ ...c, note: '' });
    } catch (e) {
      setMsg(e.status === 404 ? 'This server does not record trials, so nothing will be stored.' : `Consent could not be saved: ${e.message || e}. Nothing will be recorded.`);
      setConsent({ recorded: false, text: false, audio: false, note: '' });
    }
  };
  return html`<div class="consent" role="group" aria-labelledby="consent-h">
    <p id="consent-h"><b>Before you start (study session ${study.participant})</b></p>
    <p class="muted">The facilitator would like to record this trial: anonymous trial id, the layouts, what MIRROR proposed, what you accepted, results and timing. No name, IP address or browser details are stored.</p>
    <label><input type="checkbox" checked=${rec} onChange=${(e) => setRec(e.target.checked)} /> Record this trial anonymously</label>
    <label><input type="checkbox" checked=${text} disabled=${!rec} onChange=${(e) => setText(e.target.checked)} /> Also keep the words of my corrections</label>
    ${options?.correction?.audio ? html`<label><input type="checkbox" checked=${audio} disabled=${!rec} onChange=${(e) => setAudio(e.target.checked)} /> Also keep audio if I speak a correction</label>` : ''}
    <p class="faint">${options?.study?.retention || 'Retention: as stated in the study data policy the facilitator gave you; ask them before agreeing.'}</p>
    <div class="row"><button class="btn primary" onClick=${() => submit(true)}>Continue</button><button class="btn ghost" onClick=${() => submit(false)}>Continue without recording</button></div>
    ${msg ? html`<p class="muted">${msg}</p>` : ''}
  </div>`;
}

function Comprehension({ truth }) {
  const [ans, setAns] = useState(null);
  const send = (a) => { setAns(a); api.post('study/event', { event: 'comprehension', answer: a, truth }).catch(() => {}); };
  return html`<div class="callout" style="margin-top:8px"><b>Quick question:</b> was the result you just saw computed now, or recorded earlier?
    <div class="row" style="margin-top:6px">${['fresh', 'recorded', 'unsure'].map((a) => html`<button class="btn small" aria-pressed=${ans === a} onClick=${() => send(a)}>${{ fresh: 'Computed now', recorded: 'Recorded', unsure: 'Not sure' }[a]}</button>`)}</div></div>`;
}

// ----------------------------------------------------------------- builder (live)

function ChallengeBuilder({ ctx }) {
  const study = useStudy();
  const [consent, setConsent] = useState(null);
  const [options, setOptions] = useState(null);
  const [optErr, setOptErr] = useState(null);
  const [family, setFamily] = useState('');
  const [preset, setPreset] = useState('shortcut');
  const [seed, setSeed] = useState(4242);
  const [edits, setEdits] = useState([]);
  const [editing, setEditing] = useState(false);
  const [cid, setCid] = useState(null);
  const [ch, setCh] = useState(null);
  const [proposal, setProposal] = useState(null);
  const [text, setText] = useState('');
  const [spoken, setSpoken] = useState(null);
  const [review, setReview] = useState(null);
  const [share, setShare] = useState(null);
  const [showing, setShowing] = useState('before');
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState([]);     // finished challenges: layout and results
  const run = useRef(0);                         // the newest challenge; older replies are ignored
  useEffect(() => {
    api.getJSONApi('challenge/options').then((o) => {
      setOptions(o);
      const fam = (o.families || []).find((f) => f.id === 'shortcut') || (o.families || [])[0];
      if (fam) setFamily(fam.id);
    }).catch((e) => setOptErr(String(e.message || e)));
  }, []);
  const fam = (options?.families || []).find((f) => f.id === family);
  const stage = C.challengeStage(ch, proposal);

  // The stage shows this challenge's runs; edits in the viewer become challenge edits.
  useEffect(() => {
    if (!ch?.before?.rollout_id) { ctx.setStageOverride(null); return; }
    const before = ch.before, after = ch.after;
    const pickAfter = showing === 'after' && after?.rollout_id;
    const r = pickAfter ? after : before;
    ctx.setStageOverride({
      kind: 'rollout', id: r.rollout_id, ghost: pickAfter ? before.rollout_id : null, ghostLabel: 'before the correction',
      rollout: { id: r.rollout_id, report: { status: r.status, violations: r.violations || [], outcomes: [] } },
      title: `Your layout · ${pickAfter ? 'after the correction' : 'current rules'}`,
      sub: `${ch.scene?.family || ''}${ch.scene?.seed != null ? ' seed ' + ch.scene.seed : ''}${(ch.scene?.edits || []).length ? ` · ${ch.scene.edits.length} edit(s)` : ''} · ${statusLabel({ status: r.status, plan_status: r.plan_status })}`,
    });
  }, [ch, showing]);
  useEffect(() => () => { ctx.setStageOverride(null); ctx.setEditHandler(null); ctx.setTool(null); }, []);
  useEffect(() => {
    if (!editing) { ctx.setEditHandler(null); ctx.setTool(null); return; }
    ctx.setTool('drag');
    ctx.setEditHandler((e) => {
      const ed = e.kind === 'tray' ? { tray: { x: +e.x.toFixed(3), y: +e.y.toFixed(3) } }
        : e.kind === 'object' ? { object: { id: e.id, x: +e.x.toFixed(3), y: +e.y.toFixed(3) } }
          : e.kind === 'obstacle' ? { obstacle: { x: +e.x.toFixed(3), y: +e.y.toFixed(3) } } : null;
      if (ed) setEdits((l) => [...l, ed].slice(-(options?.max_edits || 8)));
    });
  }, [editing]);

  const problem = family ? C.seedProblem(options, family, Number(seed)) : 'Choose a layout family.';
  const create = async (overrideSeed = null) => {
    const mine = ++run.current;
    if (ch?.after) setHistory((h) => [...h, { id: ch.id, scene: ch.scene, before: ch.before, after: ch.after, rule: review?.revised_spec_version_id || ch.revised_spec_version_id }]);
    setProposal(null); setReview(null); setShare(null); setShowing('before'); setEditing(false);
    const s = overrideSeed ?? Number(seed);
    const body = fam?.presets ? { family, preset, edits } : { family, seed: s, edits };
    const res = await ctx.action('challenge', () => api.post('challenge', body));
    if (mine !== run.current || !res?.challenge_id) return;
    setCid(res.challenge_id);
    const got = await api.getJSONApi(`challenge/${encodeURIComponent(res.challenge_id)}`).catch((e) => { ctx.setError(String(e.message || e)); return null; });
    if (mine === run.current && got) setCh(got);
  };
  const another = () => {
    const s = C.randomSeed(options, family === 'reference' ? 'shortcut' : family);
    if (family === 'reference') setFamily('shortcut');
    setSeed(s); setEdits([]); setText(''); setSpoken(null);
    create(s);
  };
  const reload = async (mine) => {
    const got = await api.getJSONApi(`challenge/${encodeURIComponent(cid)}`).catch(() => null);
    if (mine === run.current && got) setCh(got);
  };
  const submit = async (answer = null) => {
    const mine = run.current;
    setProposal(null);
    const body = spoken && spoken.text === text ? { transcript_id: spoken.id } : { text: text.trim() };
    if (answer) body.answer = answer;
    const res = await ctx.action('challengeCorrect', () => api.post(`challenge/${encodeURIComponent(cid)}/correction`, body));
    if (mine !== run.current || !res) return;
    setProposal(res.proposal || res);
  };
  const decide = async (accept) => {
    const mine = run.current;
    const res = await ctx.action('challengeReview', () => api.post(`challenge/${encodeURIComponent(cid)}/review`, { accept }));
    if (mine !== run.current) return;
    if (!accept) { setProposal(null); await reload(mine); return; }
    if (res) { setReview(res); await reload(mine); }
  };
  const execute = async () => {
    const mine = run.current;
    const res = await ctx.action('challengeRun', () => api.post(`challenge/${encodeURIComponent(cid)}/execute`, {}));
    if (mine !== run.current || !res) return;
    await reload(mine);
    setShowing('after');
  };
  const publish = async () => {
    const mine = run.current;
    const res = await ctx.action('challengeShare', () => api.post(`challenge/${encodeURIComponent(cid)}/publish`, {}));
    if (mine !== run.current || !res?.record_id) return;
    setShare({ id: res.record_id, url: C.shareUrl(location.href, res.record_id) });
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(share.url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch (_) { setCopied(false); }
  };
  const pickOption = (o) => { setSpoken(null); if (o.answer != null) submit(o.answer); else setText(o.text); };
  const busy = (k) => ctx.busy[k];
  const JobNote = ({ k }) => (busy(k) ? html`<div class="job" role="status" aria-live="polite">${(busy(k).message || 'Working').split('\n')[0]}…
    <div class="progress"><div style=${`width:${Math.round(100 * (busy(k).progress || 0.05))}%`}></div></div></div>` : '');

  if (optErr) return html`<div class="sec"><p class="err">Test mode is not available on this server: ${optErr}</p></div>`;
  if (!options) return html`<div class="sec"><p class="muted">Loading…</p></div>`;
  const recheckOf = review?.rechecks || ch?.rechecks || [];
  const step = ch?.after ? 7 : (recheckOf.length && ch?.revised_spec_version_id) ? 6 : proposal ? 4 : ch ? 3 : 2;
  const micAllowed = !study || consent?.audio || !consent?.recorded;
  const ds = demoSource(ctx.snap);
  const prep = ch?.preparation || [];
  return html`
    <div class="sec">
      <p class="kicker">Test MIRROR</p>
      <h2>Break it, then correct it</h2>
      <p class="lede">Use a development layout the evaluations never used, see what the current rules do, correct one thing in your own words, and run it again. Every result says whether it was computed now or recorded.</p>
      <ol class="ch-flow" aria-label="Test steps">${FLOW_LABELS.map((s, i) => html`
        <li class=${i < step ? 'done' : i === step ? 'cur' : ''} aria-current=${i === step ? 'step' : null}>${i + 1} ${s}</li>`)}</ol>
    </div>
    ${study ? html`<div class="sec"><${Consent} study=${study} options=${options} consent=${consent} setConsent=${setConsent} /></div>` : ''}
    ${study && !consent ? '' : html`
    <div class="sec">
      <p class="ov-k">1 · The demonstration</p>
      <p>${ds.long}</p>
      ${prep.length ? html`<ul class="plain">${prep.map((p) => html`<li>${p.step}<div class="step-prov"><${ProvChip} sp=${p.step_provenance} legacy=${p.provenance} /></div></li>`)}</ul>` : ''}
    </div>
    <div class="sec">
      <p class="ov-k">2 · What MIRROR inferred</p>
      <${Requirements} snap=${ctx.snap} />
    </div>
    <div class="sec">
      <p class="ov-k">3 · Change a layout</p>
      <div class="ch-form">
        <label>Family<select value=${family} onChange=${(e) => setFamily(e.target.value)} disabled=${!!busy('challenge')}>
          ${(options.families || []).map((f) => html`<option value=${f.id} selected=${f.id === family}>${f.id}${f.split ? ` (${f.split})` : ''}</option>`)}</select></label>
        ${fam?.presets ? html`<label>Layout<select value=${preset} onChange=${(e) => setPreset(e.target.value)}>${fam.presets.map((p) => html`<option value=${p}>${p}</option>`)}</select></label>`
          : html`<label>Seed<span class="row"><input class="num-in" type="number" inputmode="numeric" value=${seed} min=${fam?.seed_range?.[0]} max=${fam?.seed_range?.[1]}
              onInput=${(e) => setSeed(e.target.value === '' ? '' : Number(e.target.value))} aria-describedby="seed-help" />
              <button class="btn small" onClick=${() => setSeed(C.randomSeed(options, family))}>Random</button></span></label>`}
      </div>
      <p id="seed-help" class="faint">Development seeds only; seeds used by a frozen evaluation are refused by the server.${options.fenced_seed_count ? ` ${options.fenced_seed_count} seeds are fenced.` : ''}</p>
      ${edits.length ? html`<div class="row ch-edits"><span class="muted">${edits.length} edit${edits.length === 1 ? '' : 's'}:</span>
        ${edits.map((e, i) => html`<span class="chip">${Object.keys(e)[0]}${e.object ? ' ' + e.object.id : ''}<button class="x" aria-label=${'Remove edit ' + (i + 1)} onClick=${() => setEdits(edits.filter((_, j) => j !== i))}>✕</button></span>`)}</div>` : ''}
      <div class="row" style="margin-top:8px">
        ${ch ? html`<button class="btn small" aria-pressed=${editing} onClick=${() => setEditing(!editing)}>${editing ? 'Done editing' : 'Move the tray or an object'}</button>
          <button class="btn small" onClick=${() => setEdits([...edits, { slot: 'on' }])}>Open a slot</button>
          <button class="btn small" onClick=${() => setEdits([...edits, { slot: 'off' }])}>Close the slot</button>` : ''}
        <span class="spacer"></span>
        <button class=${'btn ' + (ch ? '' : 'primary')} disabled=${!!problem || !!busy('challenge')} onClick=${() => create()}>${ch ? (edits.length ? 'Rerun with these edits' : 'New layout') : 'Build it and run the current rules'}</button>
      </div>
      ${problem && family ? html`<p class="err">${problem}</p>` : ''}
      ${editing ? html`<p class="faint">Drag the tray or an object in the 3D view. Edits apply when you rerun.</p>` : ''}
      <${JobNote} k="challenge" />
      ${ch ? html`<div style="margin-top:10px"><${RunResult} label="Current rules on this layout" st=${ch.before} expectFresh=${true} />
        <p class="faint num">scene ${ch.scene?.scene_id || ''} · ${short(ch.scene?.content_sha256)} · spec ${ch.base_spec_version_id || '–'}</p></div>` : ''}
    </div>
    ${ch ? html`<div class="sec">
      <p class="ov-k">4 · Correct one thing, in your own words</p>
      <label class="sr-only" for="ch-corr">Correction</label>
      <textarea id="ch-corr" class="corr" maxlength=${options.correction?.max_chars || 600} placeholder="Say what was wrong about the behaviour"
        value=${text} onInput=${(e) => setText(e.target.value)} disabled=${!!ch.after}
        onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) submit(); }}></textarea>
      ${spoken ? html`<div class="step-prov">${spoken.text === text ? 'Transcribed' : 'Transcribed, then edited'} · <${ProvChip} sp=${spoken.step_provenance} legacy=${spoken.provenance || spoken.source} /></div>` : ''}
      <div class="row" style="margin-top:8px">
        ${options.correction?.audio && ctx.snap.capabilities?.voice && micAllowed ? html`<${MicButton} ctx=${ctx} disabled=${!!busy('challengeCorrect')} onTranscript=${(tr) => { setSpoken(tr); setText(tr.text); }} />` : ''}
        ${options.correction?.audio && study && consent?.recorded && !consent?.audio ? html`<span class="faint">Speaking is off: you did not agree to keep audio.</span>` : ''}
        <span class="spacer"></span>
        <button class="btn primary" disabled=${!text.trim() || !!busy('challengeCorrect') || !!ch.after} onClick=${() => submit()}>Interpret my correction</button>
      </div>
      <${RuleLanguage} options=${options} />
      <${JobNote} k="challengeCorrect" />
    </div>` : ''}
    ${proposal ? html`<div class="sec">
      <p class="ov-k">5 · Review what MIRROR understood</p>
      <${Clarify} proposal=${proposal} onPick=${pickOption} />
      ${C.proposalAcceptable(proposal) ? html`<${ScopeBlock} proposal=${proposal} />
        ${!review ? html`<div class="row" style="margin-top:8px"><button class="btn ghost" onClick=${() => decide(false)}>Discard</button><span class="spacer"></span>
          <button class="btn primary" disabled=${!!busy('challengeReview')} onClick=${() => decide(true)}>Accept and re-check</button></div>` : ''}` : ''}
      <${JobNote} k="challengeReview" />
    </div>` : ''}
    ${review ? html`<div class="sec">
      <p class="ov-k">6 · The old behaviour, checked again <span class="num id-case">${review.revised_spec_version_id || ''}</span></p>
      <ul class="list">${recheckOf.map((r) => html`<li><div class="row"><span class="num muted">${r.label || short(r.rollout_id, 28)}${r.is_before ? ' · your layout' : ''}</span><span class="spacer"></span>
        ${r.was ? html`<span class="faint">was ${statusLabel({ status: r.was })} →</span>` : ''}<span class=${'chip ' + r.status}>${statusLabel({ status: r.status })}</span></div><${Violations} list=${r.violations} />
        <div class="step-prov"><${ProvChip} sp=${r.step_provenance} legacy=${r.provenance} /></div></li>`)}</ul>
      <p class="faint">The checker reads each run's recorded physics log under the revised rules; it does not ask the planner and repeats no run.</p>
    </div>
    <div class="sec">
      <p class="ov-k">7 · Run the revised rules</p>
      ${ch.after ? html`<${RunResult} label="Revised rules on this layout" st=${ch.after} expectFresh=${true} />
        <div class="seg ov-seg" role="group" aria-label="Which run the stage shows">
          <button aria-pressed=${showing === 'before'} onClick=${() => setShowing('before')}>Before</button>
          <button aria-pressed=${showing === 'after'} onClick=${() => setShowing('after')}>After</button></div>
        ${study && consent?.recorded ? html`<${Comprehension} truth=${ch.after.step_provenance?.kind || ch.after.provenance || 'unknown'} />` : ''}`
        : html`<button class="btn primary" disabled=${!!busy('challengeRun')} onClick=${execute}>Plan and run in MuJoCo</button>`}
      <${JobNote} k="challengeRun" />
    </div>` : ''}
    ${ch?.after ? html`<div class="sec">
      <p class="ov-k">8 · Try another layout with the revised rules</p>
      <p class="muted">A new development layout, never seen by this correction. The rules you accepted are now the current rules.</p>
      <div class="row"><button class="btn primary" disabled=${!!busy('challenge')} onClick=${another}>Try another layout</button><span class="spacer"></span>
        ${share ? '' : html`<button class="btn" disabled=${!!busy('challengeShare')} onClick=${publish}>Publish and get a link</button>`}</div>
      ${share ? html`<div class="row ch-share" style="margin-top:8px"><input readonly value=${share.url} aria-label="Share link" onFocus=${(e) => e.target.select()} />
        <button class="btn small" onClick=${copy}>${copied ? 'Copied' : 'Copy'}</button></div>
        <p class="faint">The link opens record ${share.id}: the layout, both spec versions, the correction and both runs, frozen.</p>` : ''}
    </div>` : ''}
    ${history.length ? html`<div class="sec"><p class="ov-k">Earlier layouts in this test</p><ul class="transfer-list">${history.map((x) => html`<li>
      <span class="num">${x.scene?.family} ${x.scene?.seed ?? ''}</span><span class=${'chip ' + x.before?.status}>before: ${statusLabel({ status: x.before?.status })}</span>
      <span class=${'chip ' + x.after?.status}>after: ${statusLabel({ status: x.after?.status })}</span></li>`)}</ul></div>` : ''}`}`;
}

// --------------------------------------------------------- record (shared or tour)

function RecordPanel({ ctx, record, tour }) {
  const [view, setView] = useState({ id: record.rollouts?.before?.rollout_id, ghost: null });
  const [i, setI] = useState(0);
  const rb = record.rollouts?.before, ra = record.rollouts?.after;
  const transfer = record.transfer || [];
  const shaOf = (id) => {
    for (const r of [rb, ra, ...transfer.flatMap((t) => [t.base, t.revised, t.after, t.base_under_revised])]) if (r?.rollout_id === id) return r.content_sha256 || '';
    return '';
  };
  useEffect(() => {
    if (!view.id) { ctx.setStageOverride(null); return; }
    const load = (id) => api.getRecordRollout(record, id, shaOf(id));
    const brief = [rb, ra, ...transfer.flatMap((t) => [t.base, t.revised, t.after])].find((r) => r?.rollout_id === view.id) || {};
    ctx.setStageOverride({
      kind: 'rollout', id: view.id, ghost: view.ghost, ghostLabel: 'before the correction', load,
      cacheKey: `record|${record.record_id}|${view.id}`, ghostKey: view.ghost ? `record|${record.record_id}|${view.ghost}` : '',
      rollout: { id: view.id, report: { status: brief.status, violations: brief.violations || [], outcomes: [] } },
      title: `${tour ? 'Recorded tour' : 'Shared test'} · ${view.label || (view.id === ra?.rollout_id ? 'after' : 'before')}`,
      sub: `${brief.scene_id || record.scene?.scene_id || ''} · ${statusLabel({ status: brief.status, plan_status: brief.plan_status })} · recorded playback`,
    });
  }, [view.id, view.ghost, record]);
  useEffect(() => () => ctx.setStageOverride(null), []);
  const pv = record.provenance || {};
  const corr = record.correction || {};
  const showBefore = () => setView({ id: rb?.rollout_id, ghost: null, label: 'before' });
  const showAfter = () => setView({ id: ra?.rollout_id, ghost: rb?.rollout_id, label: 'after' });
  const steps = [
    { k: 'Layout', go: showBefore, body: html`<p>${record.scene?.family || 'layout'}${record.scene?.seed != null ? ` · seed ${record.scene.seed}` : ''} (${record.scene?.split || 'dev'})${(record.scene?.edits || []).length ? ` · ${record.scene.edits.length} edit(s)` : ''}.</p>
      <p class="muted">${record.generation?.demo?.source ? `Demonstration: ${demoSource({ demo: { source: record.generation.demo.source } }).short}.` : ''}</p>` },
    { k: 'Before', go: showBefore, body: html`<${RunResult} label="Under the base rules" st=${rb} recordedView=${true} />` },
    { k: 'Correction', go: showBefore, body: html`<blockquote class="quote ov-quote">“${corr.text || ''}”</blockquote>
      <p class="muted">${corr.input || 'typed'}${corr.model ? ` · ${corr.model}` : ''}</p>
      <div class="step-prov"><${ProvChip} sp=${corr.step_provenance} legacy=${corr.provenance} recordedView=${true} /></div>
      <p>Objects: ${(corr.scope?.objects || []).join(', ') || '–'}${(corr.scope?.unaffected_objects || []).length ? ` (unchanged: ${corr.scope.unaffected_objects.join(', ')})` : ''} · phase: ${C.scopeText({ phases: corr.scope?.phases }) || '–'}</p>` },
    { k: 'Re-check', go: showBefore, body: html`<ul class="list">${(record.rechecks || []).map((r) => html`<li><div class="row"><span class="num muted">${r.label || short(r.rollout_id, 28)}${r.is_before ? ' · the layout above' : ''}</span><span class="spacer"></span>
      ${r.was ? html`<span class="faint">was ${statusLabel({ status: r.was })} →</span>` : ''}<span class=${'chip ' + r.status}>${statusLabel({ status: r.status })}</span></div><${Violations} list=${r.violations} /></li>`)}</ul>
      <p class="faint">Checked from the recorded physics log of the same run; nothing re-ran.</p>` },
    { k: 'After', go: showAfter, body: html`<${RunResult} label="Under the revised rules" st=${ra} recordedView=${true} />` },
  ];
  if (transfer.length) {
    steps.push({ k: 'Other layouts', go: showAfter, body: html`<p class="muted">The same correction on layouts it never saw:</p><ul class="transfer-list">${transfer.map((t) => {
      const b = t.base, a = t.revised || t.after;
      return html`<li><span class="num">${(b || a)?.scene_id || ''}</span>
        ${b ? html`<button class="btn small" onClick=${() => setView({ id: b.rollout_id, ghost: null, label: 'old rules · ' + b.scene_id })}>old rules: ${statusLabel({ status: (t.base_under_revised || b).status })}${beadsText(b)}</button>` : ''}
        ${a ? html`<button class="btn small" onClick=${() => setView({ id: a.rollout_id, ghost: b?.rollout_id || null, label: 'revised · ' + a.scene_id })}>revised: ${a.plan_status === 'no_plan' ? 'not executed' : statusLabel({ status: a.status })}${beadsText(a)}</button>` : ''}</li>`;
    })}</ul><p class="faint">"Old rules" is the old run judged by the revised rules.</p>` });
  }
  const go = (j) => { setI(j); steps[j].go(); };
  return html`
    <div class="sec">
      <p class="kicker">${tour ? 'Recorded tour' : 'Shared test'}</p>
      <h2>${tour ? 'A test of MIRROR, step by step' : 'Someone broke it, then corrected it'}</h2>
      <p class="prov-line"><span class="src recorded">recorded</span>
        ${record.label || (pv.recorded ? 'Produced offline and shipped with the site; nothing here calls a model or the simulator.'
          : `Computed on a MIRROR server${record.created_at ? ' on ' + new Date(record.created_at * 1000).toLocaleString() : ''}; this page replays the stored record and computes nothing.`)}
        ${pv.simulation === 'fixture' ? ' Fixture motion, not physics.' : ''}</p>
      <ol class="ch-flow">${steps.map((s, j) => html`<li class=${j < i ? 'done' : j === i ? 'cur' : ''}><button class="linkish" onClick=${() => go(j)} aria-current=${j === i ? 'step' : null}>${s.k}</button></li>`)}</ol>
      <div class="ch-tour-body">${steps[i].body}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn ghost small" disabled=${i === 0} onClick=${() => go(i - 1)}>Back</button><span class="spacer"></span>
        ${i < steps.length - 1 ? html`<button class="btn primary" onClick=${() => go(i + 1)}>Next: ${steps[i + 1].k}</button>`
          : (api.config.liveAvailable ? html`<a class="btn primary" href=${tryHref()}>Test it yourself</a>` : '')}
      </div>
    </div>
    <div class="sec"><details class="ov-details"><summary>Record detail</summary>
      <dl class="kv">
        <dt>Record</dt><dd class="num">${record.record_id || '–'} · ${record.format || ''}</dd>
        <dt>Scene</dt><dd class="num">${record.scene?.scene_id || '–'} · ${short(record.scene?.content_sha256, 16)}</dd>
        <dt>Demo</dt><dd class="num">${record.generation?.demo?.demo_id || '–'} · ${short(record.generation?.demo?.content_sha256, 16)}</dd>
        <dt>Base spec</dt><dd class="num">${record.spec_versions?.base?.version_id || '–'} · ${short(record.spec_versions?.base?.content_sha256, 16)}</dd>
        <dt>Revised spec</dt><dd class="num">${record.spec_versions?.revised?.version_id || '–'} · ${short(record.spec_versions?.revised?.content_sha256, 16)}</dd>
        <dt>Before run</dt><dd class="num">${rb?.rollout_id || '–'} · ${short(rb?.content_sha256, 16)}</dd>
        <dt>After run</dt><dd class="num">${ra?.rollout_id || '–'} · ${short(ra?.content_sha256, 16)}</dd>
        <dt>Interpreter</dt><dd class="num">${pv.recorded_with?.interpreter || pv.interpreter || '–'}</dd>
        <dt>Recorded with</dt><dd class="num">${pv.recorded_with?.code_version || pv.server_version || '–'}${pv.recorded_with?.simulation ? ' · ' + pv.recorded_with.simulation : ''}</dd>
      </dl></details></div>`;
}

function tryHref() {
  const u = new URL(location.href);
  u.searchParams.delete('challenge');
  u.searchParams.set('step', 'challenge');
  if (api.config.recordings) u.searchParams.set('live', '1');
  return u.toString();
}

// ------------------------------------------------------------------ entry point

export function ChallengePanel({ ctx }) {
  const [rec, setRec] = useState(null);      // { record, tour } | { error } | { none }
  const [wantTour, setWantTour] = useState(false);
  const recordId = C.recordIdFromUrl(location.search);
  const useRecord = recordId || ctx.isStatic || wantTour;
  useEffect(() => {
    if (recordId) {
      api.getRecord(recordId).then((r) => setRec({ record: r, tour: false }))
        .catch((e) => setRec({ error: e.status === 410 ? 'This shared test has expired from the server.' : `Could not load the shared test: ${e.message || e}` }));
    } else if (ctx.isStatic || wantTour) {
      api.getTour().then((r) => setRec({ record: { ...r, __tour: true }, tour: true }))
        .catch(() => setRec({ none: true }));
    }
  }, [recordId, ctx.isStatic, wantTour]);
  if (useRecord) {
    if (!rec) return html`<div class="sec"><p class="muted">Loading ${recordId ? 'the shared test' : 'the recorded tour'}…</p></div>`;
    if (rec.error) return html`<div class="sec"><p class="err">${rec.error}</p></div>`;
    if (rec.none) {
      return html`<div class="sec"><p class="kicker">Test MIRROR</p><h2>Testing runs on the live simulation</h2>
        <p class="lede">This page has no recorded tour. ${api.config.liveAvailable ? 'Switch to the live simulation to test it yourself.' : 'Run the studio server to test it yourself.'}</p>
        ${api.config.liveAvailable && ctx.isStatic ? html`<a class="btn primary" href=${tryHref()}>Open the live simulation</a>` : ''}
        ${wantTour ? html`<button class="btn" onClick=${() => { setWantTour(false); setRec(null); }}>Back</button>` : ''}</div>`;
    }
    return html`${wantTour ? html`<div class="sec"><button class="btn ghost small" onClick=${() => { setWantTour(false); setRec(null); ctx.setStageOverride(null); }}>← Test it yourself instead</button></div>` : ''}
      <${RecordPanel} ctx=${ctx} record=${rec.record} tour=${rec.tour} />`;
  }
  return html`<div class="sec ov-tourlink"><button class="btn ghost small" onClick=${() => setWantTour(true)}>Watch the recorded tour first</button></div>
    <${ChallengeBuilder} ctx=${ctx} />`;
}
