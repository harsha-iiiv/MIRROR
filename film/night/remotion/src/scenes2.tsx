import React from 'react';
import {useCurrentFrame, Img, staticFile, OffthreadVideo} from 'remotion';
import {C, SANS, MONO, prog, rise, fadeOut, glide} from './theme';
import {Frame, Header, Labels, Caption} from './common';
import {RobotScene, poseAt, Cam} from './robot';
import {DATA, FILM, RUN_OLD, RUN_NEW} from './data';
import {REAL_LABELS, SIM_LABELS} from './scenes1';

const OBJ: Record<string, string> = {cup_0: 'mug', block_0: 'red box', marker_0: 'marker'};
export const phaseText = (ph: string) => {
  if (!ph) return '';
  const [a, b] = ph.split(':');
  return b ? `${a} · ${OBJ[b] ?? b}` : a;
};
const tiltDeg = (q: number[]) => {
  const zz = 1 - 2 * (q[4] * q[4] + q[5] * q[5]); // pose = [x,y,z,w,qx,qy,qz]
  return (Math.acos(Math.max(-1, Math.min(1, zz))) * 180) / Math.PI;
};
export const CAM_A: Cam = {pos: [0.74, 0.1, 0.86], look: [-0.03, 0.0, 0.0], fov: 38};
export const CAM_B: Cam = {pos: [0.69, -0.14, 0.88], look: [-0.03, 0.0, 0.0], fov: 38};

// 3 · the robot on the person's layout (recorded MuJoCo rollout, 5× replay)
export const Run: React.FC<{dur: number; speed?: number; lead?: number; header?: boolean}> = ({dur, speed = 5, lead = 30, header = true}) => {
  const f = useCurrentFrame();
  const simT = Math.max(0, (f - lead) / 30) * speed;
  const i = Math.min(RUN_OLD.n - 1, Math.round(simT * RUN_OLD.frame_hz));
  const p = poseAt(RUN_OLD, 'cup_0', i);
  const done = i >= RUN_OLD.n - 1;
  const W = 1300, H = 820;
  return (
    <Frame>
      <div style={{position: 'absolute', left: 60, top: 150, width: W, height: H, borderRadius: 18, overflow: 'hidden', boxShadow: `0 0 0 1px ${C.line}`}}>
        <RobotScene width={W} height={H} run={RUN_OLD} i={i} camA={CAM_A} camB={CAM_B} camP={prog(f, 0, 110)}
          ribbon={[{body: 'cup_0', i0: 0, color: C.accent}]} grid />
        <div style={{position: 'absolute', left: 16, top: 14, fontFamily: MONO, fontSize: 15, color: C.text, background: 'rgba(17,17,19,0.72)', padding: '4px 10px', borderRadius: 999}}>
          MuJoCo · recorded rollout · replay {speed}×
        </div>
      </div>
      {header && <Header n="03 · ON YOUR LAYOUT" title="The robot does the task, in simulation" x={60} y={50} />}
      <div style={{position: 'absolute', left: 1410, top: 160, width: 440}}>
        <div style={{...rise(f, 10), border: `1px solid ${C.line}`, borderRadius: 14, overflow: 'hidden', width: 220}}>
          <Img src={staticFile('phone_end.jpg')} style={{width: 220, display: 'block'}} />
        </div>
        <div style={{...rise(f, 14), fontFamily: MONO, fontSize: 15, color: C.faint, marginTop: 8}}>your take at 32.4 s</div>
        <div style={{marginTop: 30, ...rise(f, 20)}}>
          {[
            ['sim time', `${simT > RUN_OLD.t[RUN_OLD.n - 1] ? RUN_OLD.t[RUN_OLD.n - 1].toFixed(1) : simT.toFixed(1)} s`],
            ['phase', phaseText(RUN_OLD.phase[i]) + ''],
            ['mug tilt', p ? `${tiltDeg(p).toFixed(1)}°` : '–'],
          ].map(([k, v]) => (
            <div key={k} style={{display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderTop: `1px solid ${C.line}`}}>
              <span style={{fontFamily: MONO, fontSize: 19, color: C.faint}}>{k}</span>
              <span style={{fontFamily: MONO, fontSize: 24, color: C.text}}>{v}</span>
            </div>
          ))}
        </div>
        <div style={{marginTop: 22, opacity: prog(f, (RUN_OLD.n / RUN_OLD.frame_hz / speed) * 30 + lead, 14)}}>
          <div style={{fontSize: 26, fontWeight: 600}}>Checker: success</div>
          <div style={{fontFamily: MONO, fontSize: 20, color: C.muted, lineHeight: 1.7, marginTop: 6}}>
            <div>order mug → red box → marker ✓</div>
            <div>beads kept {RUN_OLD.beads[0][1]} / {RUN_OLD.beads[0][2]}</div>
            <div>max mug tilt carried {FILM.tilt[0]}°</div>
            <div>0 violations</div>
          </div>
        </div>
      </div>
      <Labels items={SIM_LABELS} x={60} y={1000} size={17} />
    </Frame>
  );
};

