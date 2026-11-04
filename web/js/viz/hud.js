// Heads-up readouts: hover card with checker-definition measurements, and the shortcut sheet.
export const SHORTCUTS = [
  ['Space', 'play / pause'], [', .', 'previous / next frame'], ['← →', 'step 1 s (Shift: 5 s)'],
  ['[ ]', 'previous / next event'], ['1 2 3 4', 'follow · 3D · top · side'], ['5', "phone's viewpoint (captures)"],
  ['R', 'rule geometry'], ['G', 'ghost of the previous run'], ['P', 'paths'], ['M', 'measure on hover'],
  ['L', 'labels'], ['X', 'real table texture (captures)'], ['?', 'this sheet'],
];

export function makeHud(el) {
  const card = document.createElement('div');
  card.className = 'viz-card';
  card.setAttribute('role', 'status');
  card.hidden = true;
  el.appendChild(card);
  const help = document.createElement('div');
  help.className = 'viz-help';
  help.hidden = true;
  help.innerHTML = '<div class="viz-help-t">Keyboard</div>' + SHORTCUTS.map(([k, v]) => `<div class="viz-help-r"><kbd>${k}</kbd><span>${v}</span></div>`).join('');
  el.appendChild(help);
  const fps = document.createElement('div');
  fps.className = 'viz-fps';
  fps.hidden = true;
  el.appendChild(fps);
  return { card, help, fps };
}

const f = (x, d = 1) => (Number.isFinite(x) ? x.toFixed(d) : '–');

export function cardHTML(m) {
  const rows = [];
  rows.push(`<div class="viz-card-h"><b>${m.name}</b><span>${m.kind || ''}</span></div>`);
  if (m.tilt != null) rows.push(`<div><span>tilt</span><b class="${m.tiltOver ? 'bad-t' : ''}">${f(m.tilt)}°</b>${m.tiltLimit != null ? `<i>limit ${f(m.tiltLimit, 0)}°</i>` : ''}</div>`);
  if (m.speed != null) rows.push(`<div><span>speed</span><b>${f(m.speed, 3)} m/s</b><i>frame difference</i></div>`);
  if (m.height != null) rows.push(`<div><span>height</span><b>${f(m.height * 100)} cm</b><i>centre above table</i></div>`);
  if (m.clear) rows.push(`<div><span>to obstacle</span><b class="${m.clearOver ? 'bad-t' : ''}">${f(m.clear.d * 100, 2)} cm</b><i>${m.clear.owner}${m.clearLimit != null ? ` · min ${f(m.clearLimit * 100, 1)} cm` : ''} · solid shape</i></div>`);
  rows.push(`<div class="viz-card-f">t ${f(m.t, 3)} s · frame ${m.frame} · checker geometry at this playback frame</div>`);
  return rows.join('');
}
