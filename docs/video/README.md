# "Find setups" tutorial videos (owner order M92)

Six short episodes (2-4 minutes) in English and Marathi, each as a 1920x1080 desktop cut and a 1080x1920 (9:16) phone
cut, made from the real app with made-up data. Every part is reproducible: after a UI change, one command rebuilds an
episode.

| File | What it is |
|---|---|
| `EPISODES.md` | V1: the episode map (scenes 1-19 -> E1-E6), lengths, the lines to verify after the M90 deploy |
| `scripts/E1.en.json` ... `E6.mr.json` | V3: one scene-timed script per episode and language; each line = one voice clip + one subtitle, spoken over a still (`shot`) |
| `scripts/rules.json` | The education line every scene ends with, and the words a script never uses (per language) |
| `../../frontend/e2e/video/` | V2: the Playwright scene files: `scenes.ts` (19 scenes), `videoWorld.ts` (the made-up world), `director.ts` (cursor ring, outline, zoom box), `cards.ts`, `capture.video.ts`, `playwright.video.config.ts` |
| `tools/tts.py` | The TTS adapter: `placeholder` (espeak-ng, offline), `silent`, `azure`, `sarvam` (paid, off without a key and `--allow-paid`) |
| `tools/build.py`, `build.sh` | V4 / V5: capture, voice, picture, subtitles, MP4 + SRT |
| `TTS_COST.md` | Characters per episode and the cost per provider (prices to confirm) |

## One command (V5)

```sh
docs/video/build.sh E1 en                 # both cuts, offline placeholder voice
docs/video/build.sh E1 mr --form phone    # only the 9:16 phone cut
docs/video/build.sh E2 en --check-only    # only check the script
```

Outputs (outside git, default `../atp-video-out`, or `--out DIR` / `ATP_VIDEO_OUT`):
`<E>.<lang>.desktop.mp4`, `<E>.<lang>.phone.mp4` (H.264 + AAC, subtitles burned in) and the `.srt` next to each.

Options: `--tts placeholder|silent|azure|sarvam`, `--allow-paid`, `--reuse-shots` (skip the capture when the stills are
there), `--keep-shots`, `--skip-build` (the frontend `dist/` is current).

## Needs

- `ffmpeg` with libx264 and libass; `espeak-ng` for the placeholder voice (without it the build uses a silent timing
  track); a font with Devanagari for the Marathi subtitles and cards (`fonts-lohit-deva-marathi`, or FreeSans).
- `frontend/node_modules` (run `npm ci` in `frontend/`) and a Chromium for Playwright (`PW_CHROMIUM`, or the one in
  `PLAYWRIGHT_BROWSERS_PATH`).
- No backend, no account and no network: every `/api` call is answered by `videoWorld.ts`.

## What the build does

1. **Check** the script: every scene ends with the education line; no buy / sell / target / stop / "should" wording
   (English and Marathi lists in `scripts/rules.json`); the English and Marathi scripts use the same scenes and shots.
2. **Capture** (`frontend/e2e/video/`): `npm run build`, then `vite preview` on port 4190 (its own server every run,
   never a running one), one Playwright test per scene in both cuts. Each beat is a still with an amber cursor ring and
   outline on the element the line is about; its box is saved in `beats.json` for the zoom. The drawings of scenes 1, 2,
   7, 13, 14, 18 and 19 are cards over the page. The intro / outro cards are drawn in the episode's language.
3. **Voice**: one clip per line from the TTS provider, cached by provider, voice and text (`<out>/tts-cache`).
4. **Picture**: one clip per still, as long as its lines; a 0.7 s eased zoom onto the focus box.
5. **Join**: intro card (3.5 s), scenes, outro card (4 s); the SRT from the real clip lengths; subtitles burned in.

A scene step that fails after a UI change does not stop the build: the line keeps the frame before it, and the build
prints the problems (also in `shots/<E>/<form>/scene-NN/beats.json`). `VIDEO_DEBUG=1` saves the page's accessibility
tree next to the stills, to find the changed control.

## Made-up data only

Lists are the app's own group labels with generic members (STOCK01 ...), never a real company; no screen shows a price
next to a name (the Today page is blurred while the menu is shown, and the evidence chart is pointed at, not opened);
the account has no email; no server address, token or key is used or shown. Counts and matches are made up.

## The voice (owner decision first)

The placeholder voice is for checking the timing and the pipeline only. A paid voice runs only with its key in the
environment **and** `--allow-paid`:

| Provider | Environment | Voices |
|---|---|---|
| Azure AI Speech | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`; optional `ATP_TTS_VOICE_EN`, `ATP_TTS_VOICE_MR` | default en-IN-NeerjaNeural, mr-IN-AarohiNeural; alternatives en-IN-PrabhatNeural, mr-IN-ManoharNeural |
| Sarvam AI Bulbul | `SARVAM_API_KEY`, `SARVAM_TTS_MODEL` (the Bulbul version), `SARVAM_SPEAKER` | the owner's pick |

`python3 docs/video/tools/tts.py providers` shows which can run. Keys never go into the repository.

## Before the final recording

1. M86 and M90 live on the site (M90 = PR #953: no 50 cap, "120 / 500 done").
2. Check the lines marked `verify` (EPISODES.md) on the live site; re-capture E2 and E4.
3. Check which parts are switched on for the recording account; drop or re-word a scene whose part is off.
4. Owner approves the TTS service and voice (TTS_COST.md); build each episode with `--tts <service> --allow-paid`.
5. Review E1 in both languages and both cuts with the owner, then the rest.
