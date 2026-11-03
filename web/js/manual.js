// Manual structured editing (the pilot's "manual" condition): the same rule language as
// corrections, chosen from fields instead of words. Produces SpecOps (mirror/contracts.py)
// that the server validates exactly like a compiled correction.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { manualOps, manualEdit, KINDS, PHASES, CONTACT_WITH } from './manual_ops.js';

const html = htm.bind(h);
export { manualOps };

export function ManualEditor({ spec, onSubmit, disabled }) {
  const [f, setF] = useState({ type: 'upright', who: 'cup', phase: 'transport', max_tilt_deg: 15, min_m: 0.03, with: ['obstacle'], status: 'confirmed', before: 'marker', after: 'cup' });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const { ops, error } = manualOps(spec, f);
  const who = html`<label>Object<select value=${f.who} onChange=${set('who')}>${KINDS.map((k) => html`<option value=${k}>${k}</option>`)}<option value="all">all objects</option></select></label>`;
  return html`<div class="manual" role="group" aria-label="Edit the rules directly">
    <div class="ch-form">
      <label>Rule<select value=${f.type} onChange=${set('type')}>
        <option value="upright">stay upright</option><option value="keep_clearance">keep a distance from obstacles</option>
        <option value="forbidden_contact">must not touch</option><option value="ordering">order of objects</option></select></label>
      ${f.type === 'ordering' ? html`
        <label>First<select value=${f.before} onChange=${set('before')}>${KINDS.map((k) => html`<option value=${k}>${k}</option>`)}</select></label>
        <label>Then<select value=${f.after} onChange=${set('after')}>${KINDS.map((k) => html`<option value=${k}>${k}</option>`)}</select></label>`
      : html`${who}
        <label>When<select value=${f.phase} onChange=${set('phase')}>${Object.entries(PHASES).filter(([p]) => f.type !== 'keep_clearance' || p !== 'final').map(([p, t]) => html`<option value=${p}>${t}</option>`)}</select></label>
        ${f.type === 'upright' ? html`<label>Max tilt (°)<input class="num-in" type="number" min="1" max="90" value=${f.max_tilt_deg} onInput=${set('max_tilt_deg')} /></label>` : ''}
        ${f.type === 'keep_clearance' ? html`<label>Min distance (m)<input class="num-in" type="number" step="0.01" min="0.01" max="0.2" value=${f.min_m} onInput=${set('min_m')} /></label>` : ''}
        ${f.type === 'forbidden_contact' ? html`<label>Must not touch<select value=${f.with[0]} onChange=${(e) => setF({ ...f, with: [e.target.value] })}>${Object.entries(CONTACT_WITH).map(([k, t]) => html`<option value=${k}>${t}</option>`)}</select></label>` : ''}`}
      <label>Status<select value=${f.status} onChange=${set('status')}><option value="confirmed">required</option><option value="rejected">not required</option></select></label>
    </div>
    ${error ? html`<p class="muted">${error}</p>` : html`<p class="faint">Preview: ${ops.map((o) => `${o.op} ${o.target_id}`).join(', ')} (the server validates and decides).</p>`}
    <div class="row"><span class="spacer"></span><button class="btn primary" disabled=${disabled || !!error || !ops.length} onClick=${() => onSubmit([manualEdit(f)], ops, f)}>Propose this change</button></div>
  </div>`;
}
