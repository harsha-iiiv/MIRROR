// MIRROR studio: application shell, data flow and the playback stage.
import { h, render } from 'preact';
import { useState, useEffect, useRef, useMemo, useCallback } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { Viewer } from './viewer.js';
import { fmtT, statusLabel, sceneLabel, latest, cupOutcome, isAux, isBlockedScene } from './util.js';
import { PANELS, STEPS, stepState, correctionVersion, StoryBar, AboutPanel } from './panels.js';
import { VideoPane, ModelsPanel } from './media.js';
import { OverviewPanel } from './overview.js';
import { ChallengePanel } from './challenge.js';
import { CapturePanel } from './capture.js';
import { recordIdFromUrl } from './challenge_state.js';
import { correctionStory } from './story.js';
import { demoSource, videoMediaKind, demoEvidence, tiltStatusAt, isSyntheticDemo, SYNTHETIC_DEMO_TEXT } from './provenance.js';
import { generationId, rolloutCacheKey, isStale, latestOnly, DROPPED } from './identity.js';

const html = htm.bind(h);

// Decide recorded or live before loading any data.
const READY = api.init();

function useLiveStatus() {
  const [s, setS] = useState({ ...api.live });
  useEffect(() => api.onLiveStatus(setS), []);
  return s;
}

// Recorded pages with a configured server get a switch; the choice lives in the URL (?live=1).
function LiveSwitch({ status }) {
  if (!api.config.recordings || !api.config.liveAvailable) return null;
  // On only while the live server is actually in use; if it could not be reached the
  // page shows recordings, the switch reads off, and turning it on tries again.
  const on = api.config.wantLive && !['unavailable', 'busy'].includes(status.state);
  const label = { connecting: 'connecting…', waking: 'waking up…', live: 'on', unavailable: 'unavailable', busy: 'busy', off: 'off' }[status.state] || '';
  return html`<a class=${'live-switch ' + status.state} role="switch" aria-checked=${on ? 'true' : 'false'}
    href=${api.liveHref(!on)} title=${on ? 'Back to the recorded session' : 'Run the studio on the live simulation server'}>
    <span class="knob" aria-hidden="true"></span>Live simulation<span class="state">${label}</span></a>`;
}

function LiveNote({ status, onClose }) {
  const text = status.notice || (['unavailable', 'busy'].includes(status.state) ? status.detail : '');
  if (!text) return null;
  return html`<div class="live-note" role="status"><span>${text}</span>
    ${['unavailable', 'busy'].includes(status.state) ? html`<a class="btn small" href=${api.liveHref(true)}>Try again</a>` : ''}
    <button class="iconbtn" aria-label="Dismiss" onClick=${onClose}>✕</button></div>`;
}

const Icon = {
  play: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>`,
  pause: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>`,
  sun: html`<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" stroke="currentColor" stroke-width="1.4"/></svg>`,
  moon: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12.5 10.2A5.5 5.5 0 0 1 5.8 3.5a5.5 5.5 0 1 0 6.7 6.7z" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>`,
};

function useStudio() {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [jobs, setJobs] = useState({});
  const timer = useRef(null);
  // Only the newest state request is applied: a slow response that arrives after a newer one
  // (for example across a demonstration change) is dropped instead of rolling the page back.
  const getState = useMemo(() => latestOnly(api.getState), []);
  const refresh = useCallback(async () => {
    try {
      const s = await getState();
      if (s === DROPPED) return;
      api.setGeneration(generationId(s));
      setSnap(s); setLoadError(null);
    } catch (e) { setLoadError(String(e.message || e)); }
  }, []);
  useEffect(() => {
    let conn = null, gone = false;
    READY.then(() => {
      if (gone) return;
      refresh();
      conn = api.connect((msg) => {
        if (msg.type === 'state') { clearTimeout(timer.current); timer.current = setTimeout(refresh, 60); }
        if (msg.type === 'job' && !isStale(api.currentGeneration(), msg.job)) setJobs((j) => ({ ...j, [msg.job.id]: msg.job }));
      });
    }, (e) => setError(String(e.message || e)));
    const off = api.onSessionReset(() => { setJobs({}); refresh(); });
    const offStale = api.onStaleGeneration(() => { setJobs({}); refresh(); });
    return () => { gone = true; off(); offStale(); conn && conn.close(); };
  }, [refresh]);
  // A failed state load is shown until a load succeeds; an action's error (for example a stale refusal)
  // stays until the next action, so the refresh that a refusal triggers cannot wipe its explanation.
  return { snap, error: error || loadError, jobs, setJobs, refresh, setError };
}

