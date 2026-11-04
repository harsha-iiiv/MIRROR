// Styles for the viewer's own overlays (injected once; uses the studio's CSS variables).
const CSS = `
.viz-card { position: absolute; z-index: 5; pointer-events: none; min-width: 200px; max-width: 280px; font: 500 var(--fs-xs, 11px)/1.45 var(--font, sans-serif);
  color: var(--text); background: color-mix(in srgb, var(--surface, #fff) 92%, transparent); border: 1px solid var(--line, #ddd); border-radius: var(--r-md, 6px);
  padding: 7px 9px; backdrop-filter: blur(6px); font-variant-numeric: tabular-nums; }
.viz-card > div { display: grid; grid-template-columns: 74px auto 1fr; gap: 6px; align-items: baseline; }
.viz-card > div > span { color: var(--muted); }
.viz-card > div > b { font-family: var(--mono, monospace); font-weight: 600; }
.viz-card > div > i { color: var(--faint); font-style: normal; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.viz-card .viz-card-h { display: flex; justify-content: space-between; margin-bottom: 3px; }
.viz-card .viz-card-h span { color: var(--muted); }
.viz-card .viz-card-f { display: block; color: var(--faint); margin-top: 4px; border-top: 1px solid var(--line); padding-top: 4px; }
.viz-card .bad-t, .label3d.bad > span { color: var(--bad); }
.label3d.viz-rule > span { font-family: var(--mono, monospace); font-variant-numeric: tabular-nums; max-width: min(420px, 70vw); overflow: hidden; text-overflow: ellipsis; }
.label3d.viz-rule.bad > span { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
.label3d.viz-rule.warn > span { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
.label3d.viz-rule.goal > span, .label3d.viz-rule.clear > span { color: var(--muted); }
.label3d.viz-measure > span { font-family: var(--mono, monospace); background: var(--surface); }
.label3d.viz-phone > span { color: var(--muted); }
.viz-help { position: absolute; z-index: 6; right: 12px; bottom: 12px; background: color-mix(in srgb, var(--surface, #fff) 95%, transparent);
  border: 1px solid var(--line); border-radius: var(--r-md, 6px); padding: 10px 12px; font: 500 var(--fs-xs, 11px)/1.6 var(--font); color: var(--text); min-width: 230px; backdrop-filter: blur(6px); }
.viz-help-t { font-weight: 600; margin-bottom: 4px; }
.viz-help-r { display: grid; grid-template-columns: 64px 1fr; gap: 8px; }
.viz-help kbd { font: 600 10px/1.6 var(--mono, monospace); border: 1px solid var(--line-strong, #ccc); border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; justify-self: start; }
.viz-fps { position: absolute; right: 8px; top: 50%; font: 500 10px var(--mono, monospace); color: var(--faint); pointer-events: none; }
.viz-fallback-note { position: absolute; left: 12px; bottom: 12px; font-size: var(--fs-xs, 11px); color: var(--muted); }
.viz-timeline { position: relative; width: 100%; user-select: none; }
.viz-timeline canvas { display: block; cursor: ew-resize; touch-action: none; }
.viz-timeline canvas:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.viz-tl-tip { position: absolute; top: -26px; max-width: 420px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font: 500 10px/1.8 var(--mono, monospace);
  background: var(--surface); border: 1px solid var(--line); border-radius: 4px; padding: 0 6px; color: var(--text); pointer-events: none; }
`;
let done = false;
export function injectStyles() {
  if (done || typeof document === 'undefined') return;
  done = true;
  const s = document.createElement('style');
  s.dataset.viz = '1';
  s.textContent = CSS;
  document.head.appendChild(s);
}
