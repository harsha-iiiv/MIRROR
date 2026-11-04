"""Screenshots of the 3D workspace by stepping time through the viewer API (SwiftShader).
usage: viz_shots.py OUTDIR name=URLQUERY[@WxH][#js] ..."""
import sys, json, time
from playwright.sync_api import sync_playwright

out = sys.argv[1]
specs = sys.argv[2:]
BASE = "http://127.0.0.1:8765/js/viz/lab.html?quality=high&"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    for s in specs:
        name, rest = s.split("=", 1)
        js = None
        if "#" in rest:
            rest, js = rest.split("#", 1)
        size = "1440x900"
        if "@" in rest:
            rest, size = rest.rsplit("@", 1)
        w, h = map(int, size.split("x"))
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                            color_scheme="dark" if "theme=dark" in rest else "light")
        pg = ctx.new_page()
        logs = []
        pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        pg.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        pg.goto(BASE + rest, timeout=60000)
        try:
            pg.wait_for_function("window.__lab && (window.__lab.ready || window.__lab.error)", timeout=90000)
        except Exception as e:
            logs.append(f"timeout: {e}")
        pg.evaluate("() => { window.__lab.v.frozen = true; }")
        if js:
            pg.evaluate(js)
        pg.wait_for_timeout(600)
        pg.evaluate("() => { const v = window.__lab.v; for (let i = 0; i < 40; i++) { v.glide.step(0.05); v._stepPhoneGlide(0.05); } v.rules.update(v.t, v.frameIndex(), v.bodies, 0); if (v.view !== 'phone') v.controls.update(); v._render(); }")
        pg.wait_for_timeout(400)
        pg.screenshot(path=f"{out}/{name}.png", timeout=120000)
        st = pg.evaluate("() => window.__lab.v.getState ? window.__lab.v.getState() : null")
        print(name, json.dumps(st)[:400]); print("   render", json.dumps((st or {}).get("render")))
        for l in logs[-8:]:
            print("   ", l[:300])
        ctx.close()
    b.close()
