#!/usr/bin/env python3
"""M92 V4 / V5: builds one episode of the "Find setups" series in one language - `docs/video/build.sh <episode> <lang>`.

Steps (each one re-runnable; nothing is written into the repository):
  1. check   the script (docs/video/scripts/<E>.<lang>.json): every scene ends with the education line, no
             buy / sell / target / stop / "should" wording, the same shots as the other language;
  2. capture the stills of the real app (production build, `vite preview` on its own port, made-up data only) and the
             intro / outro cards - frontend/e2e/video/*.ts, one test per scene;
  3. voice   one clip per script line with the chosen TTS provider (tools/tts.py; offline placeholder by default);
  4. picture one clip per run of lines on the same still: a slow zoom on the element the line talks about;
  5. join    intro card, scenes, outro card; the SRT from the real clip lengths; subtitles burned in (an ASS file
             rendered by libass with complex text shaping, so Devanagari conjuncts join); H.264 + AAC MP4 and the SRT.

Outputs: <out>/<E>.<lang>.<form>.mp4 and <out>/<E>.<lang>.<form>.srt (form = desktop 1920x1080 or phone 1080x1920).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tts  # noqa: E402  (the adapter next to this file)

REPO = HERE.parent.parent.parent
FRONTEND = REPO / "frontend"
SCRIPTS = HERE.parent / "scripts"
FPS = 30
RATE = 24000                              # the audio track's sample rate (mono, 16 bit)
INTRO_S, OUTRO_S = 3.5, 4.0
GAP_S, END_GAP_S = 0.45, 0.9              # silence after a line, and after a scene's closing line
SIZE = {"desktop": (1920, 1080), "phone": (1080, 1920)}
# The education line every scene ends with, and the words a script never uses (CLAUDE.md section 3; owner order M92),
# per language: docs/video/scripts/rules.json (the Marathi text lives only in the script folder).
RULES = json.loads((SCRIPTS / "rules.json").read_text(encoding="utf-8"))
END_LINE: Dict[str, str] = {lang: r["end_line"] for lang, r in RULES.items()}
FORBIDDEN = {lang: re.compile(r["forbidden"], re.I) for lang, r in RULES.items()}
# Burned-in subtitles: an ASS file in frame pixels (PlayRes = the frame), drawn by libass with COMPLEX shaping
# (HarfBuzz) - ffmpeg's `subtitles` filter on an SRT uses simple shaping, which splits Devanagari conjuncts. The fonts:
# a Devanagari font with its own shaping tables (Lohit Marathi: apt fonts-lohit-deva-marathi; or ATP_VIDEO_FONT_MR,
# e.g. "Noto Sans Devanagari"); English: Inter, else the system sans.
FONT = {"en": os.environ.get("ATP_VIDEO_FONT_EN", "Inter"), "mr": os.environ.get("ATP_VIDEO_FONT_MR", "Lohit Marathi")}
LATIN_FONT = os.environ.get("ATP_VIDEO_FONT_LATIN", "FreeSans")     # the Latin words inside a Marathi subtitle
SUB_SIZE = {"desktop": {"en": 52, "mr": 58, "margin_v": 48, "margin_lr": 120},
            "phone": {"en": 54, "mr": 60, "margin_v": 230, "margin_lr": 60}}
CRF = int(os.environ.get("ATP_VIDEO_CRF", "28"))     # the final MP4: stills compress well; ~28 keeps an episode small


def log(msg: str) -> None:
    print(f"[video] {msg}", flush=True)


def run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


# --- 1. check ---------------------------------------------------------------------------------------------------------

def load(episode: str, lang: str) -> dict:
    path = SCRIPTS / f"{episode}.{lang}.json"
    if not path.exists():
        raise SystemExit(f"no script {path.relative_to(REPO)}")
    return json.loads(path.read_text(encoding="utf-8"))


def check(script: dict, other: Optional[dict]) -> List[str]:
    lang = script["lang"]
    problems: List[str] = []
    for scene in script["scenes"]:
        lines = scene["lines"]
        last = lines[-1] if lines else {}
        if not last.get("end") or last.get("text") != END_LINE[lang]:
            problems.append(f"scene {scene['n']}: does not end with the education line")
        for line in lines:
            hit = FORBIDDEN[lang].search(line["text"])
            if hit:
                problems.append(f"scene {scene['n']}: '{hit.group(0)}' in: {line['text']}")
    if other:
        mine = [(s["n"], [l["shot"] for l in s["lines"]]) for s in script["scenes"]]
        theirs = [(s["n"], [l["shot"] for l in s["lines"]]) for s in other["scenes"]]
        if mine != theirs:
            problems.append("the English and Marathi scripts do not have the same scenes and shots")
    return problems


# --- 2. capture -------------------------------------------------------------------------------------------------------

def chromium() -> Optional[str]:
    if os.environ.get("PW_CHROMIUM"):
        return os.environ["PW_CHROMIUM"]
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache" / "ms-playwright"))
    found = sorted(base.glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


def capture(episode: str, lang: str, forms: List[str], shots: Path, script: dict, skip_build: bool) -> None:
    """The stills of one episode in one language: the cards drawn over the page (scenes 1, 2, 7, 13, 14, 18, 19) are in
    the episode's language (VIDEO_LANG; the words come from scripts/cards.json, VIDEO_TEXT)."""
    if not (FRONTEND / "node_modules").exists():
        raise SystemExit("frontend/node_modules is missing: run `npm ci` in frontend/ (or symlink it) first")
    if not skip_build:
        log("frontend: npm run build")
        run(["npm", "run", "build"], cwd=FRONTEND, stdout=subprocess.DEVNULL)
    shots.mkdir(parents=True, exist_ok=True)
    cards = [{"file": f"intro-{lang}.png", **script["intro"]}, {"file": f"outro-{lang}.png", **script["outro"]}]
    cards_file = shots / f"cards-{lang}.json"
    cards_file.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "VIDEO_EPISODE": episode, "VIDEO_SHOTS": str(shots), "VIDEO_CARDS": str(cards_file),
           "VIDEO_LANG": lang, "VIDEO_TEXT": str(SCRIPTS / "cards.json")}
    exe = chromium()
    if exe:
        env["PW_CHROMIUM"] = exe
    cmd = ["npx", "playwright", "test", "-c", "e2e/video/playwright.video.config.ts", "--global-timeout", "900000"]
    for f in forms:
        cmd += ["--project", f]
    log(f"capture {episode} ({', '.join(forms)})")
    res = subprocess.run(cmd, cwd=FRONTEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (shots / "capture.log").write_text(res.stdout, encoding="utf-8")
    if res.returncode != 0:
        tail = "\n".join(l for l in res.stdout.splitlines() if "WebServer" not in l)[-3000:]
        raise SystemExit(f"capture failed (log: {shots / 'capture.log'}):\n{tail}")


def beats(shots: Path, form: str, scene: int) -> Tuple[Dict[str, dict], List[str]]:
    data = json.loads((shots / form / f"scene-{scene:02d}" / "beats.json").read_text(encoding="utf-8"))
    return data["beats"], data.get("problems", [])


# --- 3. voice ---------------------------------------------------------------------------------------------------------

def to_pcm(src: Path) -> bytes:
    """Any WAV the providers give, as mono 16-bit PCM at RATE."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    return out


# --- 4. picture -------------------------------------------------------------------------------------------------------

def zoom_filter(focus: Optional[dict], w: int, h: int, frames: int) -> str:
    """A still -> `frames` frames at w x h: a 0.7 s eased zoom onto the focus box, then held (no zoom without a box)."""
    base = f"scale={w}:{h}:flags=lanczos,setsar=1"
    if not focus:
        return base
    fw, fh = max(focus["w"], 0.02), max(focus["h"], 0.02)
    z = max(1.0, min(1.9, 0.6 / fw, 0.6 / fh))
    if z < 1.08:
        return base
    cx, cy = focus["x"] + fw / 2, focus["y"] + fh / 2
    ramp = int(FPS * 0.7)
    t = f"min(on/{ramp},1)"
    zexpr = f"1+{z - 1:.4f}*({t})*({t})*(3-2*({t}))"
    # 2x upscale first: zoompan moves in whole pixels of its input, so a larger input keeps the pan smooth.
    return (f"scale={w * 2}:{h * 2}:flags=lanczos,setsar=1,"
            f"zoompan=z='{zexpr}':x='max(0,min(iw-iw/zoom,{cx:.4f}*iw-iw/zoom/2))':"
            f"y='max(0,min(ih-ih/zoom,{cy:.4f}*ih-ih/zoom/2))':d=1:s={w}x{h}:fps={FPS}")


def still_clip(png: Path, focus: Optional[dict], seconds: float, size: Tuple[int, int], out: Path) -> None:
    w, h = size
    frames = max(1, round(seconds * FPS))
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png),
         "-vf", zoom_filter(focus, w, h, frames) + ",format=yuv420p", "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-tune", "stillimage", "-r", str(FPS), str(out)])


# --- 5. join ----------------------------------------------------------------------------------------------------------

def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000:d}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_file(subs: List[Tuple[float, float, str]], lang: str, form: str) -> str:
    """The subtitles as ASS in frame pixels: white text on a dark box, bottom centre, smart wrapping."""
    w, h = SIZE[form]
    z = SUB_SIZE[form]
    head = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding\n"
            f"Style: Default,{FONT[lang]},{z[lang]},&H00FFFFFF,&H00FFFFFF,&H00141414,&H00141414,0,0,0,0,100,100,0,0,3,10,0,2,"
            f"{z['margin_lr']},{z['margin_lr']},{z['margin_v']},1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    def clean(t: str) -> str:
        t = t.replace("{", "(").replace("}", ")").replace("\n", " ")
        if lang == "mr":
            # The Devanagari font has no Latin letters of its own; the fallback draws them too big. App words in a
            # Marathi line (Run, Matched 6 of 50 ...) get a Latin font at a matching size. (Opaque box: no seams.)
            latin = f"{{\\fn{LATIN_FONT}\\fs{round(z[lang] * 0.86)}}}"
            t = re.sub(r"[A-Za-z0-9][A-Za-z0-9&/' .:-]*[A-Za-z0-9]|[A-Za-z0-9]", lambda m: f"{latin}{m.group(0)}{{\\r}}", t)
        return t
    return head + "".join(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{clean(t)}\n" for a, b, t in subs)


def build_form(episode: str, lang: str, form: str, script: dict, shots: Path, out_dir: Path, work: Path,
               provider: tts.Provider, cache: Path, suffix: str = "") -> Tuple[Path, Path, float, List[str]]:
    size = SIZE[form]
    work.mkdir(parents=True, exist_ok=True)
    notes: List[str] = []
    pcm = bytearray()
    silence = lambda s: b"\x00\x00" * int(round(s * RATE))       # noqa: E731
    segments: List[Tuple[Path, Optional[dict], float]] = []    # (still, focus, seconds)
    subs: List[Tuple[float, float, str]] = []
    clock = 0.0

    def add_audio(chunk: bytes, seconds: float) -> None:
        nonlocal clock
        pcm.extend(chunk)
        clock += seconds

    cards = shots / form / "cards"
    segments.append((cards / f"intro-{lang}.png", None, INTRO_S))
    add_audio(silence(INTRO_S), INTRO_S)
    scene_lengths: List[str] = []
    for scene in script["scenes"]:
        start = clock
        table, problems = beats(shots, form, scene["n"])
        notes += [f"{form} scene {scene['n']}: {p}" for p in problems]
        last_png: Optional[Path] = None
        last_focus: Optional[dict] = None
        speaker = tts.scene_speaker(script, scene)          # the narrator of this scene (script `narrator` / scene `voice`)
        for line in scene["lines"]:
            beat = table.get(line["shot"])
            if beat:
                last_png, last_focus = shots / form / f"scene-{scene['n']:02d}" / beat["file"], beat.get("focus")
            elif last_png is None:
                raise SystemExit(f"{form} scene {scene['n']}: no still for '{line['shot']}' (see beats.json problems)")
            else:
                notes.append(f"{form} scene {scene['n']}: '{line['shot']}' missing, the line keeps the frame before it")
            wav, _ = tts.speak(provider, line["text"], lang, cache, speaker)
            voice = to_pcm(wav)
            secs = len(voice) / 2 / RATE
            if secs < 0.3:
                notes.append(f"{form} scene {scene['n']}: almost no voice ({secs:.2f} s) for: {line['text']}")
            gap = END_GAP_S if line.get("end") else GAP_S
            subs.append((clock, clock + secs + gap * 0.6, line["text"]))
            add_audio(bytes(voice) + silence(gap), secs + gap)
            # The end line holds the scene's last still without the zoom; any other line zooms on its focus.
            focus = None if line.get("end") else last_focus
            if segments and segments[-1][0] == last_png and segments[-1][1] == focus:
                p, f, s = segments[-1]
                segments[-1] = (p, f, s + secs + gap)
            else:
                segments.append((last_png, focus, secs + gap))
        # The scene is as long as its voice (never cut): a scene longer than planned is extended, and said so.
        planned = scene.get("target_s")
        longer = f", extended by {clock - start - planned:.0f} s" if planned and clock - start > planned + 0.5 else ""
        who = f", {speaker}" if speaker and provider.name == "indictts" else ""
        scene_lengths.append(f"scene {scene['n']}: {clock - start:.0f} s (planned {planned or '?'} s{longer}{who})")
    segments.append((cards / f"outro-{lang}.png", None, OUTRO_S))
    add_audio(silence(OUTRO_S), OUTRO_S)

    # picture: one clip per segment, frame counts rounded on the running total so the picture never drifts from the voice
    clips: List[Path] = []
    done_frames = 0
    elapsed = 0.0
    for i, (png, focus, secs) in enumerate(segments):
        elapsed += secs
        frames = round(elapsed * FPS) - done_frames
        done_frames += frames
        clip = work / f"clip-{i:03d}.mp4"
        still_clip(png, focus, frames / FPS, size, clip)
        clips.append(clip)
    (work / "clips.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "clips.txt"), "-c", "copy", str(work / "picture.mp4")])
    for c in clips:
        c.unlink()

    with wave.open(str(work / "voice.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(pcm))

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{episode}.{lang}.{form}" + (f".{suffix}" if suffix else "")
    srt = out_dir / f"{name}.srt"
    srt.write_text("".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(subs, 1)), encoding="utf-8")
    mp4 = out_dir / f"{name}.mp4"
    ass = work / "subs.ass"
    ass.write_text(ass_file(subs, lang, form), encoding="utf-8")
    sub_arg = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(work / "picture.mp4"), "-i", str(work / "voice.wav"),
         "-vf", f"ass='{sub_arg}':shaping=complex", "-c:v", "libx264", "-preset", "medium", "-crf", str(CRF), "-tune", "stillimage",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "80k", "-ac", "1", "-shortest",
         "-movflags", "+faststart", str(mp4)])
    shutil.rmtree(work, ignore_errors=True)
    log(f"{name}: {clock:.0f} s - " + "; ".join(scene_lengths))
    return mp4, srt, clock, notes


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build one episode of the Find setups videos.")
    ap.add_argument("episode", help="E1 ... E6")
    ap.add_argument("lang", choices=["en", "mr"])
    ap.add_argument("--form", choices=["desktop", "phone", "both"], default="both")
    ap.add_argument("--tts", default=os.environ.get("ATP_VIDEO_TTS", "placeholder"), help="placeholder | silent | azure | sarvam | indictts")
    ap.add_argument("--allow-paid", action="store_true", help="let a paid TTS service run (owner approval first)")
    ap.add_argument("--out", default=os.environ.get("ATP_VIDEO_OUT", str(REPO.parent / "atp-video-out")),
                    help="output folder (outside git; default ../atp-video-out)")
    ap.add_argument("--work", default=os.environ.get("ATP_VIDEO_WORK", ""),
                    help="folder for the stills, the voice cache and the temporary clips (default: the output folder; "
                         "e.g. /dev/shm on a small disk)")
    ap.add_argument("--reuse-shots", action="store_true", help="skip the capture when the stills are there")
    ap.add_argument("--skip-build", action="store_true", help="skip `npm run build` (the dist/ is current)")
    ap.add_argument("--keep-shots", action="store_true", help="keep the stills after the build")
    ap.add_argument("--check-only", action="store_true", help="only check the scripts")
    ap.add_argument("--suffix", default="", help="added to the output names: <E>.<lang>.<form>.<suffix>.mp4 (e.g. a voice tag)")
    a = ap.parse_args(argv)

    episode = a.episode.upper()
    script = load(episode, a.lang)
    other_lang = "mr" if a.lang == "en" else "en"
    other = load(episode, other_lang) if (SCRIPTS / f"{episode}.{other_lang}.json").exists() else None
    problems = check(script, other)
    if problems:
        print("script problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    if a.check_only:
        log(f"{episode}.{a.lang}: script OK")
        return 0
    for tool in ("ffmpeg", "npx"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not installed")
    provider = tts.pick(a.tts, a.allow_paid, a.lang)
    placeholder = provider.name in ("placeholder", "silent")
    log(f"voice: {provider.name}{' (placeholder - not for publishing)' if placeholder else ''}")
    unsaid = tts.unsayable(provider, script)
    if unsaid:
        print("lines the voice cannot say (add the words to docs/video/scripts/pronounce.json):\n  " + "\n  ".join(unsaid),
              file=sys.stderr)
        return 1
    out = Path(a.out).resolve()
    work_root = Path(a.work).resolve() if a.work else out
    shots = work_root / "shots" / f"{episode}.{a.lang}"          # per language: the cards are drawn in it
    forms = ["desktop", "phone"] if a.form == "both" else [a.form]
    have = all((shots / f / "cards" / f"intro-{a.lang}.png").exists() for f in forms)
    if not (a.reuse_shots and have):
        capture(episode, a.lang, forms, shots, script, a.skip_build)
    notes: List[str] = []
    for form in forms:
        mp4, srt, secs, n = build_form(episode, a.lang, form, script, shots, out, work_root / "work" / f"{episode}-{a.lang}-{form}",
                                       provider, work_root / "tts-cache", a.suffix)
        notes += n
        if not 150 <= secs <= 240:
            notes.append(f"{form}: {secs:.0f} s is outside the plan (up to 4 minutes; {provider.name} voice timing)")
        log(f"wrote {mp4} ({mp4.stat().st_size / 1e6:.1f} MB) and {srt.name}")
    if not a.keep_shots:
        for form in forms:
            shutil.rmtree(shots / form, ignore_errors=True)
    if notes:
        log("notes:\n  " + "\n  ".join(dict.fromkeys(notes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
