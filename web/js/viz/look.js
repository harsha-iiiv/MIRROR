// Look: colour management, image-based light, soft shadows, physically based materials and
// the measured tabletop (1 cm grid, 10 cm rulers, world frame triad). No decorative effects.
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export function cssVar(name, fallback = '') {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
export const isDark = () => document.documentElement.dataset.theme === 'dark';
export const prefersReducedMotion = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
export const isSmallDevice = (el) => (el?.clientWidth || 1000) < 560 || !!window.matchMedia?.('(pointer: coarse)').matches;

export function setupRenderer(renderer, small) {
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping ?? THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setPixelRatio(Math.min(small ? 1.5 : 2, window.devicePixelRatio || 1));
}

// Neutral studio light probe (three's RoomEnvironment), prefiltered once.
export function makeEnvironment(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = new RoomEnvironment();
  const tex = pmrem.fromScene(env, 0.04).texture;
  env.traverse?.((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
  pmrem.dispose();
  return tex;
}

export function makeLights(scene, small) {
  const hemi = new THREE.HemisphereLight(0xffffff, 0x9a968f, 0.35);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.position.set(-0.55, -0.75, 1.55);
  key.castShadow = true;
  key.shadow.mapSize.set(small ? 1024 : 2048, small ? 1024 : 2048);
  Object.assign(key.shadow.camera, { left: -0.7, right: 0.7, top: 0.6, bottom: -0.6, near: 0.2, far: 3.5 });
  key.shadow.bias = -0.00025;
  key.shadow.normalBias = 0.0015;
  key.shadow.radius = 4;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xffffff, 0.35);
  rim.position.set(0.8, 0.9, 0.9);
  scene.add(rim);
  return { hemi, key, rim };
}

// ------------------------------------------------------------- materials
const cache = new Map();
function mat(key, make) { if (!cache.has(key)) cache.set(key, make()); return cache.get(key); }

export function bodyRole(bodyName, scene) {
  if (/bead/i.test(bodyName)) return 'bead';
  if (/finger|pad/i.test(bodyName)) return 'finger';
  if (/gripper|palm|hand|tcp|wrist|flange/i.test(bodyName)) return 'gripper';
  if (/^(ur10e|ur5e|arm)[/_:]|shoulder|upper_?arm|forearm|elbow|base_link/i.test(bodyName)) return 'arm';
  const o = (scene?.objects || []).find((x) => x.id === bodyName);
  if (o) return o.kind;                 // cup | block | marker
  if ((scene?.trays || []).some((t) => t.id === bodyName)) return 'tray';
  if (/table|floor|ground|world/i.test(bodyName)) return 'world';
  return 'obstacle';
}

// One physically based material per (role, colour). Colours come from the VisualScene.
export function materialFor(role, rgba, { translucent = false } = {}) {
  const [r, g, b, a0] = rgba || [0.7, 0.7, 0.7, 1];
  const a = translucent ? Math.min(a0, 0.32) : a0;
  const col = new THREE.Color().setRGB(r, g, b, THREE.SRGBColorSpace);
  const key = `${role}:${col.getHexString()}:${a.toFixed(3)}`;
  return mat(key, () => {
    const base = { color: col, transparent: a < 0.999, opacity: a, depthWrite: a >= 0.999 };
    switch (role) {
      case 'cup': return new THREE.MeshPhysicalMaterial({ ...base, roughness: 0.32, metalness: 0, clearcoat: 0.7, clearcoatRoughness: 0.18 });
      case 'block': return new THREE.MeshStandardMaterial({ ...base, roughness: 0.48, metalness: 0 });
      case 'marker': return new THREE.MeshPhysicalMaterial({ ...base, roughness: 0.36, metalness: 0, clearcoat: 0.35 });
      case 'gripper': return new THREE.MeshStandardMaterial({ ...base, color: new THREE.Color('#9aa0a8'), roughness: 0.34, metalness: 0.85 });
      case 'finger': return new THREE.MeshStandardMaterial({ ...base, color: new THREE.Color('#3b3e44'), roughness: 0.62, metalness: 0.2 });
      case 'arm': return new THREE.MeshStandardMaterial({ ...base, roughness: 0.42, metalness: 0.25 });
      case 'tray': return new THREE.MeshStandardMaterial({ ...base, roughness: 0.78, metalness: 0 });
      case 'obstacle': return new THREE.MeshPhysicalMaterial({ ...base, roughness: 0.55, metalness: 0, side: a < 0.999 ? THREE.DoubleSide : THREE.FrontSide });
      default: return new THREE.MeshStandardMaterial({ ...base, roughness: 0.7, metalness: 0 });
    }
  });
}

export function beadMaterial() {
  return mat('bead', () => new THREE.MeshStandardMaterial({ color: '#ff2bd6', emissive: '#b0008f', emissiveIntensity: 0.55, roughness: 0.3 }));
}

// ------------------------------------------------------- measured tabletop
// Canvas texture for the table's top face: 1 cm minor, 5 cm and 10 cm major grid, cm rulers
// along two edges and labels every 10 cm, in metres from the world origin.
export function tableTexture(halfX, halfY, renderer) {
  const PX_PER_M = 1600;
  const W = Math.min(2048, Math.round(2 * halfX * PX_PER_M)), H = Math.round((W * halfY) / halfX);
  const k = W / (2 * halfX);
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const g = c.getContext('2d');
  const dark = isDark();
  g.fillStyle = dark ? '#24252a' : '#f3f2ee';
  g.fillRect(0, 0, W, H);
  const X = (x) => (x + halfX) * k, Y = (y) => (halfY - y) * k;   // world -> pixel (top = +y)
  const line = (x0, y0, x1, y1) => { g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); };
  const steps = [[0.01, dark ? 'rgba(255,255,255,0.045)' : 'rgba(0,0,0,0.045)', 1],
    [0.05, dark ? 'rgba(255,255,255,0.09)' : 'rgba(0,0,0,0.085)', 1.2],
    [0.10, dark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.14)', 1.6]];
  for (const [s, col, w] of steps) {
    g.strokeStyle = col; g.lineWidth = w;
    for (let i = Math.ceil(-halfX / s); i * s <= halfX + 1e-9; i++) line(X(i * s), 0, X(i * s), H);
    for (let i = Math.ceil(-halfY / s); i * s <= halfY + 1e-9; i++) line(0, Y(i * s), W, Y(i * s));
  }
  // Rulers along the near (-y) and left (-x) edges.
  g.strokeStyle = dark ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.5)';
  g.fillStyle = g.strokeStyle;
  g.font = `500 ${Math.round(k * 0.016)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  for (let i = Math.ceil(-halfX / 0.01); i * 0.01 <= halfX + 1e-9; i++) {
    const L = i % 10 === 0 ? 0.018 : i % 5 === 0 ? 0.011 : 0.006;
    g.lineWidth = i % 10 === 0 ? 2 : 1.2;
    line(X(i * 0.01), H, X(i * 0.01), H - L * k);
    if (i % 10 === 0 && Math.abs(i * 0.01) < halfX - 0.02) g.fillText(`${(i * 0.01).toFixed(1)}`, X(i * 0.01) + 4, H - 0.022 * k);
  }
  for (let i = Math.ceil(-halfY / 0.01); i * 0.01 <= halfY + 1e-9; i++) {
    const L = i % 10 === 0 ? 0.018 : i % 5 === 0 ? 0.011 : 0.006;
    g.lineWidth = i % 10 === 0 ? 2 : 1.2;
    line(0, Y(i * 0.01), L * k, Y(i * 0.01));
    if (i % 10 === 0 && Math.abs(i * 0.01) < halfY - 0.02) g.fillText(`${(i * 0.01).toFixed(1)}`, 0.022 * k, Y(i * 0.01) - 4);
  }
  g.fillText('x (m)', W - 0.07 * k, H - 0.03 * k);
  g.fillText('y (m)', 0.03 * k, 0.03 * k);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer?.capabilities?.getMaxAnisotropy?.() || 4;
  return tex;
}

// World frame triad at the origin (x, y, z = 5 cm), drawn thin and quiet.
export function frameTriad(len = 0.05) {
  const grp = new THREE.Group();
  const cols = ['#c8372d', '#2f8f4e', '#2f54d9'];
  const dirs = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  dirs.forEach((d, i) => {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0.0008), new THREE.Vector3(d[0] * len, d[1] * len, 0.0008 + d[2] * len)]);
    grp.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: cols[i], transparent: true, opacity: 0.8 })));
  });
  grp.name = 'world-frame';
  return grp;
}
