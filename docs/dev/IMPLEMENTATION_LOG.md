# v2 implementation log

Start point: main at 1787aa6 (audited commit 7be0be7 plus the static-export header fix). Each workstream lives on its own branch and is integrated here after review.

## Baseline
- 2026-11-02: `MUJOCO_GL=osmesa pytest -q -m 'not slow'` with the dev, video and meta extras → 496 passed, 20 deselected (189 s). Required system packages: libosmesa6, ffmpeg.

## Checklist
| Item | Workstream | Status |
|---|---|---|
| Ordering verified from execution evidence; unknown never passes | A1 | in progress |
| Rule-support matrix shared by compiler, planner, checker, UI | A1 | in progress |
| Perception observability (measured / inferred / interpolated / unobserved) | A2 | in progress |
| Demonstration lineage and immutable artifact identity | A3 | in progress |
| Upload validation, bounds, admission control, job robustness | B | in progress |
| Responsive layout 360–1920 px | C1 | in progress |
| First-visit story | C2 | in progress |
| Challenge mode (API + UI) | C3 | in progress |
| Correction capability beyond the prepared sentence | D | in progress |
| Articulated arm backend | E1 | in progress |
| Perception tracks, real-camera workflow | E2 | in progress |
| Scope wording of existing results | F1 | in progress |
| Fresh frozen protocol v3 (prepared, not run) | F2 | in progress |
| Impact study kit (not yet run) | F3 | in progress |
| Demo films rebuilt from verified behaviour | G | waiting on integration |
| Lock, CI, clean install, artifact scan | H | in progress |

## Verification log
Entries: defect → reproduction → fix commit → guard test → result.
