import React from 'react';
import {Composition, Sequence, AbsoluteFill, useCurrentFrame, interpolate} from 'remotion';
import {loadFonts} from './fonts';
import {C} from './theme';
import {Title, Watch, Gate} from './scenes1';
import {Run, Sentence, Rejudge, Table, End, Twin} from './scenes2';

loadFonts();

// each shot fades in from the background and out to it (no zoom, no whip, no shake)
const Shot: React.FC<{dur: number; children: React.ReactNode; fin?: number; fout?: number}> = ({dur, children, fin = 10, fout = 10}) => {
  const f = useCurrentFrame();
  const o = Math.min(interpolate(f, [0, fin], [0, 1], {extrapolateRight: 'clamp'}), interpolate(f, [dur - fout, dur], [1, 0], {extrapolateLeft: 'clamp'}));
  return <AbsoluteFill style={{background: C.bg}}><AbsoluteFill style={{opacity: o}}>{children}</AbsoluteFill></AbsoluteFill>;
};

const WatchF: React.FC<{dur: number}> = ({dur}) => <Watch dur={dur} src0={12.8} rate={1.6} />;
const RunF: React.FC<{dur: number}> = ({dur}) => <Run dur={dur} speed={6} lead={24} />;
export const SHOTS: [string, number, React.FC<{dur: number}>][] = [
  ['title', 120, Title], ['watch', 360, WatchF], ['gate', 240, Gate], ['run', 420, RunF], ['twin', 180, Twin],
  ['sentence', 240, Sentence], ['rejudge', 285, Rejudge], ['table', 255, Table], ['end', 120, End],
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

const Loop: React.FC<{children: React.ReactNode}> = ({children}) => <Shot dur={240} fin={12} fout={12}>{children}</Shot>;
const LoopWatch = () => <Loop><Watch dur={240} src0={14.0} rate={2} /></Loop>;
const LoopRun = () => <Loop><Run dur={240} speed={9.5} lead={2} /></Loop>;
const LoopRejudge = () => <Loop><Rejudge dur={240} tOld={8} tNew={90} /></Loop>;
const LoopSentence = () => <Loop><Sentence dur={240} /></Loop>;

// single-shot compositions for quick stills / review
const one = (S: React.FC<{dur: number}>, dur: number) => () => <S dur={dur} />;

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Film" component={Film} durationInFrames={FILM_FRAMES} fps={30} width={1920} height={1080} />
    <Composition id="LoopWatch" component={LoopWatch} durationInFrames={240} fps={30} width={1920} height={1080} />
    <Composition id="LoopRun" component={LoopRun} durationInFrames={240} fps={30} width={1920} height={1080} />
    <Composition id="LoopRejudge" component={LoopRejudge} durationInFrames={240} fps={30} width={1920} height={1080} />
    <Composition id="LoopSentence" component={LoopSentence} durationInFrames={240} fps={30} width={1920} height={1080} />
    {SHOTS.map(([name, dur, S]) => <Composition key={name} id={`Shot-${name}`} component={one(S, dur)} durationInFrames={dur} fps={30} width={1920} height={1080} />)}
  </>
);