// 4 · one sentence becomes one scoped rule
export const Sentence: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const text: string = FILM.correction_text;
  const n = Math.floor(Math.max(0, f - 12) * 1.6);
  const shown = text.slice(0, n);
  const typed = n >= text.length;
  const tEnd = 12 + text.length / 1.6;
  const mc = DATA.film;
  const rows: [string, string][] = [
    ['type', 'keep_clearance'], ['applies to', 'cup (the mug)'], ['phase', 'transport (while carried)'],
    ['min distance', '0.06 m from obstacles'], ['source', 'user_correction'],
  ];
  return (
    <Frame>
      <Header n="04 · ONE SENTENCE" title="Say what was wrong" x={96} y={64} />
      <div style={{position: 'absolute', left: 96, top: 270, width: 1720}}>
        <div style={{fontFamily: MONO, fontSize: 18, color: C.faint, marginBottom: 12, ...rise(f, 4)}}>correction</div>
        <div style={{fontSize: 52, fontWeight: 500, letterSpacing: -0.6, lineHeight: 1.22, minHeight: 140, width: 1500, textWrap: 'balance'} as any}>
          “{shown}{typed ? '”' : <span style={{opacity: Math.floor(f / 8) % 2 ? 1 : 0.25, color: C.accent}}>▍</span>}
        </div>
      </div>
      <div style={{position: 'absolute', left: 96, top: 540, width: 900, ...rise(f, tEnd + 10)}}>
        <div style={{fontFamily: MONO, fontSize: 18, color: C.faint, marginBottom: 10}}>becomes one rule · {mc.rule_id}</div>
        <div style={{border: `1px solid ${C.accent}`, borderRadius: 14, padding: '6px 24px', background: C.accentSoft}}>
          {rows.map(([k, v], j) => (
            <div key={k} style={{display: 'flex', justifyContent: 'space-between', padding: '12px 0', borderTop: j ? `1px solid ${C.line}` : 'none', ...rise(f, tEnd + 14 + j * 4)}}>
              <span style={{fontFamily: MONO, fontSize: 20, color: C.muted}}>{k}</span>
              <span style={{fontFamily: MONO, fontSize: 23, color: C.text}}>{v}</span>
            </div>
          ))}
        </div>
      </div>
      <div style={{position: 'absolute', left: 1080, top: 570, width: 740, ...rise(f, tEnd + 40)}}>
        <div style={{fontSize: 30, lineHeight: 1.4}}>Specification v1 → v2: <span style={{color: C.accent}}>+1 constraint</span>.</div>
        <div style={{fontSize: 26, color: C.muted, lineHeight: 1.5, marginTop: 14}}>
          Goals, order and the other three rules are unchanged. The rule names the mug only, so the red box and marker keep their own paths.
        </div>
        <div style={{marginTop: 26, paddingTop: 16, borderTop: `1px solid ${C.line}`}}>
          <div style={{fontFamily: MONO, fontSize: 19, color: C.muted}}>{DATA.spark ? `Muse Spark (1 recorded call, $${DATA.spark.est_cost_usd.toFixed(3)}) and the offline rule interpreter compile the same rule` : 'offline rule interpreter · 0 model calls'}</div>
          {DATA.spark?.rationale && <div style={{fontSize: 22, color: C.text, marginTop: 10, fontStyle: 'normal'}}>model's rationale: “{DATA.spark.rationale}”</div>}
        </div>
      </div>
    </Frame>
  );
};

