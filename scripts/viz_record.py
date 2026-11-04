"""Short screen recording of the 3D workspace by stepping time through the viewer API (works on
software GL: each frame is rendered and captured, then encoded at a fixed rate).
usage: viz_record.py OUT.mp4 QUERY --t0 6 --fps 15 --frames 90 [--speed 1] [--at FRAME:JS ...] [--size 1280x720]"""
import argparse, subprocess, tempfile, json
from pathlib import Path
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("out"); ap.add_argument("query")
ap.add_argument("--t0", type=float, default=0); ap.add_argument("--fps", type=int, default=15)
ap.add_argument("--frames", type=int, default=90); ap.add_argument("--speed", type=float, default=1.0)
ap.add_argument("--at", action="append", default=[]); ap.add_argument("--size", default="1280x720")
a = ap.parse_args()
w, h = map(int, a.size.split("x"))
acts = {}
for s in a.at:
    k, js = s.split(":", 1); acts.setdefault(int(k), []).append(js)
tmp = Path(tempfile.mkdtemp())
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": w, "height": h})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8765/js/viz/lab.html?quality=high&still&" + a.query)
    pg.wait_for_function("window.__lab && (window.__lab.ready || window.__lab.error)", timeout=120000)
    pg.evaluate("() => { window.__lab.v.frozen = true; }")
    dt = 1.0 / a.fps
    for i in range(a.frames):
        for js in acts.get(i, []):
            pg.evaluate(js)
        pg.evaluate(f"""() => {{ const v = window.__lab.v; v.setTime({a.t0} + {i} * {dt} * {a.speed});
            v.glide.step({dt}); v._stepPhoneGlide({dt}); if (v.view === 'follow') v._follow({dt});
            v.rules.update(v.t, v.frameIndex(), v.bodies, {i} * {dt} * 1000);
            if (v.view !== 'phone') v.controls.update(); v._render(); }}""")
        pg.screenshot(path=str(tmp / f"f{i:04d}.png"), timeout=120000)
    print("errors", errs)
    b.close()
subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(a.fps), "-i", str(tmp / "f%04d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-movflags", "+faststart", "-crf", "20", a.out], check=True, timeout=600, stdin=subprocess.DEVNULL)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp / f"f{a.frames // 2:04d}.png"), str(Path(a.out).with_suffix(".mid.png"))], timeout=60, stdin=subprocess.DEVNULL)
print("wrote", a.out)
