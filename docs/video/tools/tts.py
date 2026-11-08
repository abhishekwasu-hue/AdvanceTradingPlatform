#!/usr/bin/env python3
"""M92 V3: the voice of the "Find setups" videos - one adapter, several providers.

    placeholder  espeak-ng, offline and free (a robotic voice: for checking timing and the pipeline, never for publishing)
    silent       a silent track timed from the text length (when espeak-ng is not installed)
    azure        Azure AI Speech neural voices (mr-IN-AarohiNeural / mr-IN-ManoharNeural, en-IN) - PAID
    sarvam       Sarvam AI Bulbul (Indian languages) - PAID

A paid provider runs only with BOTH its key in the environment AND `--allow-paid` on the command line; without either it
reports itself unavailable and nothing is sent. No key, address or token is ever written to the repository or the logs.
The owner picks the service (owner order M92); until then only `placeholder` / `silent` run.

Every clip is cached by (provider, voice, language, text): a rebuild after a UI change re-uses the stored voice and calls
no service again (the cache lives with the outputs, outside git).

    python3 docs/video/tools/tts.py count            # characters per episode and language (the cost basis, TTS_COST.md)
    python3 docs/video/tools/tts.py providers        # which providers can run here, and why not
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LANGS = ("en", "mr")
LOCALE = {"en": "en-IN", "mr": "mr-IN"}


class TtsUnavailable(RuntimeError):
    """The provider cannot run here (no key, no tool, or a paid call without --allow-paid)."""


class Provider:
    """One text-to-speech service. `synth` writes a mono 16-bit PCM WAV and returns its length in seconds."""
    name = "base"
    paid = False

    def voice(self, lang: str) -> str:
        return lang

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        raise NotImplementedError

    def synth(self, text: str, lang: str, out: Path) -> float:
        raise NotImplementedError


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _silence(out: Path, seconds: float, rate: int = 24000) -> float:
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return seconds


class SilentTiming(Provider):
    """No voice: a silent clip as long as the line would take to read (for timing and subtitles only)."""
    name = "silent"
    RATE = {"en": 15.0, "mr": 13.0}            # characters per second of calm speech

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        return True, "always (silent track)"

    def synth(self, text: str, lang: str, out: Path) -> float:
        return _silence(out, max(1.2, len(text) / self.RATE.get(lang, 14.0)))


class EspeakPlaceholder(Provider):
    """espeak-ng (offline, free). A placeholder voice to check the pipeline and the timing; never for publishing."""
    name = "placeholder"
    VOICES = {"en": "en-gb", "mr": "mr"}

    def voice(self, lang: str) -> str:
        return "espeak-ng:" + self.VOICES[lang]

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        return (True, "espeak-ng found") if shutil.which("espeak-ng") else (False, "espeak-ng is not installed (apt-get install espeak-ng)")

    def synth(self, text: str, lang: str, out: Path) -> float:
        subprocess.run(["espeak-ng", "-v", self.VOICES[lang], "-s", "150", "-w", str(out), text], check=True, capture_output=True)
        return wav_seconds(out)


class AzureTTS(Provider):
    """Azure AI Speech, neural voices. Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION, and --allow-paid.
    Voice per language: ATP_TTS_VOICE_EN / ATP_TTS_VOICE_MR, else the defaults below (the owner picks)."""
    name = "azure"
    paid = True
    VOICES = {"en": "en-IN-NeerjaNeural", "mr": "mr-IN-AarohiNeural"}     # alternatives: en-IN-PrabhatNeural, mr-IN-ManoharNeural

    def voice(self, lang: str) -> str:
        return os.environ.get(f"ATP_TTS_VOICE_{lang.upper()}", self.VOICES[lang])

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        if not (os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION")):
            return False, "AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set"
        if not allow_paid:
            return False, "a paid service: run with --allow-paid once the owner has approved it"
        return True, "key set, paid calls allowed"

    def synth(self, text: str, lang: str, out: Path) -> float:
        from xml.sax.saxutils import escape
        region = os.environ["AZURE_SPEECH_REGION"]
        ssml = (f"<speak version='1.0' xml:lang='{LOCALE[lang]}'><voice name='{self.voice(lang)}'>"
                f"<prosody rate='-5%'>{escape(text)}</prosody></voice></speak>")
        req = urllib.request.Request(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", data=ssml.encode("utf-8"),
                                     headers={"Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_KEY"],
                                              "Content-Type": "application/ssml+xml",
                                              "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm", "User-Agent": "atp-video"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out.write_bytes(r.read())
        return wav_seconds(out)


class SarvamTTS(Provider):
    """Sarvam AI Bulbul. Needs SARVAM_API_KEY, SARVAM_TTS_MODEL (the Bulbul version the owner picks) and
    SARVAM_SPEAKER (the voice), and --allow-paid."""
    name = "sarvam"
    paid = True

    def voice(self, lang: str) -> str:
        return f"{os.environ.get('SARVAM_TTS_MODEL', '?')}:{os.environ.get('SARVAM_SPEAKER', '?')}"

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        missing = [k for k in ("SARVAM_API_KEY", "SARVAM_TTS_MODEL", "SARVAM_SPEAKER") if not os.environ.get(k)]
        if missing:
            return False, f"{' / '.join(missing)} not set"
        if not allow_paid:
            return False, "a paid service: run with --allow-paid once the owner has approved it"
        return True, "key set, paid calls allowed"

    def synth(self, text: str, lang: str, out: Path) -> float:
        body = json.dumps({"text": text, "target_language_code": LOCALE[lang], "speaker": os.environ["SARVAM_SPEAKER"],
                           "model": os.environ["SARVAM_TTS_MODEL"]}).encode("utf-8")
        req = urllib.request.Request("https://api.sarvam.ai/text-to-speech", data=body,
                                     headers={"api-subscription-key": os.environ["SARVAM_API_KEY"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = json.loads(r.read().decode("utf-8"))["audios"][0]
        out.write_bytes(base64.b64decode(audio))
        return wav_seconds(out)


PROVIDERS: Dict[str, Provider] = {p.name: p for p in (EspeakPlaceholder(), SilentTiming(), AzureTTS(), SarvamTTS())}


def pick(name: str, allow_paid: bool) -> Provider:
    """The provider asked for; `placeholder` falls back to `silent` when espeak-ng is missing. A paid provider that
    cannot run raises TtsUnavailable - never a silent fallback that would hide a missing key."""
    p = PROVIDERS.get(name)
    if p is None:
        raise TtsUnavailable(f"unknown TTS provider {name!r}; one of {', '.join(PROVIDERS)}")
    ok, why = p.available(allow_paid)
    if ok:
        return p
    if name == "placeholder":
        print(f"tts: placeholder voice unavailable ({why}); using a silent timing track", file=sys.stderr)
        return PROVIDERS["silent"]
    raise TtsUnavailable(f"{name}: {why}")


def speak(p: Provider, text: str, lang: str, cache: Path) -> Tuple[Path, float]:
    """The clip of one line, from the cache when the same provider, voice and text were spoken before."""
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{p.name}|{p.voice(lang)}|{lang}|{text}".encode("utf-8")).hexdigest()[:24]
    out = cache / f"{p.name}-{lang}-{key}.wav"
    if out.exists() and out.stat().st_size > 44:
        return out, wav_seconds(out)
    tmp = out.with_suffix(".part.wav")
    secs = p.synth(text, lang, tmp)
    tmp.replace(out)
    return out, secs


# --- the cost basis -----------------------------------------------------------------------------------------------------

def load(episode: str, lang: str) -> dict:
    return json.loads((SCRIPTS / f"{episode}.{lang}.json").read_text(encoding="utf-8"))


def spoken_lines(script: dict) -> List[str]:
    return [line["text"] for scene in script["scenes"] for line in scene["lines"]]


def counts() -> Dict[str, Dict[str, int]]:
    """Characters sent to a TTS service per episode and language (every spoken line; the cards are not spoken)."""
    out: Dict[str, Dict[str, int]] = {}
    for path in sorted(SCRIPTS.glob("E*.en.json")):
        ep = path.name.split(".")[0]
        out[ep] = {lang: sum(len(t) for t in spoken_lines(load(ep, lang))) for lang in LANGS}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "count"
    if cmd == "count":
        total = {lang: 0 for lang in LANGS}
        print("| Episode | English chars | Marathi chars | Both |")
        print("|---|---:|---:|---:|")
        for ep, c in counts().items():
            print(f"| {ep} | {c['en']:,} | {c['mr']:,} | {c['en'] + c['mr']:,} |")
            for lang in LANGS:
                total[lang] += c[lang]
        print(f"| All 6 | {total['en']:,} | {total['mr']:,} | {total['en'] + total['mr']:,} |")
        return 0
    if cmd == "providers":
        for p in PROVIDERS.values():
            ok, why = p.available("--allow-paid" in args)
            print(f"{p.name:12} {'paid' if p.paid else 'free':5} {'ready' if ok else 'off'}: {why}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
