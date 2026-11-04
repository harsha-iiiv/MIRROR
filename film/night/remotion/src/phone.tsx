import React from 'react';
import {OffthreadVideo, Img, staticFile, useCurrentFrame, Sequence} from 'remotion';
import {C, MONO, prog, rise} from './theme';
import {MASKS, DATA} from './data';

export const NAMES: Record<string, string> = new Proxy({} as Record<string, string>, {get: (_t, k: string) => String(k).replace(/_/g, ' ')});
const EVENTS: any[] = DATA.events.filter((e: any) => e.carry); // carries (image displacement >= 100 px at 540x960)
export const activeAt = (t: number) => EVENTS.filter((e) => t >= e.from_s && t <= e.to_s).map((e) => e.object);

// mask outlines for source time t (seconds), drawn in the 540x960 image coordinates of the tracks
export const MaskLayer: React.FC<{t: number; drawAt?: number; w: number; h: number; labels?: boolean; dim?: number}> = ({t, drawAt = -999, w, h, labels = true, dim = 1}) => {
  const f = useCurrentFrame();
  const i = Math.max(0, Math.min(MASKS.n_frames - 1, Math.round(t * MASKS.fps)));
  const act = activeAt(t);
  const order = ['tray', ...Object.keys(MASKS.objects).filter((k) => MASKS.objects[k].static), ...Object.keys(MASKS.objects).filter((k) => k !== 'tray' && !MASKS.objects[k].static)];
  return (
    <svg width={w} height={h} viewBox="0 0 540 960" style={{position: 'absolute', left: 0, top: 0}}>
      {order.map((lab, k) => {
        const o = MASKS.objects[lab];
        const p = o.static ?? o.poly[i];
        if (!p) return null;
        const d = 'M' + p.map((q: number[]) => q.join(',')).join('L') + 'Z';
        const draw = prog(f, drawAt + k * 5, 26);
        const on = act.includes(lab);
        const stroke = on ? C.accent : 'rgba(240,240,236,0.92)';
        // label anchor: top of the outline
        let minY = 1e9, cx = 0;
        for (const q of p) { if (q[1] < minY) { minY = q[1]; cx = q[0]; } }
        const lx = Math.max(30, Math.min(510, cx));
        const ly = Math.max(18, minY - 10);
        return (
          <g key={lab} opacity={dim}>
            {on && <path d={d} fill="rgba(109,139,255,0.18)" />}
            <path d={d} fill="none" stroke={stroke} strokeWidth={on ? 2.6 : 1.6} pathLength={1}
              strokeDasharray={o.static ? '0.012 0.008' : '1 0'} strokeDashoffset={o.static ? 0 : 1 - draw}
              opacity={o.static ? draw : 1} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            {labels && <g opacity={prog(f, drawAt + 18 + k * 5, 12)}>
              <rect x={lx - 5} y={ly - 18} width={NAMES[lab].length * 10.4 + 12} height={23} rx={11.5} fill="rgba(17,17,19,0.78)" />
              <text x={lx + 1} y={ly - 1} fontFamily="JetBrains Mono" fontSize={16} fill={on ? C.accent : C.text}>{NAMES[lab]}</text>
            </g>}
          </g>
        );
      })}
    </svg>
  );
};

// the person's own footage in a portrait panel; segment plays srcStart.. at `rate`
export const PhonePanel: React.FC<{x: number; y: number; h: number; srcStart: number; rate: number; masks?: boolean; drawAt?: number;
  still?: string; speedLabel?: string; dim?: number; maskDim?: number; labels?: boolean}> =
  ({x, y, h, srcStart, rate, masks = false, drawAt, still, speedLabel, dim = 1, maskDim = 1, labels = true}) => {
  const f = useCurrentFrame();
  const w = h * 9 / 16;
  const t = srcStart + (f / 30) * rate;
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, height: h, borderRadius: 18, overflow: 'hidden', background: '#000',
      boxShadow: `0 0 0 1px ${C.line}`}}>
      <div style={{position: 'absolute', inset: 0, opacity: dim}}>
        {still ? <Img src={staticFile(still)} style={{width: w, height: h}} /> :
          <OffthreadVideo src={staticFile('phone.mp4')} startFrom={Math.round(srcStart * 30)} playbackRate={rate} muted style={{width: w, height: h}} />}
      </div>
      {masks && <MaskLayer t={still ? srcStart : t} drawAt={drawAt} w={w} h={h} labels={labels} dim={maskDim} />}
      <div style={{position: 'absolute', left: 14, top: 12, fontFamily: MONO, fontSize: 15, color: C.text, background: 'rgba(17,17,19,0.72)',
        padding: '4px 10px', borderRadius: 999}}>
        {`IMG_0815 · ${(still ? srcStart : t).toFixed(1)} s`}{speedLabel ? ` · ${speedLabel}` : ''}
      </div>
    </div>
  );
};