// geometry helpers for the top-down view
type V = [number, number];
const yawOf = (q: number[]) => 2 * Math.atan2(q[3], q[0]); // quat [w,x,y,z]
const rect = (cx: number, cy: number, hx: number, hy: number, yaw: number): V[] => {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]].map(([x, y]) => [cx + c * x - s * y, cy + s * x + c * y]);
};
const segPt = (p: V, a: V, b: V): V => {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)));
  return [a[0] + t * dx, a[1] + t * dy];
};
const closest = (A: V[], B: V[]): [V, V, number] => {
  let best: [V, V, number] = [A[0], B[0], 1e9];
  const test = (P: V[], Q: V[], flip: boolean) => {
    for (const p of P) for (let k = 0; k < Q.length; k++) {
      const q = segPt(p, Q[k], Q[(k + 1) % Q.length]);
      const d = Math.hypot(p[0] - q[0], p[1] - q[1]);
      if (d < best[2]) best = flip ? [q, p, d] : [p, q, d];
    }
  };
  test(A, B, false); test(B, A, true);
  return best;
};

// 5 · re-judged from the old run's step log, then re-planned
export const Rejudge: React.FC<{dur: number; header?: boolean; tOld?: number; tNew?: number}> = ({dur, header = true, tOld = 20, tNew = 150}) => {
  const f = useCurrentFrame();
  const S = 1160; // px per metre; screen x = sim y, screen y = sim x (sim viewed from above, rotated to match the phone)
  const O = {x: 560, y: 600};
  const P = (v: V) => [O.x + v[1] * S, O.y + v[0] * S];
  const pts = (A: V[]) => A.map((v) => P(v).join(',')).join(' ');
  const wallG = RUN_OLD.bodies.find((b: any) => b.name === 'wall_0').geoms[0];
  const trayG = RUN_OLD.bodies.find((b: any) => b.name === 'tray_0').geoms[0];
  const wy = yawOf(wallG.quat);
  const wall = rect(wallG.pos[0], wallG.pos[1], wallG.size[0], wallG.size[1], wy);
  const band = rect(wallG.pos[0], wallG.pos[1], wallG.size[0] + FILM.min_m, wallG.size[1] + FILM.min_m, wy);
  const tray = rect(trayG.pos[0], trayG.pos[1], trayG.size[0], trayG.size[1], 0);
  const path = (pp: V[], pr: number) => { const n = Math.max(2, Math.round(pp.length * pr)); return pp.slice(0, n).map((v) => P(v).join(',')).join(' '); };
  const pOld = prog(f, tOld, 70), pNew = prog(f, tNew, 70);
  const cupAt = (run: any, k: number): V[] => { const p = run.poses.cup_0[k]; return rect(p[0], p[1], 0.03, 0.03, yawOf(p.slice(3))); };
  const cOld = cupAt(RUN_OLD, FILM.old_clear_frame), cNew = cupAt(RUN_NEW, FILM.new_clear_frame);
  const [aO, bO] = closest(cOld, wall), [aN, bN] = closest(cNew, wall);
  const Dim: React.FC<{a: V; b: V; label: string; at: number; color: string}> = ({a, b, label, at, color}) => {
    const A = P(a), B = P(b); const o = prog(f, at, 16);
    return <g opacity={o}>
      <line x1={A[0]} y1={A[1]} x2={B[0]} y2={B[1]} stroke={color} strokeWidth={2.5} />
      <circle cx={A[0]} cy={A[1]} r={4} fill={color} /><circle cx={B[0]} cy={B[1]} r={4} fill={color} />
      <rect x={Math.min(A[0], B[0]) - 128} y={(A[1] + B[1]) / 2 - 18} width={108} height={32} rx={16} fill="rgba(17,17,19,0.85)" />
      <text x={Math.min(A[0], B[0]) - 74} y={(A[1] + B[1]) / 2 + 6} fill={color} fontFamily="JetBrains Mono" fontSize={22} textAnchor="middle">{label}</text>
    </g>;
  };
  const showOld = tOld + 72, showNew = tNew + 72;
  return (
    <Frame>
      {header && <Header n="05 · CHECK" title="The old run, re-judged from its step log" x={96} y={50} />}
      <div style={{position: 'absolute', left: 96, top: 945, display: 'flex', gap: 28, fontFamily: MONO, fontSize: 16, color: C.muted}}>
        <span style={{opacity: prog(f, tOld, 14)}}><span style={{display: 'inline-block', width: 28, height: 2, background: C.text, opacity: 0.75, verticalAlign: 'middle', marginRight: 8}} />mug path, old run (v1)</span>
        <span style={{opacity: prog(f, tOld + 50, 14)}}><span style={{display: 'inline-block', width: 22, height: 12, border: `1px dashed ${C.accent}`, background: C.accentSoft, verticalAlign: 'middle', marginRight: 8}} />6 cm around the divider</span>
        <span style={{opacity: prog(f, tNew, 14)}}><span style={{display: 'inline-block', width: 28, height: 3, background: C.accent, verticalAlign: 'middle', marginRight: 8}} />mug path, new run (v2)</span>
        <span style={{opacity: prog(f, 0, 14), color: C.faint}}>top view · footprints at the closest point</span>
      </div>
      <svg width={1120} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        <polygon points={pts(tray)} fill="none" stroke={C.line2} strokeWidth={1.5} opacity={prog(f, 0, 14)} />
        <text x={P(tray[2])[0] - 10} y={P(tray[2])[1] + 26} fill={C.faint} fontFamily="JetBrains Mono" fontSize={15} textAnchor="end" opacity={prog(f, 0, 14)}>tray</text>
        <polygon points={pts(band)} fill={C.accentSoft} stroke={C.accent} strokeDasharray="6 6" strokeWidth={1.2} opacity={prog(f, tOld + 50, 20)} />
        <polygon points={pts(wall)} fill="#a49c8e" opacity={prog(f, 0, 14)} />
        <polyline points={path(FILM.old_path, pOld)} fill="none" stroke={C.text} strokeOpacity={0.75} strokeWidth={2} strokeLinejoin="round" />
        <polygon points={pts(cOld)} fill="none" stroke={C.text} strokeWidth={1.5} opacity={prog(f, showOld, 14)} />
        <Dim a={aO} b={bO} label={`${(FILM.old_clear_m * 100).toFixed(1)} cm`} at={showOld + 4} color={C.text} />
        {f > tNew - 2 && <polyline points={path(FILM.new_path, pNew)} fill="none" stroke={C.accent} strokeWidth={2.6} strokeLinejoin="round" />}
        <polygon points={pts(cNew)} fill="none" stroke={C.accent} strokeWidth={1.8} opacity={prog(f, showNew, 14)} />
        <Dim a={aN} b={bN} label={`${(FILM.new_clear_m * 100).toFixed(1)} cm`} at={showNew + 4} color={C.accent} />
        <g opacity={prog(f, 0, 14)} transform="translate(930,190)">
          <line x1={0} y1={0} x2={40} y2={0} stroke={C.faint} /><text x={46} y={5} fill={C.faint} fontFamily="JetBrains Mono" fontSize={14}>sim y</text>
          <line x1={0} y1={0} x2={0} y2={40} stroke={C.faint} /><text x={-4} y={58} fill={C.faint} fontFamily="JetBrains Mono" fontSize={14}>sim x</text>
          <line x1={-60} y1={100} x2={-60 + S * 0.1} y2={100} stroke={C.faint} strokeWidth={2} /><text x={-60} y={122} fill={C.faint} fontFamily="JetBrains Mono" fontSize={14}>10 cm</text>
        </g>
      </svg>
      <div style={{position: 'absolute', left: 1180, top: 200, width: 660}}>
        <div style={{...rise(f, tOld)}}>
          <div style={{fontFamily: MONO, fontSize: 18, color: C.faint}}>old run (v1) under the new rule (v2)</div>
          <div style={{fontSize: 27, color: C.muted, lineHeight: 1.45, marginTop: 8}}>Nothing re-runs. The checker reads the recorded physics state of every step while the mug was carried.</div>
        </div>
        <div style={{...rise(f, showOld + 8), marginTop: 24, paddingTop: 16, borderTop: `1px solid ${C.line}`}}>
          <div style={{fontSize: 40, fontWeight: 600}}>violated</div>
          <div style={{fontFamily: MONO, fontSize: 21, color: C.muted, marginTop: 6, lineHeight: 1.6}}>
            closest {(FILM.old_clear_m * 100).toFixed(2)} cm at t = {FILM.old_clear_t.toFixed(2)} s<br />
            (solid shapes, MuJoCo state log)
          </div>
        </div>
        <div style={{...rise(f, tNew), marginTop: 30, paddingTop: 16, borderTop: `1px solid ${C.line}`}}>
          <div style={{fontFamily: MONO, fontSize: 18, color: C.faint}}>new plan (v2) · same planner, physics and checker</div>
        </div>
        <div style={{...rise(f, showNew + 8), marginTop: 10}}>
          <div style={{fontSize: 40, fontWeight: 600, color: C.accent}}>satisfied</div>
          <div style={{fontFamily: MONO, fontSize: 21, color: C.muted, marginTop: 6, lineHeight: 1.6}}>
            closest {(FILM.new_clear_m * 100).toFixed(2)} cm at t = {FILM.new_clear_t.toFixed(2)} s<br />
            red box {(FILM.unchanged.block_0[1] * 100).toFixed(1)} cm · marker {(FILM.unchanged.marker_0[1] * 100).toFixed(1)} cm: unchanged, out of scope
          </div>
        </div>
      </div>
      <Labels items={SIM_LABELS} x={96} y={1012} size={17} />
    </Frame>
  );
};

