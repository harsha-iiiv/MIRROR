# Install, replay without a key, and release checks

## Requirements

* Linux x86_64 (tested on Ubuntu 24.04) with Python 3.11. macOS works for the
  command line and the studio with MuJoCo's default renderer; headless
  rendering below is Linux-specific.
* System packages: `libosmesa6` (headless MuJoCo rendering), `libgl1`, and
  `ffmpeg` (with `ffprobe`; the studio probes uploaded videos and converts audio
  with it, and the video tools encode with it).
* No GPU is needed. Video benchmark renders were made on GPUs with EGL; software
  (OSMesa) renders differ by about 1/255 per pixel on average ([CLAIMS](CLAIMS.md)).

```bash
sudo apt-get install -y libosmesa6 libgl1 ffmpeg
git clone https://github.com/harsha-iiiv/MIRROR && cd MIRROR
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-lock.txt     # tested versions
pip install -e . --no-deps
python -m playwright install chromium    # only for browser tests
```

`requirements-lock.txt` pins the versions the test suite passed with
(dependencies of `pyproject.toml` with the `dev`, `video` and `meta` extras).
`pip install -e ".[dev,video,meta]"` also works but resolves current releases.

## Headless rendering

```bash
export MUJOCO_GL=osmesa            # software rendering, no display or GPU
export PYOPENGL_PLATFORM=osmesa
```

With a GPU and EGL drivers, `MUJOCO_GL=egl` is faster. Without either variable
MuJoCo tries a windowed context and fails on a server. Rendering is used for
videos and the studio's camera views; planning, simulation and checking do not
need it.

## Replay without a key

Every published result and the reference story replay from recorded model
responses; nothing calls a model.

```bash
export MIRROR_LLM_MODE=replay MIRROR_META_MODE=replay
mirror doctor          # lists the recordings that will be used
mirror story           # the whole narrative; "Interpreter: recorded responses from muse-spark-1.3 (replay, no network)"
mirror studio          # http://127.0.0.1:8000, recorded responses only
python -m pytest -q -m "not slow"
```

`MIRROR_LLM_MODE=offline` uses the deterministic rule-based interpreter instead.
A replay request with no matching recording raises an error; it never falls
back silently, and every output names the interpreter that produced it.

## Tests

| Command | What |
|---|---|
| `python -m pytest -q -m "not slow"` | fast suite |
| `python -m pytest -q -m slow` | long simulations |
| `python -m pytest -q tests/protocol_v3 tests/study` | protocol v3 harness and impact study tools |

CI (`.github/workflows/ci.yml`) runs the fast suite, a semantic regression job
(checker, compiler, planner, protocol v3 dry run) and the release scan, all in
replay mode with no key. The workflow file is in the repository; GitHub only
runs it after it is pushed by an account allowed to change workflows.

## Release checks

```bash
python scripts/release_scan.py                     # tracked files
python scripts/release_scan.py --root site         # an exported static site
```

The scan reports secrets, credentials in URLs, emails, phone numbers and home
paths with masked previews (values are never printed), and lists media files
for provenance review. It exits non-zero on a high-severity finding.

## Clean install record (2 November 2026)

From a fresh clone of the release branch into an empty directory: a new Python
3.11 virtual environment, `pip install -r requirements-lock.txt`, `pip install
-e . --no-deps`, then with an empty environment except `MUJOCO_GL=osmesa` and
replay modes: `mirror doctor` passed every check, `mirror story` completed in
25 s (Muse Spark recordings, no network), and the fast test suite was run (see
the release notes for its count).

## Media and recordings in the repository

All 290 WAV files are synthetic speech: 288 spoken-correction benchmark clips
(ElevenLabs premade voices, `results/eval_voice/design.json`), one sample
correction (`data/demos/voice/reference_correction.json`) and one espeak-ng
test clip. Videos and images are simulator renders, segmentation recordings
and charts. No recording of a person is included.