function App() {
  const { snap, error, jobs, setJobs, refresh, setError } = useStudio();
  const [step, setStep] = useState(null);
  const [pick, setPick] = useState(null);        // user-chosen rollout id, cleared on step change
  const [theme, setTheme] = useState(document.documentElement.dataset.theme || 'light');
  const [busy, setBusy] = useState({});          // action key -> job
  const [tool, setTool] = useState(null);        // scene edit tool: 'drag' | 'obstacle'
  const [opts, setOpts] = useState({ labels: true, trace: true, ghost: true });
  const [story, setStory] = useState(localStorage.getItem('mirror-story') !== 'off');
  const [about, setAbout] = useState(false);
  const [models, setModels] = useState(false);
  const liveStatus = useLiveStatus();
  const [noteHidden, setNoteHidden] = useState(false);
  const toggleStory = (on) => { localStorage.setItem('mirror-story', on ? 'on' : 'off'); setStory(on); };
  const [stageOverride, setStageOverride] = useState(null);   // challenge mode drives the stage itself
  const editHandler = useRef(null);
  const viewerRef = useRef(null);

  // Start with the reference demonstration loaded.
  const autoloaded = useRef(false);
  useEffect(() => {
    if (!snap || api.isStatic || snap.demo || autoloaded.current) return;
    autoloaded.current = true;
    action('demo', () => api.post('demo/load'));
  }, [snap]);

  // Choose a sensible starting step once data arrives: a ?step= link, else the story
  // overview when the session holds a correction, else where the session left off.
  useEffect(() => {
    if (!snap || step) return;
    const asked = new URLSearchParams(location.search).get('step');
    if (recordIdFromUrl(location.search)) { setStep('challenge'); return; }
    if (asked === 'test') { setStep('challenge'); return; }
    if (asked === 'capture' || new URLSearchParams(location.search).has('capture')) { setStep('capture'); return; }
    if (asked && (asked === 'overview' || asked === 'challenge' || STEPS.some((s) => s.key === asked))) { setStep(asked); return; }
    if (correctionStory(snap)) { setStep('overview'); return; }
    if (api.isStatic) { setStep('watch'); return; }
    setStep(snap.evaluation ? 'evidence' : snap.specs.length ? 'interpret' : 'watch');
  }, [snap]);

  const action = useCallback(async (key, fn) => {
    try {
      setError(null);
      // Busy from the click, not from the server's reply: controls that depend on this action
      // (for example everything that needs the demonstration being loaded) stay disabled meanwhile.
      setBusy((b) => ({ ...b, [key]: { status: 'starting', progress: 0, message: 'Starting' } }));
      const res = await fn();
      if (res && res.id && res.status) {           // a background job
        setBusy((b) => ({ ...b, [key]: res }));
        const done = await api.waitJob(res, (j) => setBusy((b) => ({ ...b, [key]: j })));
        setBusy((b) => { const n = { ...b }; delete n[key]; return n; });
        await refresh();
        return done.result;
      }
      await refresh();
      setBusy((b) => { const n = { ...b }; delete n[key]; return n; });
      return res;
    } catch (e) {
      setBusy((b) => { const n = { ...b }; delete n[key]; return n; });
      if (e.stale) { await refresh(); return null; }   // late result from an earlier demonstration: not shown
      setError(String(e.message || e));
      await refresh();
      return null;
    }
  }, [refresh]);

  // Live job progress from the websocket overrides polling snapshots.
  const busyLive = useMemo(() => {
    const out = {};
    for (const [k, j] of Object.entries(busy)) out[k] = jobs[j.id] && jobs[j.id].progress >= j.progress ? jobs[j.id] : j;
    return out;
  }, [busy, jobs]);

  const toggleTheme = () => {
    const t = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    localStorage.setItem('mirror-theme', t);
    setTheme(t);
    viewerRef.current?.applyTheme();
  };

  if (!snap) {
    const msg = { connecting: 'Connecting to the live simulation…', waking: 'Live server waking up… this can take up to a minute.' }[liveStatus.state];
    return html`<div class="boot">${error ? html`<span class="err">Could not load the studio: ${error}</span>` : (msg || 'Loading studio…')}
      ${!error && liveStatus.state === 'waking' && api.config.recordings ? html`<div style="margin-top:10px"><a class="btn small" href=${api.liveHref(false)}>Watch the recordings instead</a></div>` : ''}</div>`;
  }

  const cur = step || 'watch';
  if (api.isStatic && snap) api.prefetch(prefetchFor(snap, cur), (id) => rolloutCacheKey(snap, id));
  const sstate = stepState(snap);
  const display = (cur === 'challenge' && stageOverride) || chooseDisplay(snap, cur === 'capture' ? 'watch' : cur, pick, tool);
  const ctx = {
    snap, step: cur, setStep: (s) => { setStep(s); setPick(null); setTool(null); if (s !== 'challenge') setStageOverride(null); }, pick, setPick, busy: busyLive, action,
    tool, setTool, viewer: viewerRef, display, isStatic: api.isStatic, error, setError, story, toggleStory,
    setStageOverride, setEditHandler: (fn) => { editHandler.current = fn; }, editHandler,
    openChallenge: () => { setStep('challenge'); setPick(null); setTool(null); },
    challengeLive: !api.isStatic, refresh,
    // The live session has no demonstration yet, or the page is loading one: nothing that needs it may run.
    demoSettling: !api.isStatic && !!snap && (!!busy.demo || (!snap.demo && !autoloaded.current)),
  };
  const Panel = cur === 'overview' ? OverviewPanel : cur === 'challenge' ? ChallengePanel : cur === 'capture' ? CapturePanel : PANELS[cur];
  const hasStory = !!correctionStory(snap);

  return html`
    <div class="app">
      <header class="top">
        <div class="brand"><b>MIRROR</b><span>task authoring and correction testing</span></div>
        <nav class="steps" aria-label="Story steps">
          ${hasStory ? html`<button aria-current=${cur === 'overview' ? 'step' : null} class="start" onClick=${() => ctx.setStep('overview')}
            title="What happened, in one screen" aria-label="Overview"><span class="ix" aria-hidden="true">·</span><span class="lbl">Overview</span></button>` : ''}
          ${STEPS.map((s, i) => html`
            <button aria-current=${cur === s.key ? 'step' : null} class=${sstate[s.key]?.done ? 'done' : ''}
              disabled=${!sstate[s.key]?.enabled} onClick=${() => ctx.setStep(s.key)}
              title=${s.title} aria-label=${`Step ${i + 1}: ${s.short}`}><span class="ix" aria-hidden="true">${i + 1}</span><span class="lbl">${s.short}</span></button>`)}
          <button aria-current=${cur === 'challenge' ? 'step' : null} class="challenge" onClick=${() => ctx.openChallenge()}
            title=${api.isStatic ? 'A recorded test, step by step' : 'Change a layout and correct a rule yourself'} aria-label="Test MIRROR"><span class="ix" aria-hidden="true">⚑</span><span class="lbl">Test MIRROR</span></button>
          <button aria-current=${cur === 'capture' ? 'step' : null} class="capture" onClick=${() => ctx.setStep('capture')}
            title="Record the task with real phones (tested on synthetic renders only)" aria-label="Phone capture"><span class="ix" aria-hidden="true">◎</span><span class="lbl">Phone capture</span></button>
        </nav>
        <div class="top-right">
          <${LiveSwitch} status=${liveStatus} />
          ${api.config.recordings && api.config.liveAvailable && snap.mode !== 'fixture' ? ''
            : html`<span class=${'badge ' + (api.isStatic ? '' : snap.mode)}
            title=${modeTitle(snap)}><span class="long">${api.isStatic ? 'recorded session' : snap.mode === 'live' ? 'live simulation' : 'fixture data'}</span><span class="short" aria-hidden="true">${api.isStatic ? 'recorded' : snap.mode === 'live' ? 'live' : 'fixture'}</span></span>`}
          ${!story ? html`<button class="iconbtn" onClick=${() => toggleStory(true)} title="Show the guided story">Story</button>` : ''}
          <button class="iconbtn" aria-expanded=${models} onClick=${() => { setModels(!models); setAbout(false); }}>Models${(snap.models || []).length ? html` <span class="cnt">${snap.models.length}</span>` : ''}</button>
          <button class="iconbtn" aria-expanded=${about} onClick=${() => { setAbout(!about); setModels(false); }}>About</button>
          <button class="iconbtn" onClick=${toggleTheme} aria-label=${'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' theme'}>${theme === 'dark' ? Icon.sun : Icon.moon}</button>
        </div>
      </header>
      <div class="banner" role="status">${isSyntheticDemo(snap) ? html`<div class="synthetic-banner"><b>SYNTHETIC.</b> ${SYNTHETIC_DEMO_TEXT.replace(/^SYNTHETIC test render: /, '')} Everything computed from it inherits that label.</div>` : ''}${snap.mode === 'fixture' ? html`<b>Fixture data.</b> ${(snap.missing_packages || []).length
        ? `The simulator packages are not available (${snap.missing_packages.slice(0, 3).join(', ')}${snap.missing_packages.length > 3 ? ', …' : ''}), so`
        : 'The studio was started on fixtures, so'} motion here is key-framed for interface development. It is not evidence.` : ''}
        ${!noteHidden ? html`<${LiveNote} status=${liveStatus} onClose=${() => setNoteHidden(true)} />` : ''}</div>
      ${story && !['overview', 'challenge', 'capture'].includes(cur) ? html`<${StoryBar} ctx=${ctx} />` : ''}
      ${about ? html`<${AboutPanel} ctx=${ctx} onClose=${() => setAbout(false)} />` : ''}
      ${models ? html`<${ModelsPanel} ctx=${ctx} onClose=${() => setModels(false)} />` : ''}
      <main class=${'main' + (cur === 'capture' ? ' solo' : '')}>
        ${cur === 'capture' ? '' : html`<${Stage} ctx=${ctx} viewerRef=${viewerRef} opts=${opts} setOpts=${setOpts} />`}
        <aside class="panel" aria-label="Step details">
          ${error ? html`<div class="sec"><div class="err" role="alert">${error}</div></div>` : ''}
          <${Panel} ctx=${ctx} />
        </aside>
      </main>
    </div>`;
}

