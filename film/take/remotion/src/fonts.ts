import {continueRender, delayRender, staticFile} from 'remotion';
const faces: [string, string, string][] = [
  ['Inter', 'fonts/inter-latin-400-normal.woff2', '400'],
  ['Inter', 'fonts/inter-latin-500-normal.woff2', '500'],
  ['Inter', 'fonts/inter-latin-600-normal.woff2', '600'],
  ['JetBrains Mono', 'fonts/jetbrains-mono-latin-400-normal.woff2', '400'],
  ['JetBrains Mono', 'fonts/jetbrains-mono-latin-500-normal.woff2', '500'],
];
let started = false;
export const loadFonts = () => {
  if (started || typeof document === 'undefined') return;
  started = true;
  const h = delayRender('fonts');
  Promise.all(faces.map(([fam, src, w]) => {
    const ff = new FontFace(fam, `url(${staticFile(src)}) format('woff2')`, {weight: w});
    return ff.load().then((x) => (document as any).fonts.add(x));
  })).then(() => continueRender(h)).catch((e) => { console.error(e); continueRender(h); });
};
