// node stills.mjs Comp:frame [Comp:frame ...]  (uses ./build, one browser)
import {openBrowser, selectComposition, renderStill} from '@remotion/renderer';
import path from 'path';
const serveUrl = path.resolve('build');
const browser = await openBrowser('chrome', {chromiumOptions: {gl: 'swangle'}});
const cache = {};
for (const cf of process.argv.slice(2)) {
  const [id, fr] = cf.split(':');
  try {
    const t0 = Date.now();
    cache[id] ??= await selectComposition({serveUrl, id, puppeteerInstance: browser, chromiumOptions: {gl: 'swangle'}});
    await renderStill({composition: cache[id], serveUrl, frame: Number(fr), output: `out/stills/${id}_${fr}.png`, puppeteerInstance: browser,
      chromiumOptions: {gl: 'swangle'}, timeoutInMilliseconds: 240000});
    console.log('ok', cf, Date.now() - t0, 'ms');
  } catch (e) { console.log('FAIL', cf, String(e).slice(0, 300)); }
}
await browser.close({silent: true});
