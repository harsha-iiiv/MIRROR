import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, SANS, MONO, rise, fadeOut} from './theme';

export const Frame: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill style={{background: C.bg, color: C.text, fontFamily: SANS}}>{children}</AbsoluteFill>
);

// section header: index + title; enters once, holds
export const Header: React.FC<{n: string; title: string; at?: number; end?: number; x?: number; y?: number}> = ({n, title, at = 0, end, x = 96, y = 64}) => {
  const f = useCurrentFrame();
  const o = end ? fadeOut(f, end) : 1;
  return (
    <div style={{position: 'absolute', left: x, top: y, opacity: o}}>
      <div style={{...rise(f, at), fontFamily: MONO, fontSize: 20, letterSpacing: 3, color: C.accent}}>{n}</div>
      <div style={{...rise(f, at + 4), fontSize: 44, fontWeight: 600, marginTop: 8, letterSpacing: -0.5}}>{title}</div>
    </div>
  );
};

// honest labels, always on screen while the related material is visible
export const Labels: React.FC<{items: string[]; at?: number; end?: number; x?: number; y?: number; size?: number; center?: boolean}> = ({items, at = 0, end, x = 96, y = 1012, size = 19, center = false}) => {
  const f = useCurrentFrame();
  const o = end ? fadeOut(f, end) : 1;
  return (
    <div style={{position: 'absolute', left: x, top: y, display: 'flex', gap: 10, flexWrap: 'wrap', ...rise(f, at, 14, 6), ...(o < 1 ? {opacity: o} : {}), ...(center ? {width: '100%', justifyContent: 'center'} : {})}}>
      {items.map((t) => (
        <div key={t} style={{fontFamily: MONO, fontSize: size, color: C.muted, border: `1px solid ${C.line2}`, borderRadius: 999, padding: '5px 14px', whiteSpace: 'nowrap'}}>{t}</div>
      ))}
    </div>
  );
};

export const Caption: React.FC<{children: React.ReactNode; at: number; x: number; y: number; w?: number; size?: number; color?: string; end?: number; weight?: number; mono?: boolean}> =
  ({children, at, x, y, w = 760, size = 30, color = C.text, end, weight = 400, mono = false}) => {
  const f = useCurrentFrame();
  const o = end ? fadeOut(f, end) : 1;
  return <div style={{position: 'absolute', left: x, top: y, width: w, fontSize: size, lineHeight: 1.35, color, fontWeight: weight,
    fontFamily: mono ? MONO : SANS, opacity: o, ...rise(f, at), ...(o < 1 ? {opacity: Math.min(o, rise(f, at).opacity)} : {})}}>{children}</div>;
};
