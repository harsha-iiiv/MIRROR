import {Easing, interpolate} from 'remotion';
export const C = {
  bg: '#111113', panel: '#16171a', line: 'rgba(232,232,230,0.13)', line2: 'rgba(232,232,230,0.24)',
  text: '#E8E8E6', muted: '#A3A3A0', faint: '#67686c', accent: '#6d8bff', accentSoft: 'rgba(109,139,255,0.14)',
};
export const SANS = 'Inter, sans-serif';
export const MONO = 'JetBrains Mono, monospace';
export const FPS = 30;
// one eased glide (sine in-out); every move settles and holds
export const glide = Easing.bezier(0.37, 0, 0.63, 1);
export const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
export const prog = (f: number, a: number, d: number) => clamp01(interpolate(f, [a, a + d], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: glide}));
// text entrance: opacity + 12 px rise over 14 frames (< 1 px/frame)
export const rise = (f: number, a: number, d = 14, dist = 12) => {
  const p = prog(f, a, d);
  return {opacity: p, transform: `translateY(${(1 - p) * dist}px)`};
};
export const fadeOut = (f: number, end: number, d = 12) => 1 - prog(f, end - d, d);
export const fmt = (v: number, n = 1) => v.toFixed(n);
