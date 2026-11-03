# Demo films

Two cuts are built from recorded MuJoCo rollouts; no frame is animated by hand, interpolated or generated.

| Cut | Size | Length | Purpose |
|---|---|---|---|
| social | 1080x1920 | about 33 s | phone feed; text kept between y 290 and 1240 |
| judging | 1920x1080 | about 2 min 10 s | submission video |

Headline: corrections you can check. A correction is one sentence that becomes a scoped rule change, checked three
ways: the old run is re-judged from its recorded physics log; the corrected rule is planned and run on new layouts;
when no compliant route exists, the planner refuses with a proof instead of spilling.

## What the films show, and what they do not

- Every robot image is a recorded rollout drawn by `mirror/sim/render.py` (recorded poses only). Slow and fast
  replays are labelled on screen (`replay 0.75×`, `6× speed`).
- Only reference scenes and development layouts appear (blocked 6400-6402, mixed 6200-6202, shortcut 6300-6302). No
  evaluation layout is generated or shown.
- The planner rotation price is the time-calibrated 0.2445 m/rad from `results/eval_objective_v1`, except one
  labelled comparison at 0.05 m/rad ("planner set to treat tilting as nearly free"). At 0.2445 the upright
  correction does not change behaviour on layouts that have an upright route; the films say so.
- The blocked-layout result holds at both prices: the original specification completes and loses beads, the
  corrected one plans nothing and carries a proven-infeasibility certificate (quoted with its assumptions).
- Ordering and clearance corrections come from `scripts/demo_corrections.py` run at 0.2445 m/rad (offline rule
  interpreter, development layouts). They are demonstrations, not an evaluation.
- The specification pair is built by replaying the recorded Muse Spark responses (`data/cassettes_muse`); the
  offline interpreter produces the same enforced rules. No model is called while building the films.
- No narration and no human voice: captions only, over a quiet synthesized pad written by `build.py`.

## Rebuilding

```
python scripts/film/make_sources.py --out <sources>                       # about 3 min on 4 cores
python scripts/demo_corrections.py --out <demos> --rotation-penalty 0.2445 \
    --cases ordering,clearance,blocked_refusal,ambiguous,unsupported
python scripts/film/build.py --sources <sources> --demos <demos> --out <film> [--cut social|judging]
python scripts/film/check.py <film> social     # contact sheet + motion check
python scripts/film/shotlist.py <film> social  # shot list with the source of every shot
```

`build.py --sample 0.2,0.6,0.97` renders three frames per shot for a quick review. Fonts: Roboto and DejaVu Sans
Mono (`--font-dir` if they are not installed). Rendering needs an offscreen OpenGL backend (OSMesa on Linux, CGL on
macOS); see INSTALL.md.

Physics differs slightly between platforms (bead counts can differ on macOS arm64), so the sources are recorded once
and only rendered elsewhere. Each shot list names the platform and commit of its sources.

## Checks

`check.py` writes `motion_<cut>.json`: per shot, the number of camera moves (at most one eased move, then a hold),
reversals, per-frame zoom from the camera log, and the per-frame image scale change of the simulation area measured
from the pixels (ORB features + RANSAC similarity). Thresholds: one move per shot, zoom below 0.3% per frame, image
scale change below 1% per frame.
