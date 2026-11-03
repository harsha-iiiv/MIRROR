// First visit: the whole correction story on one screen, details behind disclosure.
import { h } from 'preact';
import htm from 'htm';
import * as api from './api.js';
import { fmtNum, fmtT, sceneLabel, humanRoute, cap } from './util.js';
import { correctionStory, provenanceLine } from './story.js';

const html = htm.bind(h);

const beads = (b) => (b ? `${b.kept} of ${b.total}` : '–');

export function OverviewPanel({ ctx }) {
  const { snap } = ctx;
  const st = correctionStory(snap);
  const prov = provenanceLine(snap, { isStatic: ctx.isStatic });
  if (!st) {
    return html`<div class="sec">
      <p class="kicker">Start here</p>
      <h2>Teach by showing, then correct one rule</h2>
      <p class="lede">This session does not contain a correction yet. Follow the steps: watch the demonstration, check what MIRROR inferred, run it, change the layout and correct what it misunderstood.</p>
      <div class="row"><button class="btn primary" onClick=${() => ctx.setStep('watch')}>Start with the demonstration</button></div>
      <p class="prov-line"><span class=${'src ' + prov.tag}>${prov.tag}</span> ${prov.text}</p>
    </div>`;
  }
  const f = st.failed, x = st.fixed;
  const spilled = f.beads ? f.beads.total - f.beads.kept : 0;
  const scopeText = st.scope.map((c) => c.text).join('; ');
  const show = (id) => ctx.setPick(id);
  const showing = ctx.display?.id;
  return html`
    <div class="sec ov">
      <p class="kicker">What happened</p>
      <h2>One correction changed the plan</h2>
      <ol class="ov-steps">
        <li>
          <p class="ov-k">What went wrong</p>
          <p>In the ${sceneLabel(f.scene_id).toLowerCase()}, the robot carried the cup <b>${humanRoute(f.cup_route) || 'on a new route'}</b>${f.tilt != null && !/°/.test(humanRoute(f.cup_route)) ? html`, tilted up to <b class="num bad-t">${fmtNum(f.tilt, 0)}°</b>` : ''}.${' '}${f.beads ? html`<b class="bad-t">${spilled} of ${f.beads.total} beads</b> spilled.` : ''}</p>
          ${f.status === 'success' ? html`<p class="muted">Under the rules it had then (v${f.spec_version}), that run passed: nothing said the cup had to stay upright while carried.</p>` : ''}
        </li>
        <li>
          <p class="ov-k">What the person changed</p>
          <blockquote class="quote ov-quote">“${st.text}”</blockquote>
          <p>${st.spoken ? html`Spoken, transcribed by ${st.spoken.model} (${st.spoken.source}).${' '}` : ''}${st.compiledBy ? `Compiled by ${st.compiledBy} into` : 'Compiled into'} spec v${st.version}: <b>${scopeText || 'a scoped rule change'}</b>.</p>
          ${st.spoken?.audio_file && ctx.isStatic ? html`<audio class="ov-audio" controls preload="none" src=${api.mediaUrl(st.spoken.audio_file)} aria-label="The spoken correction"></audio>` : ''}
        </li>
        <li>
          <p class="ov-k">The old run, checked again</p>
          <p>Same physics log, new rule: run ${f.id} <b class="bad-t">fails v${st.version}</b>${st.recheck.peak != null ? html`, tilt peak <span class="num">${fmtNum(st.recheck.peak, 0)}°</span> from ${fmtT(st.recheck.t_start)} to ${fmtT(st.recheck.t_end)}` : ''}.</p>
        </li>
        <li>
          <p class="ov-k">What changed in execution</p>
          ${x ? html`<p>The planner chose a different route: cup <b>${humanRoute(x.cup_route) || 'rerouted'}</b>${x.tilt != null ? html`, at most <b class="num">${fmtNum(x.tilt, 0)}°</b>` : ''}${x.beads ? html`, <b class="ok-t">${beads(x.beads)} beads kept</b>` : ''}${x.violations ? html`, <b class="bad-t">${x.violations} violation(s)</b>` : ''}.</p>
            ${st.unchanged.length ? html`<p class="muted">${st.unchanged.map((u, i) => html`${i ? ' ' : ''}${cap(u.kind)}: still ${humanRoute(u.route).replace(/,\s*upright$/, '')}.`)} The rule applies to the cup only.</p>` : ''}`
          : html`<p class="muted">No corrected run was recorded in this layout yet.</p>`}
        </li>
      </ol>
      <div class="seg ov-seg" role="group" aria-label="Which run the stage shows">
        <button aria-pressed=${showing === f.id} onClick=${() => show(f.id)}>Before · v${f.spec_version}</button>
        ${x ? html`<button aria-pressed=${showing === x.id} onClick=${() => show(x.id)}>After · v${x.spec_version}</button>` : ''}
      </div>
      <p class="prov-line"><span class=${'src ' + prov.tag}>${prov.tag}</span> ${prov.text}</p>
    </div>
    <div class="sec">
      <p class="kicker">Try next</p>
      <div class="ov-next">
        <button class="btn primary" onClick=${() => ctx.setStep('watch')}>Walk through it step by step</button>
        ${snap.evaluation ? html`<button class="btn" onClick=${() => ctx.setStep('evidence')}>Layouts it never saw</button>` : ''}
        ${ctx.openChallenge ? html`<button class="btn" onClick=${() => ctx.openChallenge()}>${ctx.challengeLive ? 'Try your own challenge' : 'Challenge tour (recorded)'}</button>` : ''}
      </div>
      <details class="ov-details">
        <summary>Technical detail</summary>
        <dl class="kv">
          <dt>Failed run</dt><dd class="num">${f.id} · spec v${f.spec_version} · ${f.scene_id}</dd>
          <dt>Cup route</dt><dd class="num">${f.cup_route || '–'} → ${x?.cup_route || '–'}</dd>
          ${x ? html`<dt>Corrected run</dt><dd class="num">${x.id} · spec v${x.spec_version}</dd>` : ''}
          <dt>Rule</dt><dd class="num">${st.scope.map((c) => `${c.id} (${c.status}, ${c.phase})`).join(', ') || '–'}</dd>
          <dt>Re-check</dt><dd class="num">${st.recheck.constraint_id || '–'} · ${st.recheck.status}</dd>
          ${snap.export_id ? html`<dt>Export</dt><dd class="num">${snap.export_id}</dd>` : ''}
        </dl>
        <p class="faint">Tilt and beads are measured by the checker from each run's physics log, not reported by a model.</p>
      </details>
    </div>`;
}
