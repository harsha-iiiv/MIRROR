#!/bin/bash
# One command for the night film from a capture run (or the D1 phase-2 artefacts).
#   film/night/make_film.sh --pipeline <real_run out dir> --out <dir> [--remotion /tmp/night-motion] [--twin <twin dir>] [--spark spark.json] [--loops]
#   film/night/make_film.sh --d1 --out <dir> [...]
# Steps: correction sources (offline, ~25 s) -> assets -> Remotion bundle -> film (+ loops) under the shared render lock ->
#        motion check + contact sheet -> pad + cues + stills -> shot list. No model calls.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
PY=${PY:-/workspace/mirror/.venv/bin/python}; REM=/tmp/night-motion; LOCK=/workspace/mirror-team/v2/night/render.lock
PIPE=""; D1=0; OUT=""; TWIN=""; SPARK=""; LOOPS=0; NOTE=""
while [ $# -gt 0 ]; do case "$1" in
  --pipeline) PIPE="$2"; shift 2;; --d1) D1=1; shift;; --out) OUT="$2"; shift 2;; --remotion) REM="$2"; shift 2;;
  --twin) TWIN="$2"; shift 2;; --spark) SPARK="$2"; shift 2;; --loops) LOOPS=1; shift;; --gate-note) NOTE="$2"; shift 2;;
  *) echo "unknown $1"; exit 2;; esac; done
[ -n "$OUT" ] || { echo "--out required"; exit 2; }
mkdir -p "$OUT"; export PYTHONPATH="$ROOT" MIRROR_LLM_MODE=offline MUJOCO_GL=${MUJOCO_GL:-osmesa}
T0=$(date +%s)
if [ ! -d "$REM/node_modules" ]; then
  mkdir -p "$REM"; cp -r "$HERE/remotion/." "$REM/"; (cd "$REM" && npm install --no-audit --no-fund && npx remotion browser ensure && bash setup_fonts.sh)
fi
rm -rf "$REM/src"; cp -r "$HERE/remotion/src" "$REM/src"; mkdir -p "$REM/public" "$REM/out"
[ -d "$REM/public/fonts" ] || (cd "$REM" && bash setup_fonts.sh)
EXTRA=()
[ -n "$TWIN" ] && EXTRA+=(--twin "$TWIN"); [ -n "$SPARK" ] && EXTRA+=(--spark "$SPARK"); [ -n "$NOTE" ] && EXTRA+=(--gate-note "$NOTE")
if [ -n "$PIPE" ]; then
  "$PY" "$HERE/make_correction.py" --pipeline "$PIPE" --out "$OUT/corr"
  [ -z "$TWIN" ] && [ -f "$PIPE/twin/twin.json" ] && EXTRA+=(--twin "$PIPE/twin")
  "$PY" "$HERE/export_assets.py" --pipeline "$PIPE" --corr "$OUT/corr" --out "$REM/public" "${EXTRA[@]}"
else
  P2=/workspace/mirror-team/v2/phone_real/out/phase2; CAP=/cloudspace/mirror/capture/D1/real-demo
  "$PY" "$HERE/make_correction.py" --phase2 "$P2" --out "$OUT/corr"
  "$PY" "$HERE/export_assets.py" --capture "$CAP" --phase2 "$P2" --corr "$OUT/corr" --take /workspace/mirror-team/v2/phone_real/D1/IMG_0815.MOV \
     --session /workspace/mirror-team/v2/phone_real/D1/session.json --out "$REM/public" "${EXTRA[@]}"
fi
T1=$(date +%s)
(cd "$REM" && rm -rf build && npx remotion bundle src/index.ts --out-dir build --log=error)
REN=(--gl=swangle --concurrency=2 --codec=h264 --crf=18 --pixel-format=yuv420p --color-space=bt709 --log=error)
(cd "$REM" && flock "$LOCK" timeout 5400 npx remotion render build Film "$OUT/film_raw.mp4" "${REN[@]}")
if [ "$LOOPS" = 1 ]; then for L in LoopWatch LoopRun LoopGhosts LoopRejudge; do
  (cd "$REM" && flock "$LOCK" timeout 1800 npx remotion render build $L "$OUT/loop_${L#Loop}.mp4" "${REN[@]}"); done; fi
T2=$(date +%s)
"$PY" - "$REM/public/data.json" "$HERE/shots_film.json" "$OUT/shots.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])); s = json.load(open(sys.argv[2]))
if not d.get("twin"): s = [x for x in s if x["name"] != "twin"]
json.dump(s, open(sys.argv[3], "w"), indent=1)
EOF
"$PY" "$HERE/check_film.py" "$OUT/film_raw.mp4" --shots "$OUT/shots.json" --out "$OUT/check"
"$PY" "$HERE/finalize.py" --video "$OUT/film_raw.mp4" --shots "$OUT/shots.json" --out "$OUT"
"$PY" "$HERE/shotlist.py" --data "$REM/public/data.json" --shots "$OUT/shots.json" --motion "$OUT/check/motion.json" --out "$OUT/SHOTLIST.md"
cp "$REM/public/data.json" "$OUT/data.json"
echo "{\"sources_s\": $((T1-T0)), \"render_s\": $((T2-T1)), \"total_s\": $(( $(date +%s)-T0 ))}" > "$OUT/timings.json"
echo "film ready: $OUT/mirror_night_film_v3.mp4 ($(cat "$OUT/timings.json"))"