// The rollouts the next steps will show, fetched while the reader looks at this one.
function prefetchFor(snap, step) {
  const rs = snap.rollouts || [];
  const pick = (f) => { const r = latest(rs, f); return r?.id; };
  return {
    watch: [pick((r) => r.tags.includes('run'))],
    interpret: [pick((r) => r.tags.includes('run'))],
    run: [pick((r) => r.tags.includes('world'))],
    world: [pick((r) => r.tags.includes('corrected')), pick((r) => r.tags.includes('world'))],
    correct: [],
    evidence: [],
  }[step] || [];
}

function modeTitle(snap) {
  if (api.isStatic) return 'A recorded session exported from the live studio. Actions replay recorded results.';
  if (snap.mode === 'live') return 'Every rollout is executed in MuJoCo and checked from the physics step log.';
  return 'Key-framed placeholder motion for interface development.';
}

// Which rollout (and ghost) the stage shows for a step.
export function chooseDisplay(snap, step, pick, tool) {
  const rs = snap.rollouts || [];
  const demoScene = snap.demo?.scene_id;
  const sceneId = snap.scene?.scene_id;
  const cv = correctionVersion(snap);
  const res = { kind: 'scene', id: null, ghost: null, title: sceneLabel(sceneId), sub: '' };
  const asRollout = (r, ghost = null, sub = '') => ({ kind: 'rollout', id: r.id, ghost: ghost?.id || null, rollout: r,
    title: `${r.report.condition || r.id} · ${sceneLabel(r.scene_id)}`, sub: sub || `spec v${r.spec_version} · ${statusLabel(r.report)}` });
  if (step === 'overview' && !pick) {
    const st = correctionStory(snap);
    const fixed = st?.fixed && rs.find((x) => x.id === st.fixed.id);
    const failed = st && rs.find((x) => x.id === st.failed.id);
    if (fixed) return asRollout(fixed, failed, `spec v${fixed.spec_version} · after the correction · faint line: before`);
    if (failed) return asRollout(failed);
  }
  if (step === 'overview' && pick) {
    const st = correctionStory(snap);
    const r = rs.find((x) => x.id === pick);
    if (r && st) return asRollout(r, r.id === st.fixed?.id ? rs.find((x) => x.id === st.failed.id) : null,
      r.id === st.failed.id ? `spec v${r.spec_version} · before the correction` : `spec v${r.spec_version} · after the correction · faint line: before`);
  }
  if (pick === 'demo' && snap.demo) return { kind: 'demo', id: 'demo', title: 'Demonstration', sub: sourceText(snap) };
  if (pick) {
    const r = rs.find((x) => x.id === pick);
    if (r) return asRollout(r, ghostFor(snap, r));
  }
  if (step === 'watch' || step === 'interpret') {
    if (snap.demo) return { kind: 'demo', id: 'demo', title: 'Demonstration', sub: sourceText(snap) };
    return res;
  }
  if (tool) return { ...res, sub: 'Editing the layout' };
  if (step === 'run') {
    const r = latest(rs, (x) => x.tags.includes('run') && x.scene_id === demoScene) || latest(rs, (x) => x.scene_id === demoScene && !isAux(x)) || latest(rs, (x) => !isAux(x));
    return r ? asRollout(r) : res;
  }
  if (step === 'world') {
    const r = latest(rs, (x) => x.scene_id === sceneId && x.tags.includes('world')) || latest(rs, (x) => x.scene_id === sceneId && !isAux(x));
    if (r) return asRollout(r);
    return { ...res, sub: sceneId === demoScene ? 'Change the layout, then replan' : 'Not yet run in this layout' };
  }
  if (step === 'correct') {
    const after = cv ? (latest(rs, (x) => x.spec_version >= cv && x.scene_id === sceneId && !isAux(x) && x.has_frames)
      || latest(rs, (x) => x.spec_version >= cv && x.tags.includes('corrected') && x.has_frames)) : null;
    if (after) return asRollout(after, ghostFor(snap, after));
    const before = latest(rs, (x) => x.scene_id === sceneId && !isAux(x)) || latest(rs, (x) => !isAux(x));
    return before ? asRollout(before) : res;
  }
  if (step === 'evidence') {
    if (isBlockedScene(sceneId)) {
      const rb = latest(rs, (x) => x.scene_id === sceneId && !isAux(x));
      if (rb) return asRollout(rb);
    }
    // Feature a fresh layout where the original behaviour breaks the corrected rule.
    const evs = snap.evaluation?.strata ? Object.values(snap.evaluation.strata) : snap.evaluation ? [snap.evaluation] : [];
    for (const ev of evs) {
      if (!ev?.trials || !ev.studio) continue;
      const t = ev.trials.find((x) => x.a?.corrected_violations?.length);
      const ids = t && ev.studio.rollout_ids?.[String(t.seed)];
      const b = ids && rs.find((x) => x.id === ids.b && x.has_frames);
      if (b) return asRollout(b, ghostFor(snap, b), `seed ${t.seed} · corrected v${b.spec_version} · ${statusLabel(b.report)}`);
    }
    const r = latest(rs, (x) => x.scene_id === sceneId && !isAux(x));
    if (r && (isBlockedScene(sceneId) || r.tags.includes('blocked'))) return asRollout(r);
    return res;
  }
  return res;
}

