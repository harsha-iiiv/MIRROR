// Challenge mode: change a development layout, correct one rule, see the old behaviour
// re-checked, run the revised rule, and share the exact result. Server API: docs/CHALLENGE_API.md.
import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { MicButton } from './media.js';
import { fmtNum, fmtT, statusLabel } from './util.js';
import { provenanceText, provenanceTone } from './provenance.js';
import * as C from './challenge_state.js';

const html = htm.bind(h);

const Prov = ({ p }) => (p ? html`<span class=${'src ' + provenanceTone(p)} title="Where this came from">${provenanceText(p)}</span>` : '');
const short = (s, n = 10) => (s ? String(s).slice(0, n) : '–');

function Violations({ list }) {
  if (!list || !list.length) return '';
  return html`<ul class="plain">${list.map((v) => html`<li><b>${v.constraint_id || v.id || 'rule'}</b>${v.object_id ? ` · ${v.object_id}` : ''}${v.peak != null ? html` · peak <span class="num">${fmtNum(v.peak)}</span>` : ''}${v.t_start != null ? ` · ${fmtT(v.t_start)}–${fmtT(v.t_end)}` : ''}</li>`)}</ul>`;
}

function StatusLine({ label, st }) {
  if (!st) return '';
  const bad = st.status && st.status !== 'success';
  return html`<div class=${'callout ' + (bad ? 'bad' : 'ok')}><b>${label}:</b> ${statusLabel({ status: st.status, plan_status: st.plan_status })}
    <${Violations} list=${st.violations} /></div>`;
}

function ScopeBlock({ proposal }) {
  if (!proposal) return '';
  const d = proposal.diff || {};
  const n = (k) => (d[k] || []).length;
  return html`<div class="ch-scope">
    <dl class="kv">
      <dt>Input</dt><dd>${proposal.input || 'typed'} · <${Prov} p=${proposal.provenance} />${proposal.fallback_reason ? html` <span class="muted">(${proposal.fallback_reason})</span>` : ''}</dd>
      <dt>Scope</dt><dd>${C.scopeText(proposal.scope) || '–'}</dd>
      <dt>Change</dt><dd>${n('added')} added · ${n('changed')} changed · ${n('removed')} removed</dd>
    </dl>
    ${(proposal.ops || []).length ? html`<details class="ov-details"><summary>Operations (${proposal.ops.length})</summary>
      <ul class="list ops">${proposal.ops.map((o) => html`<li><code>${o.op}</code> <code>${o.target_id || ''}</code>${o.rationale ? html`<div class="item-sub">${o.rationale}</div>` : ''}</li>`)}</ul></details>` : ''}
  </div>`;
}

function RuleLanguage({ options }) {
  const rules = C.supportedRules(options);
  return html`<details class="ov-details ch-rules"><summary>What you can say</summary>
    <p class="muted">Corrections are compiled into a small rule language. These rule kinds are supported end to end (interpreter, planner and checker):</p>
    <ul class="plain">${rules.map((r) => html`<li>${C.ruleKindText(r)}${r.phases ? html` <span class="faint">(${r.phases.join(', ')})</span>` : ''}${r.example ? html`<div class="faint">e.g. “${r.example}”</div>` : ''}</li>`)}</ul>
    <p class="faint">Anything else is refused or answered with a question; nothing changes until you accept a proposal.</p>
  </details>`;
}

// ----------------------------------------------------------------- builder (live)

