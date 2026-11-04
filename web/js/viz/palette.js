// One object palette for the whole studio: the 3D workspace, the 2D fallback, the map and the
// capture pages (evidence images, overlay video and table legends use the same colours, written
// by mirror/studio/real_table.py LABEL_COLOURS). Colours identify objects; verdicts keep
// --accent / --warn / --bad.
export const OBJECT_COLORS = {
  cup: '#e5534b',      // the mug
  block: '#d99a1e',    // the box
  marker: '#3c7ff0',
  tray: '#3faf74',
  obstacle: '#b25fd9', // the divider (and any other fixed obstacle)
};

// Page label -> role, for captures that name objects by what they are.
export const LABEL_ROLE = { mug: 'cup', cup: 'cup', red_box: 'block', box: 'block', block: 'block', marker: 'marker', tray: 'tray', divider: 'obstacle', wall: 'obstacle' };

let overrides = {};
// Optional per-body overrides ({bodyName: css}), e.g. colours carried by a capture's scene.
export function setObjectColors(map) { overrides = { ...(map || {}) }; }

export function colorFor(role, bodyName) {
  if (bodyName && overrides[bodyName]) return overrides[bodyName];
  return OBJECT_COLORS[role] || null;
}

// The role of a scene object id, from a scene state (objects carry `kind`).
export function roleOfId(id, scene) {
  const o = (scene?.objects || []).find((x) => x.id === id);
  if (o) return o.kind;
  if ((scene?.trays || []).some((t) => t.id === id)) return 'tray';
  if ((scene?.obstacles || []).some((t) => t.id === id)) return 'obstacle';
  return null;
}
