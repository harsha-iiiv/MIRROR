// Small shared pieces: per-step provenance chips and planning-outcome callouts.
import { h } from 'preact';
import htm from 'htm';
import { stepProvenance, planningOutcome } from './outcomes.js';

const html = htm.bind(h);

// Where one result came from: fresh inference, recorded output, offline rules, new simulation or
// recorded playback, with the model or interpreter, request status, latency and fallback reason.
export function ProvChip({ sp, legacy = null, recordedView = false, expectFresh = false }) {
  const p = stepProvenance(sp, legacy, { recordedView });
  if (!p) return html`<span class="pchip unknown" title="The server did not say where this came from">provenance not reported</span>`;
  const notNow = expectFresh && ['recorded_model', 'recorded_playback'].includes(p.kind);
  return html`<span class="pwrap"><span class=${'pchip ' + p.tone} title=${p.recordedFrom ? `recorded from: ${p.recordedFrom}` : ''}>${p.label}</span>${p.detail ? html`<span class="pdetail num">${p.detail}</span>` : ''}
    ${p.fallback && p.kind === 'offline_fallback' ? html`<span class=${'pfallback' + (p.fellBack ? ' warn' : '')}>${p.fellBack ? 'Fallback: ' : ''}${p.fallback}</span>` : ''}
    ${notNow ? html`<span class="pfallback warn">Not computed now: this is a recorded result.</span>` : ''}</span>`;
}

// A blocked plan, worded by what is actually known.
export function PlanOutcome({ plan = null, report = null, outcome = null, compact = false }) {
  const o = planningOutcome({ plan, report, outcome });
  if (o.kind === 'planned') return null;
  return html`<div class=${'callout plan-outcome ' + o.kind}>
    <b>${o.headline}.</b>
    ${o.detail ? html`<div style="margin-top:4px">${o.detail}</div>` : ''}
    ${!compact && o.assumptions.length ? html`<div class="muted" style="margin-top:6px">Assumes: ${o.assumptions.join('; ')}.</div>` : ''}
    ${!compact && o.not_covered.length ? html`<div class="muted" style="margin-top:2px">Not covered: ${o.not_covered.join('; ')}.</div>` : ''}
    ${o.kind === 'no_route_found' ? html`<div class="muted" style="margin-top:4px">Not a safety guarantee either way: the robot did not move.</div>` : ''}
  </div>`;
}

const VERDICT = { satisfied: 'held', violated: 'broken', unknown: 'unknown', not_applicable: 'not applicable', not_evaluated: 'not checked' };
// Per-rule verdicts from the independent checker (enforced rules first).
export function RuleResults({ rr }) {
  if (!rr || !rr.length) return null;
  const rows = [...rr].sort((a, b) => (b.enforced - a.enforced));
  return html`<details class="ov-details"><summary>Per-rule verdicts (${rows.filter((x) => x.enforced).length} enforced)</summary>
    <ul class="rr">${rows.map((x) => html`<li><span class=${'v ' + x.verdict}>${VERDICT[x.verdict] || x.verdict}</span>
      <span class="num">${x.rule_id}</span><span class="muted">${x.enforced ? '' : '(not enforced)'} ${x.detail || ''}</span></li>`)}</ul></details>`;
}