function ChallengeBuilder({ ctx }) {
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
  const run = useRef(0);                     // the newest challenge; older replies are ignored
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
      title: `Your challenge · ${pickAfter ? 'after the correction' : 'before'}`,
      sub: `${ch.scene?.family || ''}${ch.scene?.seed != null ? ' seed ' + ch.scene.seed : ''}${(ch.scene?.edits || []).length ? ` · ${ch.scene.edits.length} edit(s)` : ''} · ${statusLabel({ status: r.status })}`,
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
  const create = async () => {
    const mine = ++run.current;
    setProposal(null); setReview(null); setShare(null); setShowing('before'); setEditing(false);
    const body = fam?.presets ? { family, preset, edits } : { family, seed: Number(seed), edits };
    const res = await ctx.action('challenge', () => api.post('challenge', body));
    if (mine !== run.current || !res?.challenge_id) return;
    setCid(res.challenge_id);
    const got = await api.getJSONApi(`challenge/${encodeURIComponent(res.challenge_id)}`).catch((e) => { ctx.setError(String(e.message || e)); return null; });
    if (mine === run.current && got) setCh(got);
  };
  const reload = async (mine) => {
    const got = await api.getJSONApi(`challenge/${encodeURIComponent(cid)}`).catch(() => null);
    if (mine === run.current && got) setCh(got);
  };
  const submit = async () => {
    const mine = run.current;
    setProposal(null);
    const body = spoken && spoken.text === text ? { transcript_id: spoken.id } : { text: text.trim() };
    const res = await ctx.action('challengeCorrect', () => api.post(`challenge/${encodeURIComponent(cid)}/correction`, body));
    if (mine !== run.current || !res) return;
    setProposal(res.proposal || res);
  };
  const decide = async (accept) => {
    const mine = run.current;
    const res = await ctx.action('challengeReview', () => api.post(`challenge/${encodeURIComponent(cid)}/review`, { accept }));
    if (mine !== run.current) return;
    if (!accept) { setProposal(null); return; }
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
  const busy = (k) => ctx.busy[k];
  const JobNote = ({ k }) => (busy(k) ? html`<div class="job" role="status" aria-live="polite">${(busy(k).message || 'Working').split('\n')[0]}…
    <div class="progress"><div style=${`width:${Math.round(100 * (busy(k).progress || 0.05))}%`}></div></div></div>` : '');

  if (optErr) return html`<div class="sec"><p class="err">Challenge mode is not available on this server: ${optErr}</p></div>`;
  if (!options) return html`<div class="sec"><p class="muted">Loading challenge options…</p></div>`;
  const step = C.FLOW.indexOf(stage);
  return html`
    <div class="sec">
      <p class="kicker">Challenge</p>
      <h2>Break it, then correct it</h2>
      <p class="lede">Pick a development layout the evaluation never used, see what the current rules do, correct one thing in your own words, and run it again.</p>
      <ol class="ch-flow" aria-label="Challenge steps">${['Layout', 'Before', 'Correct', 'Review', 'Run', 'Share'].map((s, i) => html`
        <li class=${i < step ? 'done' : i === step ? 'cur' : ''} aria-current=${i === step ? 'step' : null}>${s}</li>`)}</ol>
    </div>
    <div class="sec">
      <p class="ov-k">1 · Layout</p>
      <div class="ch-form">
        <label>Family<select value=${family} onChange=${(e) => setFamily(e.target.value)} disabled=${!!busy('challenge')}>
          ${(options.families || []).map((f) => html`<option value=${f.id}>${f.id}${f.split ? ` (${f.split})` : ''}</option>`)}</select></label>
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
        <button class=${'btn ' + (ch ? '' : 'primary')} disabled=${!!problem || !!busy('challenge')} onClick=${create}>${ch ? (edits.length ? 'Rerun with these edits' : 'New challenge') : 'Build it and run the current rules'}</button>
      </div>
      ${problem && family ? html`<p class="err">${problem}</p>` : ''}
      ${editing ? html`<p class="faint">Drag the tray or an object in the 3D view. Edits apply when you rerun.</p>` : ''}
      <${JobNote} k="challenge" />
    </div>
    ${ch ? html`<div class="sec">
      <p class="ov-k">2 · Before: the current rules on your layout</p>
      <${StatusLine} label="Prior behaviour under the current rules" st=${ch.before} />
      <p class="faint">scene ${ch.scene?.scene_id || ''} · ${short(ch.scene?.content_sha256)} · spec ${ch.base_spec_version_id || '–'}</p>
    </div>
    <div class="sec">
      <p class="ov-k">3 · Correct one thing</p>
      <label class="sr-only" for="ch-corr">Correction</label>
      <textarea id="ch-corr" class="corr" maxlength=${options.correction?.max_chars || 600} placeholder=${C.supportedRules(options)[0]?.example || ''}
        value=${text} onInput=${(e) => setText(e.target.value)} disabled=${stage === 'execute' || stage === 'share'}
        onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) submit(); }}></textarea>
      ${spoken ? html`<p class="faint">${spoken.text === text ? 'Transcribed' : 'Transcribed, then edited'} · ${spoken.model} · ${provenanceText(spoken.provenance || spoken.source)}</p>` : ''}
      <div class="row" style="margin-top:8px">
        ${options.correction?.audio && ctx.snap.capabilities?.voice ? html`<${MicButton} ctx=${ctx} disabled=${!!busy('challengeCorrect')} onTranscript=${(tr) => { setSpoken(tr); setText(tr.text); }} />` : ''}
        <span class="spacer"></span>
        <button class="btn primary" disabled=${!text.trim() || !!busy('challengeCorrect') || stage === 'execute' || stage === 'share'} onClick=${submit}>Interpret my correction</button>
      </div>
      <${RuleLanguage} options=${options} />
      <${JobNote} k="challengeCorrect" />
    </div>` : ''}
    ${proposal ? html`<div class="sec">
      <p class="ov-k">4 · Review what MIRROR understood</p>
      ${proposal.question ? html`<div class="callout" role="status"><b>MIRROR needs to know:</b> ${proposal.question}
        ${(proposal.options || []).length ? html`<div class="row" style="margin-top:8px">${proposal.options.map((o) => html`<button class="btn small" onClick=${() => { setSpoken(null); setText(o); }}>${o}</button>`)}</div>` : ''}
        <div class="muted" style="margin-top:4px">It asks instead of guessing. Nothing changed.</div></div>` : ''}
      ${proposal.rejected ? html`<div class="callout" role="status"><b>Not supported:</b> ${proposal.rejected}<div class="muted">Nothing changed.</div></div>` : ''}
      ${!proposal.question && !proposal.rejected && !proposal.changed ? html`<div class="callout" role="status">That did not change the specification.</div>` : ''}
      ${C.proposalAcceptable(proposal) ? html`<${ScopeBlock} proposal=${proposal} />
        ${!review ? html`<div class="row" style="margin-top:8px"><button class="btn ghost" onClick=${() => decide(false)}>Discard</button><span class="spacer"></span>
          <button class="btn primary" disabled=${!!busy('challengeReview')} onClick=${() => decide(true)}>Accept and re-check</button></div>` : ''}` : ''}
      <${JobNote} k="challengeReview" />
    </div>` : ''}
    ${review ? html`<div class="sec">
      <p class="ov-k">Re-checked under the revised rules <span class="num id-case">${review.revised_spec_version_id}</span></p>
      <ul class="list">${(review.rechecks || []).map((r) => html`<li><div class="row"><span class="num muted">${short(r.rollout_id, 28)}</span><span class="spacer"></span>
        <span class=${'chip ' + r.status}>${statusLabel({ status: r.status })}</span></div><${Violations} list=${r.violations} /></li>`)}</ul>
      <p class="faint">The checker reads each run's recorded physics log under the revised rules; no run is repeated for this.</p>
    </div>
    <div class="sec">
      <p class="ov-k">5 · Run the revised rules</p>
      ${ch.after ? html`<${StatusLine} label="After" st=${ch.after} />
        <div class="seg ov-seg" role="group" aria-label="Which run the stage shows">
          <button aria-pressed=${showing === 'before'} onClick=${() => setShowing('before')}>Before</button>
          <button aria-pressed=${showing === 'after'} onClick=${() => setShowing('after')}>After</button></div>`
        : html`<button class="btn primary" disabled=${!!busy('challengeRun')} onClick=${execute}>Plan and run in MuJoCo</button>`}
      <${JobNote} k="challengeRun" />
    </div>` : ''}
    ${ch?.after ? html`<div class="sec">
      <p class="ov-k">6 · Share this exact result</p>
      ${share ? html`<div class="row ch-share"><input readonly value=${share.url} aria-label="Share link" onFocus=${(e) => e.target.select()} />
        <button class="btn small" onClick=${copy}>${copied ? 'Copied' : 'Copy'}</button></div>
        <p class="faint">The link opens record ${share.id}: the layout, both spec versions, the correction and both runs, frozen.</p>`
        : html`<button class="btn" disabled=${!!busy('challengeShare')} onClick=${publish}>Publish and get a link</button>`}
    </div>` : ''}`;
}

