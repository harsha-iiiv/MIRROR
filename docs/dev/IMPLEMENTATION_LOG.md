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

## Final acceptance matrix
| ID | Requirement | Evidence required | Status |
|---|---|---|---|
| A1 | Ordering checked from execution evidence; wrong-order historical run fails; missing evidence never passes | regression tests | in progress |
| A2 | Unobserved tilt never becomes measured upright | tests on top-only video | in progress |
| A3 | Demo A → run → demo B → run never mixes lineage | integration test | in progress |
| B | Uploads probed and decoded; storage, duration, frame and admission bounds | tests per bound | in progress |
| C | No horizontal overflow 360–1920 px; first-visit story; Challenge mode | screenshots + measured widths | in progress |
| D | Two or more correction types, scoped, multi-turn, ambiguous → question, new constraint, transfer | scripted demos with old-run failure | in progress |
| E1 | Articulated arm with IK, limits, collision, controller execution | tests + episodes | in progress |
| E2 | Separate perception provenance; calibrated real-camera workflow | tests; real footage pending | in progress |
| F1 | Existing results described at their actual scope | doc review | in progress |
| F2 | Fresh frozen authoring protocol | prepared; awaiting authors and budget | in progress |
| F3 | Impact study kit | kit ready; not yet run | in progress |
| F-A1 | Planner baseline fairness: time-calibrated objective, never-tilt baseline, rotation-cost sweep, all executed in physics | new registration results | in progress |
| F-A2 | Spec agreement labelled a regression check; separate language-understanding evaluation | protocol; awaiting independent authors and budget | in progress |
| F-A3 | Video benchmark: unconditional errors, constant-zero baseline on existing outputs; new tilted benchmark | reanalysis now; new benchmark awaiting budget | in progress |
| F-A4 | Robustness denominators corrected in a derived summary | derived summary | in progress |
| F-A5 | Holdout chronology investigated; manifests with hashes | investigation note + manifests | in progress |
| G | Social cut and judging video from verified behaviour | rendered files reviewed at phone size | waiting |
| H | Lock, CI, clean install, artifact scan | logs | in progress |

## Verification log
Entries: defect → reproduction → fix commit → guard test → result.
- F-A1/A2/A3/A5 reproduced on 1787aa6 by the coordinator: `PlannerConfig.rotation_penalty = 0.05` (mirror/plan/config.py); spec agreement requirement in mirror/evaluate/specs.py; `frame_errors` computes centroid error only for estimates within 5 cm (mirror/evaluate/bench_video.py); holdout `frozen_at` 2026-10-31T04:45:00-07:00 vs `generated_at` 2026-10-31T11:27:50 without a zone. Investigation assigned; originals untouched.