// 6 · re-check table + the other sentences
const V_ = (rows: any[], id: string) => rows.find((r: any) => r.rule_id === id)?.verdict ?? '—';
export const Table: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const R = FILM.rows;
  const lines: [string, string][] = [
    ['mug in the tray', 'g_inside_cup'], ['red box in the tray', 'g_inside_block'], ['marker in the tray', 'g_inside_marker'],
    ['order mug → red box', 'o_cup_before_block'], ['order red box → marker', 'o_block_before_marker'],
    ['mug upright while carried', 'c_upright_transport_cup'], ['mug upright once placed', 'c_upright_final_cup'],
    ['nothing touches the divider', 'c_no_contact_divider'], ['NEW · mug ≥ 6 cm from the divider', 'c_keep_clearance_transport_cup'],
  ];
  const cell = (v: string, hi: boolean) => (
    <span style={{fontFamily: MONO, fontSize: 21, color: v === 'violated' ? C.text : v === 'satisfied' ? (hi ? C.accent : C.muted) : C.faint,
      fontWeight: v === 'violated' ? 600 : 400, textDecoration: v === 'violated' ? 'underline' : 'none', textUnderlineOffset: 5}}>{v === '—' ? 'not in v1' : v}</span>
  );
  const oth = FILM.others;
  const say: Record<string, string> = {
    box_upright: 'already satisfied by the old run · re-planned: same routes',
    no_table_contact: 'MIRROR asks a question instead of guessing',
    marker_first: 'refused: it contradicts the order the person stated',
  };
  return (
    <Frame>
      <Header n="06 · WHAT CHANGED" title="Every rule, before and after" x={96} y={50} />
      <div style={{position: 'absolute', left: 96, top: 200, width: 1040}}>
        <div style={{display: 'grid', gridTemplateColumns: '440px 200px 260px 200px', padding: '0 0 12px', ...rise(f, 6)}}>
          {['rule', 'old run (v1)', 'old run re-judged (v2)', 'new run (v2)'].map((h) => <span key={h} style={{fontFamily: MONO, fontSize: 18, color: C.faint}}>{h}</span>)}
        </div>
        {lines.map(([name, id], j) => {
          const last = id === 'c_keep_clearance_transport_cup';
          return (
            <div key={id} style={{display: 'grid', gridTemplateColumns: '440px 200px 260px 200px', padding: '13px 0', borderTop: `1px solid ${last ? C.line2 : C.line}`, ...rise(f, 10 + j * 3)}}>
              <span style={{fontSize: 25, color: last ? C.accent : C.text}}>{name}</span>
              {cell(V_(R.old, id), false)}{cell(V_(R.rechecked, id), last)}{cell(V_(R.new, id), last)}
            </div>
          );
        })}
        <div style={{fontFamily: MONO, fontSize: 19, color: C.faint, marginTop: 16, ...rise(f, 44)}}>
          beads kept {FILM.beads_old[0][1]}/{FILM.beads_old[0][2]} → {FILM.beads_new[0][1]}/{FILM.beads_new[0][2]} · max mug tilt {FILM.tilt[0]}° → {FILM.tilt[1]}° · {FILM.exec_s[0].toFixed(1)} s → {FILM.exec_s[1].toFixed(1)} s of robot time
        </div>
      </div>
      <div style={{position: 'absolute', left: 1290, top: 200, width: 560}}>
        <div style={{fontFamily: MONO, fontSize: 16, color: C.faint, ...rise(f, 60)}}>three other sentences, same layout</div>
        {oth.map((o: any, j: number) => (
          <div key={o.id} style={{padding: '16px 0', borderTop: `1px solid ${C.line}`, marginTop: j ? 0 : 12, ...rise(f, 66 + j * 10)}}>
            <div style={{fontSize: 25}}>“{o.text}”</div>
            <div style={{fontFamily: MONO, fontSize: 19, color: C.muted, marginTop: 6}}>{say[o.id] ?? o.outcome}</div>
          </div>
        ))}
        <div style={{fontSize: 22, color: C.muted, marginTop: 18, lineHeight: 1.4, ...rise(f, 100)}}>A correction that changes nothing says so.</div>
      </div>
      <Labels items={['rule from Muse Spark + offline interpreter (identical)', 'MuJoCo · independent checker', 'development clip D1 · never scored']} x={96} y={1012} size={18} />
    </Frame>
  );
};

