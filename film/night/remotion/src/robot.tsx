import React, {useMemo} from 'react';
import {useCurrentFrame} from 'remotion';
import {ThreeCanvas} from '@remotion/three';
import {useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {C, glide} from './theme';

// neutral proxy materials for the film (geometry and poses are the recorded ones; catalogue colours are not used)
const TINT: Record<string, string> = {
  table: '#2a2b2f', tray: '#5d6063', wall: '#a49c8e', cup: '#e9e7e1', block: '#8c8d90', marker: '#b9babd',
  gripper: '#2b2c30', finger: '#3a3b40', bead: '#d8d4ca',
};
const tintFor = (geomName: string, body: string) => {
  if (geomName === 'table') return TINT.table;
  if (body.startsWith('tray')) return TINT.tray;
  if (body.startsWith('wall')) return TINT.wall;
  if (body.includes('/bead')) return TINT.bead;
  if (body.startsWith('cup')) return TINT.cup;
  if (body.startsWith('block')) return TINT.block;
  if (body.startsWith('marker')) return TINT.marker;
  if (body.includes('finger')) return TINT.finger;
  return TINT.gripper;
};
const q4 = (q: number[]) => new THREE.Quaternion(q[1], q[2], q[3], q[0]);

const Geom: React.FC<{g: any; body: string; accent?: boolean; ghost?: boolean}> = ({g, body, accent, ghost}) => {
  if (g.type === 'plane') return null;
  const col = accent ? C.accent : tintFor(g.name, body);
  const quat = q4(g.quat);
  let geo: React.ReactNode = null;
  let extra: [number, number, number] = [0, 0, 0];
  if (g.type === 'box') geo = <boxGeometry args={[g.size[0] * 2, g.size[1] * 2, g.size[2] * 2]} />;
  else if (g.type === 'sphere') geo = <sphereGeometry args={[g.size[0], 14, 10]} />;
  else if (g.type === 'cylinder' || g.type === 'capsule') { geo = <cylinderGeometry args={[g.size[0], g.size[0], g.size[1] * 2, 24]} />; extra = [Math.PI / 2, 0, 0]; }
  if (!geo) return null;
  return (
    <group position={g.pos} quaternion={quat}>
      <mesh rotation={extra} castShadow={!ghost && g.name !== 'table'} receiveShadow>
        {geo}
        <meshStandardMaterial color={col} roughness={g.name === 'table' ? 0.95 : 0.55} metalness={body.startsWith('gripper') ? 0.35 : 0.0}
          transparent={ghost} opacity={ghost ? 0.28 : 1} depthWrite={!ghost} />
      </mesh>
    </group>
  );
};

export const poseAt = (run: any, name: string, i: number) => run.poses[name]?.[Math.max(0, Math.min(run.n - 1, i))];

const Bodies: React.FC<{run: any; i: number; ghost?: boolean; accentBody?: string; skipStatic?: boolean}> = ({run, i, ghost, accentBody, skipStatic}) => (
  <group>
    {run.bodies.map((b: any) => {
      if (b.static) {
        if (skipStatic) return null;
        return <group key={b.name}>{b.geoms.map((g: any, k: number) => <Geom key={k} g={g} body={b.name} />)}</group>;
      }
      const p = poseAt(run, b.name, i);
      if (!p) return null;
      return (
        <group key={b.name} position={[p[0], p[1], p[2]]} quaternion={q4(p.slice(3))}>
          {b.geoms.map((g: any, k: number) => <Geom key={k} g={g} body={b.name} ghost={ghost} accent={accentBody === b.name} />)}
        </group>
      );
    })}
  </group>
);

// flat ribbon under a body's recorded path, revealed up to frame i (setDrawRange, no re-tessellation)
const Ribbon: React.FC<{run: any; body: string; i0: number; i: number; color: string; width?: number; z?: number; opacity?: number}> =
  ({run, body, i0, i, color, width = 0.006, z, opacity = 0.95}) => {
  const geo = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    for (let k = i0; k < run.n; k++) { const p = run.poses[body][k]; if (p) pts.push(new THREE.Vector3(p[0], p[1], z ?? p[2])); }
    const pos: number[] = [];
    for (let k = 0; k < pts.length; k++) {
      const a = pts[Math.max(0, k - 1)], b = pts[Math.min(pts.length - 1, k + 1)];
      const d = new THREE.Vector3().subVectors(b, a); d.z = 0; if (d.length() < 1e-9) d.set(1, 0, 0); d.normalize();
      const n = new THREE.Vector3(-d.y, d.x, 0).multiplyScalar(width / 2);
      pos.push(pts[k].x + n.x, pts[k].y + n.y, pts[k].z, pts[k].x - n.x, pts[k].y - n.y, pts[k].z);
    }
    const idx: number[] = [];
    for (let k = 0; k < pts.length - 1; k++) { const a = 2 * k; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx);
    return g;
  }, [run, body, i0, width, z]);
  const n = Math.max(0, Math.min(run.n - i0 - 1, i - i0));
  geo.setDrawRange(0, n * 6);
  return <mesh geometry={geo}><meshBasicMaterial color={color} side={THREE.DoubleSide} transparent opacity={opacity} depthWrite={false} /></mesh>;
};

