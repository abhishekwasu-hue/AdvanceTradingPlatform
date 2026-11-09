# M92 - voice (TTS) cost for the "Find setups" videos

No paid call has been made. The owner picks the service first (owner order M92: "no paid TTS call before the owner
approves the service and cost").

## Characters per episode

Counted by `python3 docs/video/tools/tts.py count` from the scripts in `docs/video/scripts/` (every spoken line,
including each scene's education line; the intro / outro cards are not spoken). A character is one Unicode code point,
so a Marathi vowel sign counts as one character.

## Cost per episode (both languages) and for the whole series

| Episode | English chars | Marathi chars | Both | Azure neural (USD 16 / 1M) | Sarvam Bulbul v2 (INR 15 / 10K) | Sarvam Bulbul v3 (INR 30 / 10K) |
|---|---:|---:|---:|---:|---:|---:|
| E1 | 2,100 | 2,116 | 4,216 | USD 0.067 | INR 6.32 | INR 12.65 |
| E2 | 1,840 | 1,862 | 3,702 | USD 0.059 | INR 5.55 | INR 11.11 |
| E3 | 1,900 | 1,850 | 3,750 | USD 0.060 | INR 5.62 | INR 11.25 |
| E4 | 1,815 | 1,846 | 3,661 | USD 0.059 | INR 5.49 | INR 10.98 |
| E5 | 1,692 | 1,720 | 3,412 | USD 0.055 | INR 5.12 | INR 10.24 |
| E6 | 1,843 | 1,867 | 3,710 | USD 0.059 | INR 5.56 | INR 11.13 |
| **All 6 x 2 languages** | **11,190** | **11,261** | **22,451** | **USD 0.36** | **INR 33.68** | **INR 67.35** |

One language alone is about half of each row. Re-recording after a UI change costs only the lines whose text changed:
the build caches every clip by provider, voice and text (`<out>/tts-cache`). Even three full re-takes of the series stay
under USD 1.10 (Azure) or INR 205 (Bulbul v3).

Free allowances, if they apply to the account (to confirm): Azure's free tier is reported as 0.5 million neural
characters a month, which is more than the whole series. Sarvam reports INR 1,000 of free credits on a new account.

## Prices: source and date - TO CONFIRM before approval

| Provider | Price used above | Source (read on 8 Nov 2026) | Status |
|---|---|---|---|
| Azure AI Speech, neural voices (mr-IN-AarohiNeural, mr-IN-ManoharNeural, en-IN-NeerjaNeural / PrabhatNeural) | USD 16 per 1 million characters (pay as you go) | Web search summaries of the Azure pricing page and third-party price lists. Azure's own page shows prices only in its interactive table; another list gave USD 15. | **To confirm** on azure.microsoft.com/pricing/details/cognitive-services/speech-services for the account's region and currency. |
| AI4Bharat Indic-TTS, Marathi (female + male; owner decision: both) | Free: runs locally on the CPU, MIT licence; no key, no per-character cost | github.com/AI4Bharat/Indic-TTS (LICENSE.txt, release `v1-checkpoints-release`) | **In use** for the Marathi videos (`--tts indictts`, README) |
| Sarvam AI Bulbul v2 | INR 15 per 10,000 characters | Web search summary of Sarvam's API pricing page and docs (docs.sarvam.ai, sarvam.ai/api-pricing) | **To confirm** on sarvam.ai/api-pricing |
| Sarvam AI Bulbul v3 | INR 30 per 10,000 characters (marked beta pricing) | as above | **To confirm** |
| Sarvam AI Bulbul V4 Flash | INR 20 per 10,000 characters reported, with a launch offer of INR 10 until 12 Nov | News reports only; not on the pricing pages found | **Not used above**; to confirm |

This session could not open the pricing pages themselves (their hosts are not reachable from the build environment),
so every price above is marked "to confirm". The character counts are exact.

Also to confirm with the provider: how SSML markup is billed (the Azure adapter wraps each line in a short `<speak>` /
`<voice>` / `<prosody>` element), and Sarvam's per-request text limit (each script line is under 300 characters).

## What the owner decides

1. The service: Azure (two Marathi neural voices, female Aarohi / male Manohar, plus en-IN voices) or Sarvam Bulbul
   (Indian-language voices; the version v2 / v3 / V4 Flash).
2. The voice for each language.
3. Then: put the key in the build machine's environment (never in the repository), and run
   `docs/video/build.sh E1 mr --tts azure --allow-paid` (or `--tts sarvam`). See README.md.
