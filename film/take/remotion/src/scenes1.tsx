import React from 'react';
import {useCurrentFrame, Sequence, AbsoluteFill} from 'remotion';
import {C, SANS, MONO, prog, rise, fadeOut} from './theme';
import {Frame, Header, Labels, Caption} from './common';
import {PhonePanel, Timeline, activeAt, NAMES} from './phone';
import {DATA, FILM, MASKS} from './data';
const EV3: any[] = DATA.events.filter((e: any) => e.carry);

export const REAL_LABELS: string[] = (DATA.labels ?? ['real phone footage', 'one camera', 'calibration reuse flagged', 'development clip D1, never scored']);
export const SIM_LABELS = ['simulation proxies sized from ruler measurements', DATA.copy.placement_label, 'positions approximate'];
const SEG = DATA.segments;
const CP = DATA.copy;
export const PH = {x: 120, y: 80, h: 920};

// 0 · title over the person's own footage (board and sync card; 2.6× speed)
export const Title: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={SEG.title_src0} rate={SEG.title_rate} speedLabel={`${SEG.title_rate}×`} />
      <div style={{position: 'absolute', left: 820, top: 300, width: 960}}>
        <div style={{...rise(f, 0), fontFamily: MONO, fontSize: 24, letterSpacing: 6, color: C.accent}}>MIRROR</div>
        <div style={{...rise(f, 4), fontSize: 76, fontWeight: 600, letterSpacing: -1.5, marginTop: 18, lineHeight: 1.05}}>Corrections you can check.</div>
        <div style={{...rise(f, 16), fontSize: 32, color: C.muted, marginTop: 34, lineHeight: 1.4, width: 860}}>
          One phone video of a real table. A robot run on that layout. One sentence that changes one rule, and the checks that show what changed.
        </div>
        <div style={{...rise(f, 40), fontFamily: MONO, fontSize: 20, color: C.faint, marginTop: 40}}>
          {CP.title_note}
        </div>
      </div>
      <Labels items={REAL_LABELS} at={24} x={820} y={952} />
    </Frame>
  );
};

// 1 · your table: footage + SAM outlines + event timeline (1.5× speed)
export const Watch: React.FC<{dur: number; src0?: number; rate?: number; compact?: boolean}> = ({dur, src0 = SEG.watch_src0, rate = SEG.watch_rate}) => {
  const f = useCurrentFrame();
  const t = src0 + (f / 30) * rate;
  const act = activeAt(t);
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={src0} rate={rate} masks drawAt={6} speedLabel={`${rate}×`} />
      <Header n="01 · YOUR TABLE" title="MIRROR watches the take" x={820} y={80} />
      <Caption at={14} x={820} y={210} w={960} size={28} color={C.muted}>
        {`${['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine'][Object.keys(MASKS.objects).length] ?? Object.keys(MASKS.objects).length} objects outlined in every frame.`} When one moves in the image, its bar grows on the take's own clock.
      </Caption>
      <Timeline x={940} y={330} w={760} t={t} at={20} />
      <div style={{position: 'absolute', left: 820, top: 720, width: 980, ...rise(f, 40)}}>
        <div style={{fontFamily: MONO, fontSize: 19, color: C.muted, lineHeight: 1.7}}>
          {CP.watch_notes.map((l: string) => <div key={l}>{l}</div>)}
        </div>
      </div>
      <div style={{position: 'absolute', left: 820, top: 880, fontSize: 30, fontWeight: 500, opacity: prog(f, 30, 12)}}>
        <span style={{color: C.muted}}>moving now </span>
        {EV3.map((e: any, j: number) => {
          // each name fades with its own event bracket (6 frames in and out), never pops
          const o = e.object;
          const a = (e.from_s - src0) / rate * 30, b = (e.to_s - src0) / rate * 30;
          const op = Math.min(prog(f, a - 3, 6), 1 - prog(f, b - 3, 6));
          return <span key={j} style={{position: 'absolute', left: 196, top: 0, color: C.accent, opacity: op, whiteSpace: 'nowrap'}}>{NAMES[o]}</span>;
        })}
      </div>
      <Labels items={REAL_LABELS} x={820} y={952} />
    </Frame>
  );
};

// 2 · the gate: MIRROR may not infer the task from this take; the task shown is the one the person stated
export const Gate: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const spec = DATA.spec;
  const g = DATA.gate;
  const L = (k: string, v: string, id: string, at: number) => (
    <div style={{display: 'grid', gridTemplateColumns: '150px 1fr 360px', alignItems: 'baseline', padding: '12px 0', borderTop: `1px solid ${C.line}`, ...rise(f, at)}}>
      <div style={{fontFamily: MONO, fontSize: 17, color: C.faint, letterSpacing: 2}}>{k}</div>
      <div style={{fontSize: 28}}>{v}</div>
      <div style={{fontFamily: MONO, fontSize: 17, color: C.faint, textAlign: 'right'}}>{id}</div>
    </div>
  );
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={CP.end_still_s} rate={0} still="phone_end.jpg" masks dim={0.8} maskDim={0.55} labels={false} />
      <Header n="02 · THE GATE" title="May MIRROR infer the task from this take?" x={820} y={80} />
      <div style={{position: 'absolute', left: 820, top: 220, width: 980, border: `1px solid ${C.line2}`, borderRadius: 14, padding: '22px 26px', ...rise(f, 14)}}>
        <div style={{fontSize: 32, fontWeight: 600}}>{CP.gate.headline}</div>
        <div style={{fontFamily: MONO, fontSize: 18, color: C.muted, marginTop: 10, lineHeight: 1.55}}>
          {CP.gate.code}{CP.gate.detail ? ` · ${CP.gate.detail}` : ''} · {CP.gate.evidence_kind}
        </div>
        <div style={{fontSize: 22, color: C.muted, marginTop: 10}}>{CP.gate.note}</div>
      </div>
      <div style={{position: 'absolute', left: 820, top: 470, width: 980}}>
        <div style={{...rise(f, 70), fontSize: 26, color: C.text, marginBottom: 14}}>
          So the task below is <span style={{color: C.accent}}>{CP.gate.spec_intro}</span>{CP.gate.spec_tail}.
        </div>
        {CP.spec_lines.slice(0, 5).map((l: string[], j: number) => <React.Fragment key={j}>{L(l[0], l[1], l[2], 84 + j * 8)}</React.Fragment>)}
        <div style={{...rise(f, 118), fontFamily: MONO, fontSize: 16, color: C.faint, marginTop: 14}}>
          {CP.spec_prov}
        </div>
      </div>
      <Labels items={REAL_LABELS} x={820} y={952} />
    </Frame>
  );
};
