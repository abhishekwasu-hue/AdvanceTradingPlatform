#!/usr/bin/env bash
# M92: install the free, local Marathi voice for the tutorial videos - AI4Bharat Indic-TTS (FastPitch + HiFi-GAN,
# MIT licence), two speakers: female and male. CPU only, no key, no network once installed.
#
#   docs/video/setup_indictts.sh [DIR]        (DIR, or ATP_INDICTTS_DIR, default ../atp-indictts next to the repo)
#
# DIR gets: venv/ (Python with coqui-tts and torch from PyPI) and mr/ (the model: fastpitch/ + hifigan/).
# About 1.6 GB for the model and about 6 GB for the venv: put DIR on a disk with room (e.g. /dev/shm/atp-indictts).
# Nothing here goes into git. Then: ATP_INDICTTS_DIR=DIR docs/video/build.sh E1 mr --tts indictts
# Remove with: rm -rf DIR
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-${ATP_INDICTTS_DIR:-$(cd "$HERE/../../.." && pwd)/atp-indictts}}"
# The model: the Marathi checkpoints of the AI4Bharat Indic-TTS v1 release on GitHub (the source used for the samples).
MODEL_URL="https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release/mr.zip"
MODEL_SHA256="${ATP_INDICTTS_SHA256:-d4457b2b9d2604396ab214d6ef8b3d4689f3bfd1312814ca295f44af5def90a0}"
# The versions the voice was made with (PyPI only; torch's own download host is not needed). torch from PyPI brings
# its CUDA libraries too (about 5 GB); they are not used - the voice runs on the CPU.
PACKAGES=("coqui-tts[codec]==0.28.0" "torch==2.15.0" "torchaudio==2.11.1" "torchcodec==0.17.0" "six")

mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"
export TMPDIR="$DIR/tmp"            # pip and unzip unpack here, not on the system disk
mkdir -p "$TMPDIR"

if [ ! -x "$DIR/venv/bin/python" ]; then
  echo "[indictts] venv in $DIR/venv"
  python3 -m venv "$DIR/venv"
fi
if ! "$DIR/venv/bin/python" -c "import TTS.utils.synthesizer" 2>/dev/null; then
  echo "[indictts] pip install ${PACKAGES[*]}"
  "$DIR/venv/bin/pip" install --no-cache-dir --quiet --upgrade pip
  "$DIR/venv/bin/pip" install --no-cache-dir --quiet "${PACKAGES[@]}"
fi

if [ ! -f "$DIR/mr/fastpitch/best_model.pth" ] || [ ! -f "$DIR/mr/hifigan/best_model.pth" ]; then
  echo "[indictts] download $MODEL_URL (1.5 GB)"
  curl -fL --retry 3 -o "$DIR/mr.zip.part" "$MODEL_URL"
  got="$(sha256sum "$DIR/mr.zip.part" | cut -d' ' -f1)"
  if [ "$got" != "$MODEL_SHA256" ]; then
    echo "[indictts] checksum mismatch: $got (expected $MODEL_SHA256)" >&2
    rm -f "$DIR/mr.zip.part"
    exit 1
  fi
  rm -rf "$DIR/mr"
  (cd "$DIR" && unzip -q mr.zip.part && rm -f mr.zip.part)
fi
rm -rf "$TMPDIR"
"$DIR/venv/bin/python" -I "$HERE/tools/indictts_worker.py" --check "$DIR/mr"
echo "[indictts] ready: ATP_INDICTTS_DIR=$DIR"
