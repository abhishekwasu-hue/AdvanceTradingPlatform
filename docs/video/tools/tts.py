#!/usr/bin/env python3
"""M92 V3: the voice of the "Find setups" videos - one adapter, several providers.

    placeholder  espeak-ng, offline and free (a robotic voice: for checking timing and the pipeline, never for publishing)
    silent       a silent track timed from the text length (when espeak-ng is not installed)
    azure        Azure AI Speech neural voices (mr-IN-AarohiNeural / mr-IN-ManoharNeural, en-IN) - PAID
    sarvam       Sarvam AI Bulbul (Indian languages) - PAID
    indictts     AI4Bharat Indic-TTS, Marathi and Indian English (FastPitch + HiFi-GAN, MIT licence) - free, local, CPU,
                 no key; two speakers per language, female and male (install: docs/video/setup_indictts.sh)

A paid provider runs only with BOTH its key in the environment AND `--allow-paid` on the command line; without either it
reports itself unavailable and nothing is sent. No key, address or token is ever written to the repository or the logs.
The owner picks the service (owner order M92); until then only `placeholder` / `silent` run.

Every clip is cached by (provider, voice, language, text): a rebuild after a UI change re-uses the stored voice and calls
no service again (the cache lives with the outputs, outside git).

Narrator per scene: a script may set `"narrator": {"odd": "female", "even": "male"}` (the speaker for odd and even
scene numbers) and a scene may set its own `"voice"`; a provider with speakers uses them, the others ignore them.
Pronunciation: a provider that reads only its own script (indictts cannot read Latin letters or most digits) gets the
line through scripts/pronounce.json first; the subtitles keep the script as written.

    python3 docs/video/tools/tts.py count            # characters per episode and language (the cost basis, TTS_COST.md)
    python3 docs/video/tools/tts.py providers        # which providers can run here, and why not
    python3 docs/video/tools/tts.py say E1 mr        # each line as the local voice will say it (scene, narrator, text)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
REPO = HERE.parent.parent.parent
LANGS = ("en", "mr")
LOCALE = {"en": "en-IN", "mr": "mr-IN"}


class TtsUnavailable(RuntimeError):
    """The provider cannot run here (no key, no tool, or a paid call without --allow-paid)."""


class Provider:
    """One text-to-speech service. `synth` writes a mono 16-bit PCM WAV and returns its length in seconds."""
    name = "base"
    paid = False
    own_script_only = False      # True: cannot read Latin letters etc. -> the text goes through scripts/pronounce.json

    def voice(self, lang: str, speaker: Optional[str] = None) -> str:
        return lang

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        raise NotImplementedError

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
        raise NotImplementedError

    def supports(self, lang: str) -> bool:
        return True

    def unreadable(self, text: str, lang: str = "") -> List[str]:
        """The characters of `text` this provider cannot say in `lang` (checked before any clip is made)."""
        return []


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

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
        return _silence(out, max(1.2, len(text) / self.RATE.get(lang, 14.0)))


class EspeakPlaceholder(Provider):
    """espeak-ng (offline, free). A placeholder voice to check the pipeline and the timing; never for publishing."""
    name = "placeholder"
    VOICES = {"en": "en-gb", "mr": "mr"}

    def voice(self, lang: str, speaker: Optional[str] = None) -> str:
        return "espeak-ng:" + self.VOICES[lang]

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        return (True, "espeak-ng found") if shutil.which("espeak-ng") else (False, "espeak-ng is not installed (apt-get install espeak-ng)")

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
        subprocess.run(["espeak-ng", "-v", self.VOICES[lang], "-s", "150", "-w", str(out), text], check=True, capture_output=True)
        return wav_seconds(out)


class AzureTTS(Provider):
    """Azure AI Speech, neural voices. Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION, and --allow-paid.
    Voice per language: ATP_TTS_VOICE_EN / ATP_TTS_VOICE_MR, else the defaults below (the owner picks)."""
    name = "azure"
    paid = True
    VOICES = {"en": "en-IN-NeerjaNeural", "mr": "mr-IN-AarohiNeural"}     # alternatives: en-IN-PrabhatNeural, mr-IN-ManoharNeural

    def voice(self, lang: str, speaker: Optional[str] = None) -> str:
        return os.environ.get(f"ATP_TTS_VOICE_{lang.upper()}", self.VOICES[lang])

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        if not (os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION")):
            return False, "AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set"
        if not allow_paid:
            return False, "a paid service: run with --allow-paid once the owner has approved it"
        return True, "key set, paid calls allowed"

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
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

    def voice(self, lang: str, speaker: Optional[str] = None) -> str:
        return f"{os.environ.get('SARVAM_TTS_MODEL', '?')}:{os.environ.get('SARVAM_SPEAKER', '?')}"

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        missing = [k for k in ("SARVAM_API_KEY", "SARVAM_TTS_MODEL", "SARVAM_SPEAKER") if not os.environ.get(k)]
        if missing:
            return False, f"{' / '.join(missing)} not set"
        if not allow_paid:
            return False, "a paid service: run with --allow-paid once the owner has approved it"
        return True, "key set, paid calls allowed"

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
        body = json.dumps({"text": text, "target_language_code": LOCALE[lang], "speaker": os.environ["SARVAM_SPEAKER"],
                           "model": os.environ["SARVAM_TTS_MODEL"]}).encode("utf-8")
        req = urllib.request.Request("https://api.sarvam.ai/text-to-speech", data=body,
                                     headers={"api-subscription-key": os.environ["SARVAM_API_KEY"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = json.loads(r.read().decode("utf-8"))["audios"][0]
        out.write_bytes(base64.b64decode(audio))
        return wav_seconds(out)


class IndicTTS(Provider):
    """AI4Bharat Indic-TTS (FastPitch + HiFi-GAN; MIT licence): Marathi (`mr`) and Indian English (`en`), one model per
    language. Free and local: CPU only, no key, no network at run time. Two speakers per model, `female` (default) and
    `male`; the episode script picks them per scene (`narrator`). Installed by docs/video/setup_indictts.sh into
    ATP_INDICTTS_DIR (default ../atp-indictts next to the repository, outside git): its own venv runs
    tools/indictts_worker.py, which loads a model once for the whole build. Each model reads only its own characters
    (Marathi: Devanagari; English: plain Latin letters, some signs): every line goes through scripts/pronounce.json first."""
    name = "indictts"
    own_script_only = True
    SPEAKERS = ("female", "male")
    LANGS = ("mr", "en")
    # Speaking pace per language (FastPitch `length_scale`: above 1 = slower). The English model speaks fast (about
    # 160 words a minute); 1.15 brings it near a calm tutorial pace. ATP_INDICTTS_PACE_EN / _MR override it.
    PACE = {"mr": 1.0, "en": 1.15}

    def pace(self, lang: str) -> float:
        return float(os.environ.get(f"ATP_INDICTTS_PACE_{lang.upper()}", self.PACE.get(lang, 1.0)))

    def __init__(self) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}
        self._known: Dict[str, set] = {}

    @staticmethod
    def home() -> Path:
        return Path(os.environ.get("ATP_INDICTTS_DIR") or (REPO.parent / "atp-indictts"))

    def _python(self) -> Path:
        return self.home() / "venv" / "bin" / "python"

    def _model(self, lang: str = "mr") -> Path:
        return self.home() / lang

    def voice(self, lang: str, speaker: Optional[str] = None) -> str:
        pace = self.pace(lang)
        return f"indictts:{lang}:{speaker or self.SPEAKERS[0]}" + (f":pace{pace:g}" if pace != 1.0 else "")

    def installed(self) -> List[str]:
        """The languages whose model is in place."""
        return [lang for lang in self.LANGS
                if all((self._model(lang) / d / "best_model.pth").exists() for d in ("fastpitch", "hifigan"))]

    def available(self, allow_paid: bool = False) -> Tuple[bool, str]:
        if not self._python().exists():
            return False, f"no venv in {self.home()} (run docs/video/setup_indictts.sh, or set ATP_INDICTTS_DIR)"
        langs = self.installed()
        if not langs:
            return False, f"no model in {self.home()} (run docs/video/setup_indictts.sh)"
        return True, f"models in {self.home()} ({', '.join(langs)}: female, male)"

    def supports(self, lang: str) -> bool:
        return lang in self.installed()

    def unreadable(self, text: str, lang: str = "mr") -> List[str]:
        if lang not in self._known:
            cfg = json.loads((self._model(lang) / "fastpitch" / "config.json").read_text(encoding="utf-8"))["characters"]
            self._known[lang] = set(cfg.get("characters") or "") | set(cfg.get("punctuations") or "")
        return sorted({c for c in text if c not in self._known[lang] and not c.isspace()})

    def _worker(self, lang: str) -> subprocess.Popen:
        proc = self._procs.get(lang)
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen([str(self._python()), "-I", str(HERE / "indictts_worker.py"), str(self._model(lang))],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", bufsize=1)
            ready = proc.stdout.readline()
            if not ready.strip():
                raise TtsUnavailable(f"indictts: the {lang} model did not load (run docs/video/setup_indictts.sh again)")
            self._procs[lang] = proc
        return proc

    def synth(self, text: str, lang: str, out: Path, speaker: Optional[str] = None) -> float:
        if not self.supports(lang):
            raise TtsUnavailable(f"indictts: no {lang} model in {self.home()} (docs/video/setup_indictts.sh {lang})")
        speaker = speaker or self.SPEAKERS[0]
        if speaker not in self.SPEAKERS:
            raise TtsUnavailable(f"indictts: unknown speaker {speaker!r}; one of {', '.join(self.SPEAKERS)}")
        proc = self._worker(lang)
        proc.stdin.write(json.dumps({"text": text, "speaker": speaker, "out": str(out), "pace": self.pace(lang)}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        answer = json.loads(proc.stdout.readline() or '{"ok": false, "error": "the worker stopped"}')
        if not answer.get("ok"):
            raise RuntimeError(f"indictts: {answer.get('error')}")
        if answer.get("unknown"):
            raise RuntimeError(f"indictts: cannot say {''.join(answer['unknown'])!r} in: {text} (add it to scripts/pronounce.json)")
        return wav_seconds(out)


PROVIDERS: Dict[str, Provider] = {p.name: p for p in (EspeakPlaceholder(), SilentTiming(), AzureTTS(), SarvamTTS(), IndicTTS())}


# --- narrator and pronunciation -----------------------------------------------------------------------------------------

def scene_speaker(script: dict, scene: dict) -> Optional[str]:
    """The narrator of one scene: the scene's own `voice`, else the script's `narrator` for odd / even scene numbers
    (e.g. {"odd": "female", "even": "male"}), else None (the provider's default voice)."""
    if scene.get("voice"):
        return scene["voice"]
    narrator = script.get("narrator") or {}
    return narrator.get("odd" if int(scene["n"]) % 2 else "even")


_PRONOUNCE: Dict[str, Tuple[List[Tuple[re.Pattern, str]], Dict[str, str]]] = {}


def _pronounce_rules(lang: str) -> Tuple[List[Tuple[re.Pattern, str]], Dict[str, str]]:
    if lang not in _PRONOUNCE:
        path = SCRIPTS / "pronounce.json"
        table = json.loads(path.read_text(encoding="utf-8")).get(lang, {}) if path.exists() else {}
        words = sorted((table.get("words") or {}).items(), key=lambda kv: -len(kv[0]))
        rules = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", re.I), v) for k, v in words]
        _PRONOUNCE[lang] = (rules, table.get("chars") or {})
    return _PRONOUNCE[lang]


def pronounce(text: str, lang: str) -> str:
    """The line as the voice should say it: the words of scripts/pronounce.json (longest first), then its characters.
    Only for the TTS input; the subtitles keep `text`."""
    rules, chars = _pronounce_rules(lang)
    for pattern, said in rules:
        text = pattern.sub(said, text)
    for c, said in chars.items():
        text = text.replace(c, said)
    return re.sub(r" {2,}", " ", text).strip()


def tts_text(p: Provider, text: str, lang: str) -> str:
    return pronounce(text, lang) if p.own_script_only else text


def unsayable(p: Provider, script: dict) -> List[str]:
    """Lines this provider could not say after the pronunciation map (empty = all fine); checked before the build."""
    lang = script["lang"]
    out = []
    for scene in script["scenes"]:
        for line in scene["lines"]:
            said = tts_text(p, line["text"], lang)
            bad = p.unreadable(said, lang)
            if bad:
                out.append(f"scene {scene['n']}: cannot say {' '.join(bad)} in: {said}")
    return out


def pick(name: str, allow_paid: bool, lang: Optional[str] = None) -> Provider:
    """The provider asked for; `placeholder` falls back to `silent` when espeak-ng is missing. A paid provider that
    cannot run raises TtsUnavailable - never a silent fallback that would hide a missing key."""
    p = PROVIDERS.get(name)
    if p is None:
        raise TtsUnavailable(f"unknown TTS provider {name!r}; one of {', '.join(PROVIDERS)}")
    ok, why = p.available(allow_paid)
    if ok and lang and not p.supports(lang):
        ok, why = False, f"no {lang} voice installed"
    if ok:
        return p
    if name == "placeholder":
        print(f"tts: placeholder voice unavailable ({why}); using a silent timing track", file=sys.stderr)
        return PROVIDERS["silent"]
    raise TtsUnavailable(f"{name}: {why}")


def speak(p: Provider, text: str, lang: str, cache: Path, speaker: Optional[str] = None) -> Tuple[Path, float]:
    """The clip of one line, from the cache when the same provider, voice and (spoken) text were spoken before."""
    cache.mkdir(parents=True, exist_ok=True)
    said = tts_text(p, text, lang)
    key = hashlib.sha256(f"{p.name}|{p.voice(lang, speaker)}|{lang}|{said}".encode("utf-8")).hexdigest()[:24]
    out = cache / f"{p.name}-{lang}-{key}.wav"
    if out.exists() and out.stat().st_size > 44:
        return out, wav_seconds(out)
    tmp = out.with_suffix(".part.wav")
    secs = p.synth(said, lang, tmp, speaker)
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
    if cmd == "say" and len(args) >= 3:          # tts.py say <E> <lang>: the text each line is spoken as
        script = load(args[1].upper(), args[2])
        for scene in script["scenes"]:
            for line in scene["lines"]:
                print(f"{scene['n']}\t{scene_speaker(script, scene) or '-'}\t{pronounce(line['text'], args[2])}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
