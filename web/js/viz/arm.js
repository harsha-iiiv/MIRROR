// Display meshes for the UR arm backend (decimated MuJoCo Menagerie visuals, BSD-3; see
// assets/NOTICE). The simulation's collision capsules stay available (C toggles them).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const cache = new Map();
const matCache = new Map();
const pbr = (key, o) => () => { if (!matCache.has(key)) matCache.set(key, new THREE.MeshStandardMaterial({ side: THREE.DoubleSide, ...o })); return matCache.get(key); };
// Menagerie material names -> display finish (colours from its rgba, lifted slightly so black reads).
const FINISH = [['urblue', pbr('urblue', { color: '#7aa6c9', roughness: 0.42, metalness: 0.15 })],
  ['linkgray', pbr('linkgray', { color: '#d3d5d8', roughness: 0.32, metalness: 0.55 })],
  ['jointgray', pbr('jointgray', { color: '#55585e', roughness: 0.5, metalness: 0.3 })],
  ['black', pbr('black', { color: '#1f2023', roughness: 0.6, metalness: 0.1 })]];
const LINKS = ['upper_arm_link', 'shoulder_link', 'forearm_link', 'wrist_1_link', 'wrist_2_link', 'wrist_3_link', 'base'];
const base = new URL('./assets/', import.meta.url).href;

function load(arm) {
  if (!cache.has(arm)) cache.set(arm, new GLTFLoader().loadAsync(`${base}${arm}.glb`));
  return cache.get(arm);
}

export function armModel(visual) {
  const ua = visual?.bodies?.find((b) => b.name === 'arm/upper_arm_link');
  if (!ua) return null;
  const long = Math.max(...ua.geoms.map((g) => (g.type === 'capsule' ? g.size[1] : 0)));
  return long > 0.25 ? 'ur10e' : 'ur5e';
}

// Base pose = mount plate pose composed with the Menagerie base quat (0 0 0 -1).
function basePose(visual) {
  const mount = visual.bodies.find((b) => b.name === 'arm/mount');
  const plate = mount?.geoms?.find((g) => /mount_plate/.test(g.name || '')) || mount?.geoms?.[0];
  if (!plate) return null;
  const q = new THREE.Quaternion(plate.quat[1], plate.quat[2], plate.quat[3], plate.quat[0]);
  const pos = new THREE.Vector3(...plate.pos).sub(new THREE.Vector3(0, 0, -0.01).applyQuaternion(q));
  const qb = q.clone().multiply(new THREE.Quaternion(0, 0, -1, 0));
  return { pos, quat: qb };
}

// Attach meshes to the viewer's arm body groups. Returns the model name, or null.
export async function attachArm(viewer, visual, token) {
  const arm = armModel(visual);
  if (!arm) return null;
  let gltf;
  try { gltf = await load(arm); } catch (e) { console.warn('arm meshes not loaded', e); return null; }
  if (viewer._armToken !== token) return null;     // a newer scene replaced this one
  const nodes = [];
  gltf.scene.traverse((o) => { if (o.isMesh) nodes.push(o); });
  let baseGrp = null;
  for (const n of nodes) {
    // GLTFLoader strips ':' from node names ("shoulder_link:urblue" -> "shoulder_linkurblue").
    const nm = n.name || n.parent?.name || '';
    const body = LINKS.find((l) => nm.startsWith(l));
    if (!body) continue;
    let grp = viewer.bodies.get(`arm/${body}`);
    if (body === 'base') {
      if (!baseGrp) {
        const bp = basePose(visual);
        if (!bp) continue;
        baseGrp = new THREE.Group();
        baseGrp.name = 'arm/base (display)';
        baseGrp.position.copy(bp.pos); baseGrp.quaternion.copy(bp.quat);
        baseGrp.userData = { static: true, bodyName: 'arm/base', role: 'arm' };
        viewer.world.add(baseGrp);
      }
      grp = baseGrp;
    }
    if (!grp) continue;
    const finish = FINISH.find(([k]) => nm.endsWith(k)) || FINISH[2];
    const geo = n.geometry;
    if (!geo.userData.normalsFixed) { geo.deleteAttribute('normal'); geo.computeVertexNormals(); geo.userData.normalsFixed = true; }
    const m = new THREE.Mesh(geo, finish[1]());
    m.castShadow = true; m.receiveShadow = true;
    m.userData.armMesh = true;
    grp.add(m);
  }
  for (const [name, grp] of viewer.bodies) {
    if (!name.startsWith('arm/') || name === 'arm/mount') continue;
    for (const c of grp.children) if (c.isMesh && !c.userData.armMesh) { c.userData.collision = true; c.visible = !!viewer.showCollision; }
  }
  return arm;
}
