#!/usr/bin/env bash
# M92: install the free, local voices for the tutorial videos - AI4Bharat Indic-TTS (FastPitch + HiFi-GAN, MIT
# licence): Marathi (mr) and Indian English (en), two speakers each: female and male. CPU only, no key, no network once
# installed.
#
#   docs/video/setup_indictts.sh [DIR]        (DIR, or ATP_INDICTTS_DIR, default ../atp-indictts next to the repo)
#   ATP_INDICTTS_LANGS="mr en" docs/video/setup_indictts.sh DIR      (the languages; default: mr)
#
# DIR gets: venv/ (Python with coqui-tts and torch from PyPI) and one model folder per language (mr/, en/: fastpitch/ +
# hifigan/). About 1.6 GB per model and about 6 GB for the venv: put DIR on a disk with room (e.g. /dev/shm).
# Nothing here goes into git. Then: ATP_INDICTTS_DIR=DIR docs/video/build.sh E1 mr --tts indictts
# Remove with: rm -rf DIR
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-${ATP_INDICTTS_DIR:-$(cd "$HERE/../../.." && pwd)/atp-indictts}}"
# The models: the checkpoints of the AI4Bharat Indic-TTS v1 release on GitHub, <lang>.zip, each checked by its SHA-256.
LANGS="${ATP_INDICTTS_LANGS:-mr}"
RELEASE="https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release"
sha_of() {
  case "$1" in
    mr) echo d4457b2b9d2604396ab214d6ef8b3d4689f3bfd1312814ca295f44af5def90a0 ;;
    en) echo df2703987372d3637a60a00705f5f43c9642fa794bc22ad13fb17b7173a3889d ;;
    *) echo "[indictts] no checksum known for '$1'" >&2; return 1 ;;
  esac
}
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

for lang in $LANGS; do
  want="$(sha_of "$lang")"
  if [ ! -f "$DIR/$lang/fastpitch/best_model.pth" ] || [ ! -f "$DIR/$lang/hifigan/best_model.pth" ]; then
    echo "[indictts] download $RELEASE/$lang.zip (1.5 GB)"
    curl -fL --retry 3 -o "$DIR/$lang.zip.part" "$RELEASE/$lang.zip"
    got="$(sha256sum "$DIR/$lang.zip.part" | cut -d' ' -f1)"
    if [ "$got" != "$want" ]; then
      echo "[indictts] checksum mismatch for $lang: $got (expected $want)" >&2
      rm -f "$DIR/$lang.zip.part"
      exit 1
    fi
    rm -rf "${DIR:?}/$lang"
    (cd "$DIR" && unzip -q "$lang.zip.part" && rm -f "$lang.zip.part")
    # The released checkpoints carry the training optimizer state too (about 1 GB per model); only the weights are
    # needed to speak, so keep those: half the disk and memory (a worker loads its whole checkpoint into memory).
    "$DIR/venv/bin/python" -I -c 'import sys, torch
for f in sys.argv[1:]:
    c = torch.load(f, map_location="cpu", weights_only=False)
    torch.save({k: v for k, v in c.items() if k not in ("optimizer", "scaler")}, f)' \
      "$DIR/$lang/fastpitch/best_model.pth" "$DIR/$lang/hifigan/best_model.pth"
  fi
done
rm -rf "$TMPDIR"
for lang in $LANGS; do
  "$DIR/venv/bin/python" -I "$HERE/tools/indictts_worker.py" --check "$DIR/$lang"
done
echo "[indictts] ready: ATP_INDICTTS_DIR=$DIR"
