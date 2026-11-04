import React from 'react';
import {Composition, Sequence, AbsoluteFill, useCurrentFrame, interpolate} from 'remotion';
import {loadFonts} from './fonts';
import {C} from './theme';
import {Title, Watch, Gate} from './scenes1';
import {Run, Sentence, Rejudge, Table, End, Twin, Ghosts} from './scenes2';

loadFonts();

// each shot fades in from the background and out to it (no zoom, no whip, no shake)
const Shot: React.FC<{dur: number; children: React.ReactNode; fin?: number; fout?: number}> = ({dur, children, fin = 10, fout = 10}) => {
  const f = useCurrentFrame();
  const a = fin > 0 ? interpolate(f, [0, fin], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  const b = fout > 0 ? interpolate(f, [dur - 1 - fout, dur - 1], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  const o = Math.min(a, b);
  return <AbsoluteFill style={{background: C.bg}}><AbsoluteFill style={{opacity: o}}>{children}</AbsoluteFill></AbsoluteFill>;
};

const WatchF: React.FC<{dur: number}> = ({dur}) => <Watch dur={dur} src0={12.8} rate={1.75} />;
const RunF: React.FC<{dur: number}> = ({dur}) => <Run dur={dur} speed={8} lead={20} />;
export const SHOTS: [string, number, React.FC<{dur: number}>][] = [
  ['title', 120, Title], ['watch', 330, WatchF], ['gate', 210, Gate], ['run', 345, RunF], ['twin', 150, Twin],
  ['sentence', 210, Sentence], ['rejudge', 285, Rejudge], ['ghosts', 210, Ghosts], ['table', 240, Table], ['end', 120, End],
];
export const FILM_FRAMES = SHOTS.reduce((a, s) => a + s[1], 0);

const Film: React.FC = () => {
  let at = 0;
  return (
    <AbsoluteFill style={{background: C.bg}}>
      {SHOTS.map(([name, dur, S], k) => {
        const from = at; at += dur;
        return <Sequence key={name} name={name} from={from} durationInFrames={dur}>
          <Shot dur={dur} fin={k === 0 ? 0 : 10} fout={k === SHOTS.length - 1 ? 14 : 10}><S dur={dur} /></Shot>
        </Sequence>;
      })}
    </AbsoluteFill>
  );
};

// loops: the 1920x1080 layout drawn into a 1280x720 frame (exact 2/3 scale), fading from and to the background so the
// first and last frames match
const Loop: React.FC<{children: React.ReactNode}> = ({children}) => (
  <Shot dur={240} fin={12} fout={12}>
    <div style={{position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, transform: 'scale(0.6666667)', transformOrigin: '0 0'}}>{children}</div>
  </Shot>
);
const LoopWatch = () => <Loop><Watch dur={240} src0={14.0} rate={2} /></Loop>;
const LoopRun = () => <Loop><Run dur={240} speed={9.5} lead={2} /></Loop>;
const LoopRejudge = () => <Loop><Rejudge dur={240} tOld={8} tNew={90} /></Loop>;
const LoopGhosts = () => <Loop><Ghosts dur={240} speed={5} t0={4} /></Loop>;
const LoopSentence = () => <Loop><Sentence dur={240} /></Loop>;

// single-shot compositions for quick stills / review
const one = (S: React.FC<{dur: number}>, dur: number) => () => <S dur={dur} />;

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Film" component={Film} durationInFrames={FILM_FRAMES} fps={30} width={1920} height={1080} />
    <Composition id="LoopWatch" component={LoopWatch} durationInFrames={240} fps={30} width={1280} height={720} />
    <Composition id="LoopRun" component={LoopRun} durationInFrames={240} fps={30} width={1280} height={720} />
    <Composition id="LoopRejudge" component={LoopRejudge} durationInFrames={240} fps={30} width={1280} height={720} />
    <Composition id="LoopGhosts" component={LoopGhosts} durationInFrames={240} fps={30} width={1280} height={720} />
    <Composition id="LoopSentence" component={LoopSentence} durationInFrames={240} fps={30} width={1280} height={720} />
    {SHOTS.map(([name, dur, S]) => <Composition key={name} id={`Shot-${name}`} component={one(S, dur)} durationInFrames={dur} fps={30} width={1920} height={1080} />)}
  </>
);
