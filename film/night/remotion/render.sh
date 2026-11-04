#!/bin/bash
# render.sh COMP OUT [extra args]   (bundle in ./build; under the shared render lock; <=720p loops via --scale)
set -e
cd /tmp/night-motion
comp=$1; out=$2; shift 2
flock /workspace/mirror-team/v2/night/render.lock timeout 5400 npx remotion render build $comp $out --gl=swangle --concurrency=2 \
  --codec=h264 --crf=18 --pixel-format=yuv420p --color-space=bt709 --log=error "$@"
echo RENDERED $out