// --------------------------------------------------------- record (shared or tour)

function RecordPanel({ ctx, record, tour }) {
  const [showing, setShowing] = useState('before');
  const [i, setI] = useState(0);
  const rb = record.rollouts?.before, ra = record.rollouts?.after;
  useEffect(() => {
    const r = showing === 'after' && ra?.rollout_id ? ra : rb;
    if (!r?.rollout_id) { ctx.setStageOverride(null); return; }
    const load = (id) => api.getRecordRollout(record, id, id === ra?.rollout_id ? ra?.content_sha256 : rb?.content_sha256);
    ctx.setStageOverride({
      kind: 'rollout', id: r.rollout_id, ghost: r === ra ? rb?.rollout_id : null, ghostLabel: 'before the correction', load,
      cacheKey: `record|${record.record_id}|${r.rollout_id}`, ghostKey: r === ra ? `record|${record.record_id}|${rb?.rollout_id}` : '',
      rollout: { id: r.rollout_id, report: { status: r.status, violations: r.violations || [], outcomes: [] } },
      title: `${tour ? 'Recorded tour' : 'Shared challenge'} · ${r === ra ? 'after' : 'before'}`,
      sub: `${record.scene?.family || ''}${record.scene?.seed != null ? ' seed ' + record.scene.seed : ''} · ${statusLabel({ status: r.status })}`,
    });
  }, [showing, record]);
  useEffect(() => () => ctx.setStageOverride(null), []);
  const pv = record.provenance || {};
  const corr = record.correction || {};
  const steps = [
    { k: 'Layout', body: html`<p>${record.scene?.family || 'layout'}${record.scene?.seed != null ? ` · seed ${record.scene.seed}` : ''} (${record.scene?.split || 'dev'})${(record.scene?.edits || []).length ? ` · ${record.scene.edits.length} edit(s)` : ''}.</p>`, show: 'before' },
    { k: 'Before', body: html`<${StatusLine} label="Under the base rules" st=${rb} />`, show: 'before' },
    { k: 'Correction', body: html`<blockquote class="quote ov-quote">“${corr.text || ''}”</blockquote>
      <p class="muted">${corr.input || 'typed'} · <${Prov} p=${corr.provenance} />${corr.fallback_reason ? ` (${corr.fallback_reason})` : ''}</p>
      <p>Scope: ${C.scopeText(corr.scope) || '–'}</p>`, show: 'before' },
    { k: 'Re-check', body: html`<ul class="list">${(record.rechecks || []).map((r) => html`<li><div class="row"><span class="num muted">${short(r.rollout_id, 28)}</span><span class="spacer"></span>
      <span class=${'chip ' + r.status}>${statusLabel({ status: r.status })}</span></div><${Violations} list=${r.violations} /></li>`)}</ul>`, show: 'before' },
    { k: 'After', body: html`<${StatusLine} label="Under the revised rules" st=${ra} />`, show: 'after' },
  ];
  const go = (j) => { setI(j); setShowing(steps[j].show); };
  return html`
    <div class="sec">
      <p class="kicker">${tour ? 'Recorded tour' : 'Shared challenge'}</p>
      <h2>${tour ? 'A challenge, step by step' : 'Someone broke it, then corrected it'}</h2>
      <p class="prov-line"><span class=${'src ' + (pv.recorded ? 'recorded' : 'live')}>${pv.recorded ? 'recorded' : 'published record'}</span>
        ${pv.recorded ? 'Produced offline and shipped with the site; nothing here calls a model or the simulator.'
          : `Computed on a MIRROR server${record.created_at ? ' on ' + new Date(record.created_at * 1000).toLocaleString() : ''}; this page replays the stored record.`}
        ${pv.simulation === 'fixture' ? ' Fixture motion, not physics.' : ''}</p>
      <ol class="ch-flow">${steps.map((s, j) => html`<li class=${j < i ? 'done' : j === i ? 'cur' : ''}><button class="linkish" onClick=${() => go(j)} aria-current=${j === i ? 'step' : null}>${s.k}</button></li>`)}</ol>
      <div class="ch-tour-body">${steps[i].body}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn ghost small" disabled=${i === 0} onClick=${() => go(i - 1)}>Back</button><span class="spacer"></span>
        ${i < steps.length - 1 ? html`<button class="btn primary" onClick=${() => go(i + 1)}>Next: ${steps[i + 1].k}</button>`
          : (api.config.liveAvailable ? html`<a class="btn primary" href=${tryHref()}>Try your own</a>` : '')}
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
        <dt>Interpreter</dt><dd class="num">${pv.interpreter || '–'}</dd>
        <dt>Server</dt><dd class="num">${pv.server_version || '–'}</dd>
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
  const [rec, setRec] = useState(null);      // { record, tour } | { error }
  const recordId = C.recordIdFromUrl(location.search);
  useEffect(() => {
    if (recordId) {
      api.getRecord(recordId).then((r) => setRec({ record: r, tour: false }))
        .catch((e) => setRec({ error: e.status === 410 ? 'This shared challenge has expired from the server.' : `Could not load the shared challenge: ${e.message || e}` }));
    } else if (ctx.isStatic) {
      api.getTour().then((r) => setRec({ record: { ...r, __tour: true }, tour: true }))
        .catch(() => setRec({ error: null, none: true }));
    }
  }, [recordId, ctx.isStatic]);
  if (recordId || ctx.isStatic) {
    if (!rec) return html`<div class="sec"><p class="muted">Loading ${recordId ? 'the shared challenge' : 'the recorded tour'}…</p></div>`;
    if (rec.error) return html`<div class="sec"><p class="err">${rec.error}</p></div>`;
    if (rec.none) {
      return html`<div class="sec"><p class="kicker">Challenge</p><h2>Challenges run on the live simulation</h2>
        <p class="lede">This recorded site has no challenge tour. ${api.config.liveAvailable ? 'Switch to the live simulation to build your own.' : 'Run the studio server to build your own.'}</p>
        ${api.config.liveAvailable ? html`<a class="btn primary" href=${tryHref()}>Open the live simulation</a>` : ''}</div>`;
    }
    return html`<${RecordPanel} ctx=${ctx} record=${rec.record} tour=${rec.tour} />`;
  }
  return html`<${ChallengeBuilder} ctx=${ctx} />`;
}
