#!/bin/bash
# render.sh COMP OUT [extra args]   (run in the Remotion work dir with the bundle in ./build; <=720p loops via --scale)
# Set MIRROR_RENDER_LOCK to a file to serialise renders with other heavy jobs (flock).
set -e
comp=$1; out=$2; shift 2
ARGS=(timeout 5400 npx remotion render build "$comp" "$out" --gl=swangle --concurrency=2 \
  --codec=h264 --crf=18 --pixel-format=yuv420p --color-space=bt709 --log=error "$@")
if [ -n "${MIRROR_RENDER_LOCK:-}" ]; then flock "$MIRROR_RENDER_LOCK" "${ARGS[@]}"; else "${ARGS[@]}"; fi
echo RENDERED $out
