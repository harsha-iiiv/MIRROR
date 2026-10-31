// MIRROR studio: application shell, data flow and the playback stage.
import { h, render } from 'preact';
import { useState, useEffect, useRef, useMemo, useCallback } from 'preact/hooks';
import htm from 'htm';
import * as api from './api.js';
import { Viewer } from './viewer.js';
import { fmtT, statusLabel, sceneLabel, latest, cupOutcome, isAux, isBlockedScene } from './util.js';
import { PANELS, STEPS, stepState, correctionVersion, StoryBar, AboutPanel } from './panels.js';

const html = htm.bind(h);

const Icon = {
  play: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>`,
  pause: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>`,
  sun: html`<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" stroke="currentColor" stroke-width="1.4"/></svg>`,
  moon: html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12.5 10.2A5.5 5.5 0 0 1 5.8 3.5a5.5 5.5 0 1 0 6.7 6.7z" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>`,
};

function useStudio() {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState(null);
  const [jobs, setJobs] = useState({});
  const timer = useRef(null);
  const refresh = useCallback(async () => {
    try { setSnap(await api.getState()); setError(null); } catch (e) { setError(String(e.message || e)); }
  }, []);
  useEffect(() => {
    refresh();
    const conn = api.connect((msg) => {
      if (msg.type === 'state') { clearTimeout(timer.current); timer.current = setTimeout(refresh, 60); }
      if (msg.type === 'job') setJobs((j) => ({ ...j, [msg.job.id]: msg.job }));
    });
    return () => conn.close();
  }, [refresh]);
  return { snap, error, jobs, setJobs, refresh, setError };
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
  const toggleStory = (on) => { localStorage.setItem('mirror-story', on ? 'on' : 'off'); setStory(on); };
  const viewerRef = useRef(null);

  // Start with the reference demonstration loaded.
  const autoloaded = useRef(false);
  useEffect(() => {
    if (!snap || api.isStatic || snap.demo || autoloaded.current) return;
    autoloaded.current = true;
    action('demo', () => api.post('demo/load'));
  }, [snap]);

  // Choose a sensible starting step once data arrives.
  useEffect(() => {
    if (!snap || step) return;
    if (api.isStatic) { setStep('watch'); return; }
    setStep(snap.evaluation ? 'evidence' : snap.specs.length ? 'interpret' : 'watch');
  }, [snap]);

  const action = useCallback(async (key, fn) => {
    try {
      setError(null);
      const res = await fn();
      if (res && res.id && res.status) {           // a background job
        setBusy((b) => ({ ...b, [key]: res }));
        const done = await api.waitJob(res, (j) => setBusy((b) => ({ ...b, [key]: j })));
        setBusy((b) => { const n = { ...b }; delete n[key]; return n; });
        await refresh();
        return done.result;
      }
      await refresh();
      return res;
    } catch (e) {
      setBusy((b) => { const n = { ...b }; delete n[key]; return n; });
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
    return html`<div class="boot">${error ? html`<span class="err">Could not load the studio: ${error}</span>` : 'Loading studio…'}</div>`;
  }

  const cur = step || 'watch';
  if (api.isStatic && snap) api.prefetch(prefetchFor(snap, cur));
  const sstate = stepState(snap);
  const display = chooseDisplay(snap, cur, pick, tool);
  const ctx = {
    snap, step: cur, setStep: (s) => { setStep(s); setPick(null); setTool(null); }, pick, setPick, busy: busyLive, action,
    tool, setTool, viewer: viewerRef, display, isStatic: api.isStatic, error, setError, story, toggleStory,
  };
  const Panel = PANELS[cur];

  return html`
    <div class="app">
      <header class="top">
        <div class="brand"><b>MIRROR</b><span>task authoring and correction testing</span></div>
        <nav class="steps" aria-label="Story steps">
          ${STEPS.map((s, i) => html`
            <button aria-current=${cur === s.key ? 'step' : null} class=${sstate[s.key]?.done ? 'done' : ''}
              disabled=${!sstate[s.key]?.enabled} onClick=${() => ctx.setStep(s.key)}
              title=${s.title} aria-label=${`Step ${i + 1}: ${s.short}`}><span class="ix" aria-hidden="true">${i + 1}</span>${s.short}</button>`)}
        </nav>
        <div class="top-right">
          <span class=${'badge ' + (api.isStatic ? '' : snap.mode)}
            title=${modeTitle(snap)}>${api.isStatic ? 'recorded session' : snap.mode === 'live' ? 'live simulation' : 'fixture data'}</span>
          ${!story ? html`<button class="iconbtn" onClick=${() => toggleStory(true)} title="Show the guided story">Story</button>` : ''}
          <button class="iconbtn" aria-expanded=${about} onClick=${() => setAbout(!about)}>About</button>
          <button class="iconbtn" onClick=${toggleTheme} aria-label=${'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' theme'}>${theme === 'dark' ? Icon.sun : Icon.moon}</button>
        </div>
      </header>
      <div class="banner" role="status">${snap.mode === 'fixture' ? html`<b>Fixture data.</b> ${(snap.missing_packages || []).length
        ? `The simulator packages are not available (${snap.missing_packages.slice(0, 3).join(', ')}${snap.missing_packages.length > 3 ? ', …' : ''}), so`
        : 'The studio was started on fixtures, so'} motion here is key-framed for interface development. It is not evidence.` : ''}</div>
      ${story ? html`<${StoryBar} ctx=${ctx} />` : ''}
      ${about ? html`<${AboutPanel} ctx=${ctx} onClose=${() => setAbout(false)} />` : ''}
      <main class="main">
        <${Stage} ctx=${ctx} viewerRef=${viewerRef} opts=${opts} setOpts=${setOpts} />
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
  if (pick === 'demo' && snap.demo) return { kind: 'demo', id: 'demo', title: 'Demonstration', sub: snap.demo.source_note || snap.demo.source };
  if (pick) {
    const r = rs.find((x) => x.id === pick);
    if (r) return asRollout(r, ghostFor(snap, r));
  }
  if (step === 'watch' || step === 'interpret') {
    if (snap.demo) return { kind: 'demo', id: 'demo', title: 'Demonstration', sub: sourceText(snap.demo) };
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

function sourceText(demo) {
  const m = { teleop_scripted: 'Scripted teleoperation in simulation', teleop_keyboard: 'Keyboard teleoperation in simulation',
    replay: 'Replayed trace', video_grounded: 'Video grounded to simulator objects' };
  return m[demo.source] || demo.source;
}

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
    return () => v.dispose();
  }, []);

  const editRef = useRef(null);
  editRef.current = ctx;
  function onEdit(e) {
    const c = editRef.current;
    let edit = null;
    if (e.kind === 'tray') edit = { tray: { x: e.x, y: e.y } };
    else if (e.kind === 'object') edit = { object: { id: e.id, x: e.x, y: e.y } };
    else if (e.kind === 'obstacle') edit = { obstacle: { x: e.x, y: e.y } };
    if (edit) c.action('scene', () => api.post('scene', { edit }));
  }

  useEffect(() => { viewerRef.current?.setEditMode(tool); }, [tool]);
  useEffect(() => { viewerRef.current?.setOptions(opts); }, [opts]);
  useEffect(() => { if (viewerRef.current) viewerRef.current.speed = speed; }, [speed]);

  // Load what the stage should show.
  const key = display.kind === 'scene' ? `scene:${snap.scene?.scene_id}:${snap.revision}`
    : display.kind === 'demo' ? `demo:${snap.demo?.demo_id}:${snap.demo?.duration}` : `${display.id}|${display.ghost || ''}`;
  const lastDemo = useRef(null);
  if (display.kind === 'demo' && lastDemo.current && lastDemo.current !== key) api.forget('demo');
  if (display.kind === 'demo') lastDemo.current = key;
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
        const p = await api.getRollout(display.id);
        const g = display.ghost ? await api.getRollout(display.ghost) : null;
        if (cancelled) return;
        if (!p.t || !p.t.length) {
          setPayload(p);
          v.showScene(p.visual || snap.scene_visual, p.scene || snap.scene);
          setDur(0); setT(0);
          return;
        }
        v.loadRollout(p, { ghost: g, sceneState: p.scene || (display.kind === 'demo' ? snap.demo?.scene : null) });
        setPayload(p);
        setDur(v.duration());
        v.setTime(0);
        if (!matchMedia('(prefers-reduced-motion: reduce)').matches) v.play();
      } catch (e) { if (!cancelled) setLoadErr(String(e.message || e)); }
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
            ${['3d', 'top', 'side'].map((k) => html`<button class="iconbtn" aria-pressed=${v?.view === k}
              onClick=${() => { viewerRef.current.setView(k); setOpts({ ...opts }); }}>${k === '3d' ? '3D' : k === 'top' ? 'Top' : 'Side'}</button>`)}
            <button class="iconbtn" aria-pressed=${opts.trace} onClick=${() => setOpts({ ...opts, trace: !opts.trace })}>Path</button>
            ${ghostOn ? html`<button class="iconbtn" aria-pressed=${opts.ghost} onClick=${() => setOpts({ ...opts, ghost: !opts.ghost })}>Previous</button>` : ''}
            <button class="iconbtn" aria-pressed=${opts.labels} onClick=${() => setOpts({ ...opts, labels: !opts.labels })}>Labels</button>
          </div>
        </div>
        ${display.kind !== 'scene' && payload && !payload.t?.length ? html`<div class="v-empty"><span class="v-note">${report?.plan_status === 'no_plan' ? 'No plan: nothing was executed.' : 'Nothing was executed.'}</span></div>` : ''}
        ${loadErr ? html`<div class="v-empty err">${loadErr}</div>` : ''}
        ${loading ? html`<div class="v-loading" role="status">Loading recorded rollout ${loading.total ? `${Math.round((100 * loading.got) / loading.total)}%` : `${(loading.got / 1e6).toFixed(1)} MB`}
          <div class="progress"><div style=${`width:${loading.total ? (100 * loading.got) / loading.total : 30}%`}></div></div></div>` : ''}
        ${payload?.t?.length ? html`<div class="v-legend">
          <span><i class="grad"></i>container path by tilt: within ${15}° → over</span>
          ${ghostOn && opts.ghost ? html`<span><i style="background:var(--faint)"></i>${ghostText(snap, display.ghost)}</span>` : ''}
          ${payload.beads_hidden ? html`<span>beads not recorded in demo trace</span>` : ''}
        </div>` : ''}
        ${tool ? html`<div class="v-hint">${tool === 'obstacle' ? 'Click the table to place a box' : 'Drag the tray or an object'}</div>` : ''}
      </div>
      <div class="transport">
        <button class="iconbtn" onClick=${() => v?.toggle()} disabled=${!dur} aria-label=${playing ? 'Pause' : 'Play'}>${playing ? Icon.pause : Icon.play}</button>
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
        <span class="muted num" style="font-size:12px">${cup ? `beads ${cup.beads_in_container}/${cup.beads_total}` : ''}</span>
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
