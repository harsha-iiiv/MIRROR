import React from 'react';
import {useCurrentFrame, Sequence, AbsoluteFill} from 'remotion';
import {C, SANS, MONO, prog, rise, fadeOut} from './theme';
import {Frame, Header, Labels, Caption} from './common';
import {PhonePanel, Timeline, activeAt, NAMES} from './phone';
import {DATA, FILM} from './data';

export const REAL_LABELS = ['real phone footage', 'one camera', 'calibration reuse flagged', 'development clip D1 · never scored'];
export const SIM_LABELS = ['simulation proxies sized from ruler measurements', 'layout centred by software', 'positions approximate'];
export const PH = {x: 120, y: 80, h: 920};

// 0 · title over the person's own footage (board and sync card; 2.6× speed)
export const Title: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={2.3} rate={2.6} speedLabel="2.6×" />
      <div style={{position: 'absolute', left: 820, top: 300, width: 960}}>
        <div style={{...rise(f, 0), fontFamily: MONO, fontSize: 24, letterSpacing: 6, color: C.accent}}>MIRROR</div>
        <div style={{...rise(f, 4), fontSize: 76, fontWeight: 600, letterSpacing: -1.5, marginTop: 18, lineHeight: 1.05}}>Corrections you can check.</div>
        <div style={{...rise(f, 16), fontSize: 32, color: C.muted, marginTop: 34, lineHeight: 1.4, width: 860}}>
          One phone video of a real table. A robot run on that layout. One sentence that changes one rule, and the checks that show what changed.
        </div>
        <div style={{...rise(f, 40), fontFamily: MONO, fontSize: 20, color: C.faint, marginTop: 40}}>
          the printed board and sync card calibrate the phone's camera
        </div>
      </div>
      <Labels items={REAL_LABELS} at={24} x={820} y={952} />
    </Frame>
  );
};

// 1 · your table: footage + SAM outlines + event timeline (1.5× speed)
export const Watch: React.FC<{dur: number; src0?: number; rate?: number; compact?: boolean}> = ({dur, src0 = 12.8, rate = 1.5}) => {
  const f = useCurrentFrame();
  const t = src0 + (f / 30) * rate;
  const act = activeAt(t);
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={src0} rate={rate} masks drawAt={6} speedLabel={`${rate}×`} />
      <Header n="01 · YOUR TABLE" title="MIRROR watches the take" x={820} y={80} />
      <Caption at={14} x={820} y={210} w={960} size={28} color={C.muted}>
        Five objects outlined in every frame. When one moves in the image, its bar grows on the take's own clock.
      </Caption>
      <Timeline x={940} y={330} w={760} t={t} at={20} />
      <div style={{position: 'absolute', left: 820, top: 720, width: 980, ...rise(f, 40)}}>
        <div style={{fontFamily: MONO, fontSize: 18, color: C.faint, lineHeight: 1.7}}>
          <div>masks · SAM 3.1, box prompts from Muse Spark grounding at 12.5 s</div>
          <div>recorded on 3 Nov ($0.33) · replayed here, no model call</div>
          <div>{`1 small mug mask change at 13.5 s (hand arriving) is kept for review, not hidden`}</div>
        </div>
      </div>
      <div style={{position: 'absolute', left: 820, top: 880, fontSize: 30, fontWeight: 500, opacity: act.length ? 1 : 0.0}}>
        {act.length ? <span>moving now: <span style={{color: C.accent}}>{act.map((a) => NAMES[a]).join(', ')}</span></span> : null}
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
      <div style={{fontFamily: MONO, fontSize: 16, color: C.faint, textAlign: 'right'}}>{id}</div>
    </div>
  );
  return (
    <Frame>
      <PhonePanel {...PH} srcStart={32.4} rate={0} still="phone_end.jpg" masks dim={0.8} maskDim={0.55} labels={false} />
      <Header n="02 · THE GATE" title="May MIRROR infer the task from this take?" x={820} y={80} />
      <div style={{position: 'absolute', left: 820, top: 220, width: 980, border: `1px solid ${C.line2}`, borderRadius: 14, padding: '22px 26px', ...rise(f, 14)}}>
        <div style={{fontSize: 32, fontWeight: 600}}>Not yet. The gate refused.</div>
        <div style={{fontFamily: MONO, fontSize: 18, color: C.muted, marginTop: 10, lineHeight: 1.55}}>
          {g.blocking[0].code} · focal length re-estimated 7.1% different (limit 6%) · {g.evidence_kind}
        </div>
        <div style={{fontSize: 22, color: C.muted, marginTop: 10}}>One camera, and the phone was moved afterwards, so the flag stays open. No bypass exists, and none was used.</div>
      </div>
      <div style={{position: 'absolute', left: 820, top: 470, width: 980}}>
        <div style={{...rise(f, 70), fontSize: 26, color: C.text, marginBottom: 14}}>
          So the task below is <span style={{color: C.accent}}>the one the person stated</span>, not one MIRROR inferred.
        </div>
        {L('GOALS', 'mug, red box and marker end inside the tray', 'g_inside_cup · _block · _marker', 84)}
        {L('ORDER', 'mug  →  red box  →  marker', 'o_cup_before_block · o_block_before_marker', 92)}
        {L('RULE', 'mug within 15° of upright while carried and once placed', 'c_upright_transport_cup · _final_cup', 100)}
        {L('RULE', 'nothing touches the divider', 'c_no_contact_divider', 108)}
        <div style={{...rise(f, 118), fontFamily: MONO, fontSize: 16, color: C.faint, marginTop: 14}}>
          provenance: manual (stated by the person) · status: confirmed · {spec.spec_id} v{spec.version}
        </div>
      </div>
      <Labels items={REAL_LABELS} x={820} y={952} />
    </Frame>
  );
};
