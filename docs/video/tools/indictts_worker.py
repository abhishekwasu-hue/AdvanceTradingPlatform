#!/usr/bin/env python3
"""M92: the local voice (AI4Bharat Indic-TTS, FastPitch + HiFi-GAN; one model per language: Marathi, Indian English),
run inside its own venv by tools/tts.py.

The model is loaded once; then one request per line on stdin, one answer per line on stdout:

    {"text": "...", "speaker": "female" | "male", "out": "/path/clip.wav", "pace": 1.0}
    -> {"ok": true, "seconds": 4.2, "unknown": []}      (unknown = characters the model cannot read, dropped by it)

    python -I indictts_worker.py --check MODEL_DIR       (load the model, say one word, exit)

CPU only; no key, no network. Everything the library prints goes to stderr, so stdout carries only the answers.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SPEAKERS = ("female", "male")


def load(model_dir: Path):
    import torch
    from TTS.utils.synthesizer import Synthesizer

    torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
    fp, hg = model_dir / "fastpitch", model_dir / "hifigan"
    cfg = json.loads((fp / "config.json").read_text(encoding="utf-8"))
    # The released config points at the training machine's paths; point it at this copy (a temporary file, the model
    # folder stays as downloaded).
    speakers = str(fp / "speakers.pth")
    cfg["speakers_file"] = speakers
    cfg.setdefault("model_args", {})["speakers_file"] = speakers
    tmp = Path(tempfile.mkdtemp(prefix="indictts-"))
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    synth = Synthesizer(tts_checkpoint=str(fp / "best_model.pth"), tts_config_path=str(tmp / "config.json"),
                        tts_speakers_file=speakers, vocoder_checkpoint=str(hg / "best_model.pth"),
                        vocoder_config=str(hg / "config.json"), use_cuda=False)
    chars = cfg["characters"]
    known = set(chars.get("characters") or "") | set(chars.get("punctuations") or "")
    return synth, known


def say(synth, known: set, text: str, speaker: str, out: str, pace: float = 1.0) -> dict:
    if speaker not in SPEAKERS:
        return {"ok": False, "error": f"unknown speaker {speaker!r}; one of {', '.join(SPEAKERS)}"}
    synth.tts_model.length_scale = float(pace)          # FastPitch durations x pace (above 1 = slower)
    unknown = sorted({c for c in text if c not in known and not c.isspace()})
    wav = synth.tts(text, speaker_name=speaker)
    synth.save_wav(wav, out)
    return {"ok": True, "seconds": len(wav) / float(synth.output_sample_rate), "unknown": unknown}


def main() -> int:
    answers = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)                                    # the library's own prints go to stderr
    sys.stdout = sys.stderr
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        synth, known = load(Path(sys.argv[2]))
        with tempfile.TemporaryDirectory() as d:
            # The check line: the education line of the model's language (scripts/rules.json; the folder is named mr / en).
            rules = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "rules.json").read_text(encoding="utf-8"))
            word = rules.get(Path(sys.argv[2]).name, rules["en"])["end_line"]
            r = say(synth, known, word, "female", str(Path(d) / "check.wav"))
        print(f"[indictts] model OK ({r['seconds']:.1f} s test clip)", file=sys.stderr)
        return 0
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    synth, known = load(Path(sys.argv[1]))
    answers.write(json.dumps({"ok": True, "ready": True}) + "\n")
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            req = json.loads(raw)
            res = say(synth, known, req["text"], req.get("speaker", "female"), req["out"], req.get("pace", 1.0))
        except Exception as e:                       # one bad line must not kill the worker; the caller decides
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        answers.write(json.dumps(res, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
