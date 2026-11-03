# Cross-platform replay check: Muse registration on Apple arm64 (derived, development only)

Not evidence for any registration. The frozen layouts of `results/eval_muse` were replayed offline with every
network call refused on an Apple M3 Pro (macOS 26.6.2, arm64; MuJoCo 3.17.0, NumPy 2.5.4, SciPy 1.18.1,
Python 3.12 venv) at commit `5f3b0fa` (code differs from the freeze only by later commits), and compared with
the stored report (Linux x86_64, NumPy 2.4.6, Python 3.11.18). Commands:
`python -m mirror.evaluate.reanalysis.replay results/eval_muse --out <file>`, then
`python -m mirror.evaluate.reanalysis.crossplatform <mac replay> results/eval_muse/report.json --out <file>`;
layouts: `python -m mirror.evaluate.reanalysis.scenes dump|compare`.

| check | arm64 vs stored |
|---|---|
| specification pair (sha256) | identical (both versions) |
| layout sha256 | 0 of 95 identical |
| layout numbers (every pose, size and parameter) | identical in structure; largest difference 1.1e-16 (last-bit rounding of a quaternion component) |
| outcome (status, completion, violations, placement) | 95 of 95 trials identical in both conditions |
| route labels | 95 of 95 identical |
| beads kept | 52 of 95 identical; 43 differ, all in the original (uncorrected) condition where the cup spills; largest difference 2 beads |
| corrected condition | every trial keeps 8 of 8 beads on both platforms |
| largest final-tilt difference | 0.048 deg |
| largest peak transport-tilt difference | 0.226 deg |
| largest executed-time difference | 0.832 s (208 physics steps) |
| blocked reference case | same statuses (original succeeds, corrected refused as infeasible) |

Reading: the layout hash is computed over exact floating-point values, so last-bit rounding differences between
platforms change every hash even though the layouts agree to about 1e-16. From there the physics diverges
slightly (sub-degree tilts, under a second of executed time) and chaotically where beads spill, so bead counts
in spilling runs differ by up to 2. Every registered verdict (status, completion, violation-free or not) is
the same. Cross-platform reproduction is therefore at the outcome level, like the cross-machine Linux replay
in `../replay/`, not bit-exact. Object positions are not stored per trial, so position differences are not
reported here; tilts and executed time are the continuous quantities the stored reports carry.

Files: `eval_muse_mac_arm64.json` (arm64 replay), `mac_arm64_vs_stored.json` (per-trial comparison incl. every
bead difference), `scenes_mac_arm64_vs_linux.json` (layout comparison; the Linux dump reproduces all 95 stored
layout hashes).
