"""Platform diagnostics for portability fixes (development only)."""
import hashlib, io, json, platform, subprocess, sys, tempfile, zlib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
out = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0]}
import PIL, numpy as np
from PIL import Image, features
out["pillow"] = PIL.__version__; out["zlib"] = zlib.ZLIB_VERSION; out["freetype"] = features.check("freetype2")
out["pillow_zlib"] = features.version("zlib") if hasattr(features, "version") else None
import httpx
from mirror.interpret import sam_prompts as sp
from mirror.meta import MetaClient
from mirror.meta.cassette import normalize, request_key
FX = ROOT / "tests" / "meta" / "fixtures"
cl = MetaClient(mode="replay", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("net"))))
ps = sp.choose_prompts(FX / "ref_top_frame0.png", "muse-spark-1.3", cl)
cand = [c for c in ps.choices if c.ok and c.box]
frame = sp.first_frame(FX / "ref_top_frame0.png")
png = sp._outlined(frame, cand)
out["frame_sha"] = hashlib.sha256(frame).hexdigest()[:16]
out["outlined_png_sha"] = hashlib.sha256(png).hexdigest()[:16]
px = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
out["outlined_pixels_sha"] = hashlib.sha256(px.tobytes()).hexdigest()[:16]
out["boxes"] = [c.box for c in cand]
seen = {}
orig = cl._cached
def spy(model, endpoint, request, network, *, name, reuse=True):
    seen["key"] = request_key(model, endpoint, normalize(request, cl._files))[:12]
    return orig(model, endpoint, request, network, name=name, reuse=reuse)
cl._cached = spy
try:
    sp.verify_boxes(cl, "muse-spark-1.3", frame, cand)
    out["verify"] = "hit"
except Exception as e:
    out["verify"] = type(e).__name__
out["verify_key"] = seen.get("key")
# video slowing
from mirror.interpret import video_observe as vo
clip = FX / "keyframes_clip.mp4"
def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate,avg_frame_rate,nb_frames,duration:format=duration", "-of", "json", str(p)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return json.loads(r.stdout or "{}")
out["ffmpeg"] = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
out["clip"] = probe(clip)
with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
    f.write(vo._slowed(clip, 4.0)); f.flush()
    out["slowed"] = probe(f.name)
    out["slowed_video_duration"] = vo.video_duration(f.name)
import mujoco, os
out["mujoco"] = mujoco.__version__; out["MUJOCO_GL"] = os.environ.get("MUJOCO_GL")
print("DIAG " + json.dumps(out))