export const End: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  return (
    <Frame>
      <div style={{position: 'absolute', left: 0, right: 0, top: 380, textAlign: 'center'}}>
        <div style={{...rise(f, 0), fontFamily: MONO, fontSize: 26, letterSpacing: 6, color: C.accent}}>MIRROR</div>
        <div style={{...rise(f, 4), fontSize: 70, fontWeight: 600, letterSpacing: -1.2, marginTop: 18}}>Corrections you can check.</div>
        <div style={{...rise(f, 14), fontSize: 26, color: C.muted, marginTop: 28}}>Film your table. State the task. Say one correction. See what changed, and why.</div>
      </div>
      <Labels items={[...REAL_LABELS, 'simulation proxies']} at={20} x={0} y={980} size={17} center />
    </Frame>
  );
};

// 3b · the same recorded run drawn from the phone's own viewpoint (twin track output, approximate registration)
export const Twin: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const TW = DATA.twin;
  const h = 900, w = h * 9 / 16;
  return (
    <Frame>
      <div style={{position: 'absolute', left: 120, top: 90, width: w, height: h, borderRadius: 18, overflow: 'hidden', boxShadow: `0 0 0 1px ${C.line}`, background: '#000'}}>
        <OffthreadVideo src={staticFile('twin_overlay.mp4')} startFrom={0} playbackRate={2} muted style={{width: w, height: h}} />
      </div>
      <Header n="03 · ON YOUR TABLE" title="The same run, seen from your phone" x={820} y={90} />
      <Caption at={14} x={820} y={230} w={960} size={28} color={C.muted}>
        The phone's calibration places the camera; the recorded simulator run is drawn over your empty table, with your objects removed by a clean plate.
      </Caption>
      <div style={{position: 'absolute', left: 820, top: 400, width: 960, ...rise(f, 30)}}>
        {[
          ['camera', 'IMG_0806 lens (0.60 px) + board pose at rest (1.13 px, about 88 cm)'],
          ['registration', `tray outline IoU ${TW.tray_iou} in the image · simulated tray ${TW.tray_offset_cm} cm / ${Math.abs(TW.tray_yaw_deg)}° from the real one`],
          ['stand-ins', 'gripper and proxies are simulated, not your objects'],
          ['video', `twin track overlay, first ${(dur / 30 * 2).toFixed(0)} s at 2× (robot at ${2 * (TW.time_map?.sim_speed ?? 1)}× real time)`],
        ].map(([k, v]) => (
          <div key={k} style={{display: 'grid', gridTemplateColumns: '200px 1fr', padding: '13px 0', borderTop: `1px solid ${C.line}`}}>
            <span style={{fontFamily: MONO, fontSize: 18, color: C.faint}}>{k}</span>
            <span style={{fontSize: 25}}>{v}</span>
          </div>
        ))}
      </div>
      <Caption at={50} x={820} y={740} w={960} size={30}>Approximate: one camera, calibration reuse flagged.</Caption>
      <Labels items={REAL_LABELS} x={820} y={952} />
    </Frame>
  );
};