// 10 cm grid on the simulator table (0.6 x 0.5 m half extents), a metric reference
const TableGrid: React.FC = () => {
  const geo = useMemo(() => {
    const p: number[] = [];
    for (let x = -0.6; x <= 0.6001; x += 0.1) p.push(x, -0.5, 0.0006, x, 0.5, 0.0006);
    for (let y = -0.5; y <= 0.5001; y += 0.1) p.push(-0.6, y, 0.0006, 0.6, y, 0.0006);
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3)); return g;
  }, []);
  return <lineSegments geometry={geo}><lineBasicMaterial color="#8a8b90" transparent opacity={0.18} /></lineSegments>;
};

export type Cam = {pos: [number, number, number]; look: [number, number, number]; fov?: number};
const CamRig: React.FC<{a: Cam; b: Cam; p: number}> = ({a, b, p}) => {
  const {camera} = useThree();
  const e = glide(Math.max(0, Math.min(1, p)));
  const lerp = (u: number[], v: number[]) => u.map((x, k) => x + (v[k] - x) * e) as [number, number, number];
  const pos = lerp(a.pos, b.pos), look = lerp(a.look, b.look);
  camera.up.set(0, 0, 1);
  camera.position.set(...pos);
  camera.lookAt(...look);
  (camera as THREE.PerspectiveCamera).fov = a.fov ?? 32;
  camera.updateProjectionMatrix();
  return null;
};

export const RobotScene: React.FC<{width: number; height: number; run: any; i: number; camA: Cam; camB: Cam; camP: number;
  ribbon?: {body: string; i0: number; color: string}[]; ghost?: {run: any; i: number}; accentBody?: string; grid?: boolean}> =
  ({width, height, run, i, camA, camB, camP, ribbon = [], ghost, accentBody, grid = false}) => (
  <ThreeCanvas width={width} height={height} shadows gl={{antialias: true, preserveDrawingBuffer: true}} camera={{fov: camA.fov ?? 32, near: 0.02, far: 20}}
    style={{background: 'transparent'}}>
    <CamRig a={camA} b={camB} p={camP} />
    <color attach="background" args={[C.bg]} />
    <hemisphereLight args={['#e6e9ef', '#26262a', 0.95]} />
    <ambientLight intensity={0.35} />
    <directionalLight position={[-0.35, -0.45, 1.7]} intensity={1.25} castShadow shadow-mapSize-width={2048} shadow-mapSize-height={2048}
      shadow-camera-left={-0.8} shadow-camera-right={0.8} shadow-camera-top={0.8} shadow-camera-bottom={-0.8} shadow-bias={-0.0004} shadow-radius={4} />
    <directionalLight position={[0.8, 0.6, 0.9]} intensity={0.35} />
    <Bodies run={run} i={i} accentBody={accentBody} />
    {ghost && <Bodies run={ghost.run} i={ghost.i} ghost skipStatic />}
    {ribbon.map((r) => <Ribbon key={r.body + r.color + 'g'} run={run} body={r.body} i0={r.i0} i={i} color={r.color} z={0.0012} opacity={0.35} />)}
    {ribbon.map((r) => <Ribbon key={r.body + r.color} run={run} body={r.body} i0={r.i0} i={i} color={r.color} width={0.005} />)}
    {grid && <TableGrid />}
  </ThreeCanvas>
);
