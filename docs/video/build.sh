#!/usr/bin/env bash
# M92 V5: rebuild one episode of the "Find setups" videos after a UI change - one command.
#   docs/video/build.sh <episode> <lang> [--form desktop|phone|both] [--tts placeholder|silent|azure|sarvam|indictts] [--allow-paid]
#   e.g. docs/video/build.sh E1 en          (both cuts, offline placeholder voice)
# Outputs go to ../atp-video-out (outside git; --out or ATP_VIDEO_OUT to change). Steps and options: docs/video/README.md.
set -euo pipefail
if [ $# -lt 2 ]; then
  echo "usage: docs/video/build.sh <E1..E6> <en|mr> [options]   (see docs/video/README.md)" >&2
  exit 2
fi
exec python3 "$(cd "$(dirname "$0")" && pwd)/tools/build.py" "$@"