// The most recent earlier behaviour in the same layout under an older spec version.
export function ghostFor(snap, r) {
  if (!r) return null;
  const rs = snap.rollouts || [];
  for (let i = rs.length - 1; i >= 0; i--) {
    const g = rs[i];
    if (g.id === r.id || g.scene_id !== r.scene_id || g.spec_version >= r.spec_version || !g.has_frames) continue;
    if (g.tags.includes('candidate')) continue;
    if (g.tags.includes('eval') !== r.tags.includes('eval')) continue;
    return g;
  }
  return null;
}

function sourceText(snap) { return demoSource(snap).short; }

function Stage({ ctx, viewerRef, opts, setOpts }) {
  const el = useRef(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [dur, setDur] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [payload, setPayload] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [loading, setLoading] = useState(null);
  useEffect(() => api.onRolloutProgress((id, got, total) => {
    setLoading(got < 0 ? null : { id, got, total });
  }), []);
  const { snap, display, tool } = ctx;

  useEffect(() => {
    const v = new Viewer(el.current, {
      onTick: (tt, p) => { setT(tt); setPlaying(p); },
      onEdit: (e) => onEdit(e),
    });
    viewerRef.current = v;
    window.__mirrorViewer = v;
    return () => { v.dispose(); if (viewerRef.current === v) viewerRef.current = null; if (window.__mirrorViewer === v) window.__mirrorViewer = null; };
  }, []);

  const editRef = useRef(null);
  editRef.current = ctx;
  function onEdit(e) {
    const c = editRef.current;
    if (c.editHandler?.current) { c.editHandler.current(e); return; }
    let edit = null;
    if (e.kind === 'tray') edit = { tray: { x: e.x, y: e.y } };
    else if (e.kind === 'object') edit = { object: { id: e.id, x: e.x, y: e.y } };
    else if (e.kind === 'obstacle') edit = { obstacle: { x: e.x, y: e.y } };
    if (edit) c.action('scene', () => api.post('scene', { edit }));
  }

  useEffect(() => { viewerRef.current?.setEditMode(tool); }, [tool]);
  // Follow the cup on the overview and on narrow screens, until the visitor picks a view.
  const userView = useRef(false);
  useEffect(() => {
    const vw = viewerRef.current;
    if (!vw || userView.current) return;
    const narrow = (el.current?.clientWidth || 1000) < 560;
    const want = display.kind === 'rollout' && (ctx.step === 'overview' || narrow) ? 'follow' : '3d';
    if (vw.view !== want) { vw.setView(want); setOpts((o) => ({ ...o })); }
  }, [ctx.step, display.kind]);
  useEffect(() => { viewerRef.current?.setOptions(opts); }, [opts]);
  useEffect(() => { if (viewerRef.current) viewerRef.current.speed = speed; }, [speed]);

  // Load what the stage should show.
  // Keys combine the generation with immutable artifact ids and hashes, never bare run numbers.
  const gen = generationId(snap);
  const k1 = display.kind === 'scene' ? '' : display.cacheKey || rolloutCacheKey(snap, display.id);
  const k2 = display.ghost ? display.ghostKey || rolloutCacheKey(snap, display.ghost) : '';
  const key = display.kind === 'scene' ? `scene:${gen}:${snap.scene?.scene_id}:${snap.revision}` : `${k1}||${k2}`;
  useEffect(() => {
    const v = viewerRef.current;
    if (!v) return;
    let cancelled = false;
    setLoadErr(null);
    if (display.kind === 'scene') {
      setPayload(null);
      v.showScene(snap.scene_visual, snap.scene);
      setDur(0); setT(0);
      return;
    }
    (async () => {
      try {
        // Shared records and the tour bring their own loader (their runs are stored with the record).
        const load = display.load || ((id, key) => api.getRollout(id, key, gen));
        const p = await load(display.id, k1);
        const g = display.ghost ? await load(display.ghost, k2) : null;
        if (cancelled) return;
        if (!p.t || !p.t.length) {
          setPayload(p);
          v.showScene(p.visual || snap.scene_visual, p.scene || snap.scene);
          setDur(0); setT(0);
          return;
        }
        const ev = display.kind === 'demo' ? demoEvidence(snap) : null;
        const hz = snap.demo?.sample_hz || 0;
        const tiltStatus = ev && hz && p.t ? (i) => tiltStatusAt(ev, v.traceObject || '', Math.round(p.t[i] * hz)) : null;
        v.loadRollout(p, { ghost: g, sceneState: p.scene || (display.kind === 'demo' ? snap.demo?.scene : null), tiltStatus });
        setPayload(p);
        setDur(v.duration());
        v.setTime(0);
        if (!matchMedia('(prefers-reduced-motion: reduce)').matches) v.play();
      } catch (e) { if (!cancelled && !e.stale) setLoadErr(String(e.message || e)); }
    })();
    return () => { cancelled = true; };
  }, [key]);

  useEffect(() => {
    const onKey = (e) => {
      const a = document.activeElement;
      const onViewer = a === el.current;
      if (a && a !== document.body && !onViewer) return;   // buttons, inputs and the scrubber keep their own keys
      const v = viewerRef.current;
      if (!v || !v.payload) return;
      if (e.code === 'Space') { e.preventDefault(); v.toggle(); }
      else if (onViewer && (e.code === 'ArrowRight' || e.code === 'ArrowLeft')) {
        e.preventDefault(); v.pause(); v.setTime(v.t + (e.code === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 5 : 1));
      } else if (onViewer && e.code === 'Home') { e.preventDefault(); v.setTime(0); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const events = useMemo(() => stageEvents(display, snap, payload), [payload, display.id]);
  const viol = (display.rollout?.report?.violations || []);
  const report = display.rollout?.report;
  const cup = cupOutcome(report);
  const v = viewerRef.current;
  const ghostOn = !!display.ghost;

  return html`
    <section class="stage" aria-label="Simulation playback">
      <div class="viewer" ref=${el} tabindex="0" aria-label="3D view. Drag to orbit, scroll to zoom. Space plays or pauses; arrow keys step one second.">
        <div class="v-head">
          ${display.title ? html`<div class="v-title"><div class="t1">${display.title}</div><div class="t2">${display.sub}</div></div>` : html`<span></span>`}
          <div class="v-tools">
            ${['follow', '3d', 'top', 'side'].map((k) => (k === 'follow' && display.kind === 'scene') ? '' : html`<button class="iconbtn" aria-pressed=${v?.view === k}
              title=${k === 'follow' ? 'Camera follows the cup' : ''}
              onClick=${() => { userView.current = true; viewerRef.current.setView(k); setOpts({ ...opts }); }}>${{ follow: 'Follow', '3d': '3D', top: 'Top', side: 'Side' }[k]}</button>`)}
            <button class="iconbtn" aria-pressed=${opts.trace} onClick=${() => setOpts({ ...opts, trace: !opts.trace })}>Path</button>
            ${ghostOn ? html`<button class="iconbtn" aria-pressed=${opts.ghost} onClick=${() => setOpts({ ...opts, ghost: !opts.ghost })}>Previous</button>` : ''}
            <button class="iconbtn" aria-pressed=${opts.labels} onClick=${() => setOpts({ ...opts, labels: !opts.labels })}>Labels</button>
          </div>
        </div>
        ${display.kind === 'demo' && snap.video_demo && snap.demo && snap.video_demo.trace_source === snap.demo.source
          ? html`<${VideoPane} vd=${snap.video_demo} t=${t} kind=${videoMediaKind(snap)} />` : ''}
        ${display.kind !== 'scene' && payload && !payload.t?.length ? html`<div class="v-empty"><span class="v-note">${report?.plan_status === 'no_plan' ? 'No plan: nothing was executed.' : 'Nothing was executed.'}</span></div>` : ''}
        ${loadErr ? html`<div class="v-empty err">${loadErr}</div>` : ''}
        ${loading ? html`<div class="v-loading" role="status">Loading recorded rollout ${loading.total ? `${Math.round((100 * loading.got) / loading.total)}%` : `${(loading.got / 1e6).toFixed(1)} MB`}
          <div class="progress"><div style=${`width:${loading.total ? (100 * loading.got) / loading.total : 30}%`}></div></div></div>` : ''}
        ${payload?.t?.length ? html`<div class="v-legend">
          <span><i class="grad"></i><span class="long">container path by tilt: within ${15}° → over</span><span class="short">cup path by tilt</span></span>
          ${viewerRef.current?.hasUnobservedTilt ? html`<span><i style="background:var(--faint)"></i>tilt not observed</span>` : ''}
          ${ghostOn && opts.ghost ? html`<span><i style="background:var(--faint)"></i><span class="long">${display.ghostLabel || ghostText(snap, display.ghost)}</span><span class="short">${display.ghostLabel || `before (v${(snap.rollouts || []).find((r) => r.id === display.ghost)?.spec_version ?? '?'})`}</span></span>` : ''}
          ${payload.beads_hidden ? html`<span>beads not recorded in demo trace</span>`
            : html`<span><i style="background:#ff2bd6;border-radius:50%;width:8px;height:8px"></i><span class="long">beads, drawn enlarged at their true positions</span><span class="short">beads (enlarged)</span>${(() => {
              const off = t >= 0 ? viewerRef.current?.beadsOffTable?.() || 0 : 0;
              return off ? html` · <b class="bad-t">${off} fell off the table</b>` : '';
            })()}</span>`}
        </div>` : ''}
        ${tool ? html`<div class="v-hint">${tool === 'obstacle' ? 'Click the table to place a box' : 'Drag the tray or an object'}</div>` : ''}
      </div>
      <div class="transport">
        <button class="iconbtn play" onClick=${() => v?.toggle()} disabled=${!dur} aria-label=${playing ? 'Pause' : 'Play'}>${playing ? Icon.pause : Icon.play}</button>
        <span class="time">${fmtT(t)} / ${fmtT(dur)}</span>
        <div class="scrub">
          <input type="range" min="0" max=${dur || 1} step="0.01" value=${t} disabled=${!dur} aria-label="Playback position"
            aria-valuetext=${fmtT(t)}
            onInput=${(e) => { v?.pause(); v?.setTime(parseFloat(e.target.value)); }} />
          <div class="track"></div>
          <div class="fill" style=${`width:${dur ? (100 * t) / dur : 0}%`}></div>
          ${dur ? viol.map((x) => html`<div class="viol" title=${`${x.constraint_id}: peak ${x.peak.toFixed(1)}`}
            style=${`left:${(100 * x.t_start) / dur}%;width:${Math.max(0.4, (100 * (x.t_end - x.t_start)) / dur)}%`}></div>`) : ''}
          ${dur ? events.map((e) => html`<div class=${'ev ' + e.type} style=${`left:${(100 * e.t) / dur}%`} title=${`${e.type} ${e.object_id || ''} at ${fmtT(e.t)}`}></div>`) : ''}
        </div>
        <span class="muted num beads" style="font-size:12px">${cup ? `beads ${cup.beads_in_container}/${cup.beads_total}` : ''}</span>
        <select class="speed" value=${speed} onChange=${(e) => setSpeed(parseFloat(e.target.value))} aria-label="Playback speed">
          ${[0.5, 1, 2, 4].map((s) => html`<option value=${s}>${s}×</option>`)}
        </select>
      </div>
    </section>`;
}

function ghostText(snap, id) {
  const g = (snap.rollouts || []).find((r) => r.id === id);
  if (!g) return 'previous behaviour';
  const c = cupOutcome(g.report);
  return `previous behaviour (v${g.spec_version})` + (c ? ` · cup ${c.max_transport_tilt_deg.toFixed(0)}° · beads ${c.beads_in_container}/${c.beads_total}` : '');
}

function stageEvents(display, snap, payload) {
  if (display.kind === 'demo') return snap.demo?.events || [];
  if (!payload?.phase) return [];
  const out = [];
  let prev = '';
  payload.phase.forEach((ph, i) => {
    const [kind, obj] = (ph || '').split(':');
    if ((kind === 'grasp' || kind === 'release') && ph !== prev) out.push({ type: kind, object_id: obj, t: payload.t[i] });
    prev = ph;
  });
  return out;
}

render(html`<${App} />`, document.getElementById('app'));