// 5b · both runs on one clock: the old run as a ghost, the corrected run solid, inside the rule's clearance region
export const Ghosts: React.FC<{dur: number; speed?: number; t0?: number; header?: boolean}> = ({dur, speed = 5, t0 = 4, header = true}) => {
  const f = useCurrentFrame();
  const simT = t0 + Math.max(0, (f - 20) / 30) * speed;
  const i = Math.round(simT * 30);
  const W = 1300, H = 820;
  const camA: Cam = {pos: [0.56, 0.2, 1.08], look: [-0.16, -0.03, 0.04], fov: 36};
  const camB: Cam = {pos: [0.53, -0.02, 1.1], look: [-0.16, -0.03, 0.04], fov: 36};
  return (
    <Frame>
      <div style={{position: 'absolute', left: 60, top: 150, width: W, height: H, borderRadius: 18, overflow: 'hidden', boxShadow: `0 0 0 1px ${C.line}`}}>
        <RobotScene width={W} height={H} run={RUN_NEW} i={Math.min(RUN_NEW.n - 1, i)} camA={camA} camB={camB} camP={prog(f, 0, 120)}
          ribbon={[{body: 'cup_0', i0: 0, color: C.accent}]} ghost={{run: RUN_OLD, i: Math.min(RUN_OLD.n - 1, i), ribbon: '#e8e8e6'}}
          clearance={{m: FILM.min_m, opacity: prog(f, 10, 20)}} grid />
        <div style={{position: 'absolute', left: 16, top: 14, fontFamily: MONO, fontSize: 16, color: C.text, background: 'rgba(17,17,19,0.72)', padding: '4px 10px', borderRadius: 999}}>
          MuJoCo · two recorded rollouts on one clock · replay {speed}× · sim {simT.toFixed(1)} s
        </div>
      </div>
      {header && <Header n="05 · SAME LAYOUT, BOTH RUNS" title="The corrected run keeps its distance" x={60} y={50} />}
      <div style={{position: 'absolute', left: 1410, top: 170, width: 440}}>
        {[
          ['ghost', 'old run (v1)', `closest ${(FILM.old_clear_m * 100).toFixed(2)} cm`, '#e8e8e6'],
          ['solid', 'new run (v2)', `closest ${(FILM.new_clear_m * 100).toFixed(2)} cm`, C.accent],
          ['box', 'divider + 6 cm on every side', 'the region the rule forbids', C.accent],
        ].map(([k, a, b, col], j) => (
          <div key={k} style={{padding: '16px 0', borderTop: `1px solid ${C.line}`, ...rise(f, 16 + j * 8)}}>
            <div style={{fontSize: 26, color: col as string}}>{a}</div>
            <div style={{fontFamily: MONO, fontSize: 20, color: C.muted, marginTop: 4}}>{b}</div>
          </div>
        ))}
        <div style={{fontSize: 24, color: C.muted, lineHeight: 1.45, marginTop: 20, ...rise(f, 50)}}>
          The red box and the marker still pass at {(FILM.unchanged.block_0[1] * 100).toFixed(1)} and {(FILM.unchanged.marker_0[1] * 100).toFixed(1)} cm: the rule names the mug only.
        </div>
      </div>
      <Labels items={SIM_LABELS} x={60} y={1000} size={18} />
    </Frame>
  );
};