// event timeline on the take's own clock (0 to 49.8 s)
export const Timeline: React.FC<{x: number; y: number; w: number; t: number; at?: number}> = ({x, y, w, t, at = 0}) => {
  const f = useCurrentFrame();
  const T = MASKS.n_frames / MASKS.fps;
  const X = (s: number) => (s / T) * w;
  const rows = [...Object.keys(MASKS.objects).filter((k) => !MASKS.objects[k].static && k !== 'tray'), 'tray', ...Object.keys(MASKS.objects).filter((k) => MASKS.objects[k].static)];
  const rowH = 54;
  const ev: any[] = DATA.events;
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w}}>
      <div style={{...rise(f, at), fontFamily: MONO, fontSize: 17, color: C.muted, marginBottom: 18}}>
        object motion in the image · one camera · SAM 3.1 masks, 30 fps
      </div>
      <svg width={w + 140} height={rows.length * rowH + 60} style={{overflow: 'visible', marginLeft: -120}}>
        <g transform="translate(120,0)">
          {[0, 10, 20, 30, 40].map((s) => (
            <g key={s} opacity={prog(f, at + 6, 14)}>
              <line x1={X(s)} x2={X(s)} y1={0} y2={rows.length * rowH} stroke={C.line} />
              <text x={X(s)} y={rows.length * rowH + 26} fill={C.faint} fontFamily="JetBrains Mono" fontSize={15} textAnchor="middle">{s} s</text>
            </g>
          ))}
          {rows.map((r, k) => {
            const yy = k * rowH + rowH / 2;
            const rowEv = ev.filter((e) => e.object === r);
            return (
              <g key={r} opacity={prog(f, at + 8 + k * 3, 14)}>
                <text x={-16} y={yy + 7} fill={C.text} fontFamily="Inter" fontSize={21} textAnchor="end">{NAMES[r]}</text>
                <line x1={0} x2={w} y1={yy} y2={yy} stroke={C.line2} strokeDasharray={MASKS.objects[r]?.static ? '3 6' : undefined} />
                {rowEv.map((e, j) => {
                  const big = e.carry;
                  const a = e.from_s, b = e.to_s;
                  const vis = Math.max(0, Math.min(b, t) - a);
                  if (t < a) return null;
                  return (
                    <g key={j}>
                      <rect x={X(a)} y={yy - (big ? 11 : 5)} width={Math.max(2, X(a + vis) - X(a))} height={big ? 22 : 10} rx={big ? 11 : 5}
                        fill={big ? C.accent : C.faint} />
                      {big && <text x={X(b) + 14} y={yy + 6} opacity={Math.max(0, Math.min(1, (t - b) / 0.3))} fill={C.muted} fontFamily="JetBrains Mono" fontSize={17}>
                        {`${a.toFixed(1)}–${b.toFixed(1)} s · ends in the tray (2D)`}</text>}
                    </g>
                  );
                })}
                {(r === 'tray' || MASKS.objects[r]?.static) && r === 'tray' && <text x={w} y={yy - 10} fill={C.faint} fontFamily="JetBrains Mono" fontSize={14} textAnchor="end">stationary</text>}
                {MASKS.objects[r]?.static && <text x={w} y={yy - 10} fill={C.faint} fontFamily="JetBrains Mono" fontSize={14} textAnchor="end">one static mask (held for every frame)</text>}
              </g>
            );
          })}
          <line x1={X(Math.min(t, T))} x2={X(Math.min(t, T))} y1={-8} y2={rows.length * rowH + 4} stroke={C.text} strokeWidth={1.5} opacity={prog(f, at + 10, 10)} />
        </g>
      </svg>
    </div>
  );
};
