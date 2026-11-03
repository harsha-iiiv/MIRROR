"""Measure the studio layout at several viewport widths and save screenshots.

    python tests/web/measure_layout.py --data <exported site>/data --out tests/web/artifacts/<label>

Writes one PNG per case and measurements.json: document and main widths,
elements wider than the viewport, and controls cut off at the right edge.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.web.harness import StaticServer, build_site  # noqa: E402

WIDTHS = [360, 390, 768, 1280, 1920]
HEIGHT = {360: 740, 390: 844, 768: 1024, 1280: 800, 1920: 1080}

PROBE = """() => {
  const vw = window.innerWidth;
  const de = document.documentElement;
  const main = document.querySelector('.main');
  const wide = [];
  const cut = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (r.width > vw + 1) wide.push({ el: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : ''), w: Math.round(r.width) });
    const interactive = el.matches('button, a[href], input, select, textarea, [role=switch]');
    if (interactive && (r.right > vw + 1 || r.left < -1)) cut.push({ el: el.tagName.toLowerCase(), text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40), left: Math.round(r.left), right: Math.round(r.right) });
  }
  const canvas = document.querySelector('.viewer canvas');
  const cr = canvas ? canvas.getBoundingClientRect() : null;
  return {
    viewport: vw,
    documentScrollWidth: de.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    mainWidth: main ? Math.round(main.getBoundingClientRect().width) : null,
    canvas: cr ? { w: Math.round(cr.width), h: Math.round(cr.height), right: Math.round(cr.right) } : null,
    widestOverflowing: wide.sort((a, b) => b.w - a.w).slice(0, 8),
    controlsOutside: cut.slice(0, 20),
  };
}"""


def run(data: Path, out: Path, widths=WIDTHS, themes=("light", "dark"), extra_cases=True) -> list[dict]:
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        site = build_site(Path(tmp) / "site", data)
        with StaticServer(site) as srv, sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
            for theme in themes:
                for w in widths:
                    cases = [("fresh", None)]
                    if extra_cases:
                        cases += [("about", "About"), ("models", "Models")]
                    for case, button in cases:
                        ctx = browser.new_context(viewport={"width": w, "height": HEIGHT.get(w, 900)})
                        ctx.add_init_script(f"localStorage.setItem('mirror-theme', '{theme}')")
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        page.goto(srv.url, wait_until="networkidle")
                        page.wait_for_selector(".main", timeout=30000)
                        page.wait_for_timeout(1500)
                        if button:
                            page.locator(".top-right .iconbtn", has_text=button).first.click()
                            page.wait_for_timeout(300)
                        m = page.evaluate(PROBE)
                        m.update({"theme": theme, "case": case, "errors": errors})
                        shot = out / f"{theme}-{w}-{case}.png"
                        page.screenshot(path=str(shot), full_page=False)
                        m["screenshot"] = shot.name
                        results.append(m)
                        ctx.close()
            browser.close()
    (out / "measurements.json").write_text(json.dumps(results, indent=1))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--widths", default=",".join(map(str, WIDTHS)))
    ap.add_argument("--quick", action="store_true", help="fresh load only, light theme")
    a = ap.parse_args(argv)
    widths = [int(x) for x in a.widths.split(",")]
    res = run(a.data, a.out, widths, ("light",) if a.quick else ("light", "dark"), not a.quick)
    for m in res:
        print(f"{m['theme']:5} {m['viewport']:5} {m['case']:7} doc={m['documentScrollWidth']:5} main={m['mainWidth']} "
              f"outside={len(m['controlsOutside'])} errors={len(m['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
