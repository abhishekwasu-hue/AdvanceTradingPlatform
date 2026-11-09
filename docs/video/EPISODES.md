# M92 V1 - "Find setups" episode map

Source: the 19-scene outline and the Find setups guide (screener guide). Six episodes, 3 to 4 minutes each (Marathi; the English voice is faster). Every scene
ends with "Information and education only, not investment advice." Scripts: `scripts/<E>.<lang>.json`; stills:
`frontend/e2e/video/scenes.ts` (one function per scene).

| Episode | Scenes | Title | Guide sections | Length with the Indic-TTS voices (English / Marathi) |
|---|---|---|---|---|
| E1 | 1 What a screener is, 2 What it does NOT do, 3 Sign in, 4 Open Find setups, 5 Page tour | What it is, and the page | 1, 2.1, 2.2, 3 | 3:10 / 3:22 |
| E2 | 6 Pick a list, 7 Pick a chart | Your list and your chart | 4, 5 | 2:54 / 3:19 |
| E3 | 8 Start a scan, 9 Build blocks, 10 Bars ago and timeframe chip | Start a scan and build it | 6, 7, 7.3 | 2:53 / 3:05 |
| E4 | 11 Run, 12 Read results, 13 Data source | Run, results, and the data | 7.5, 8, 8.1, 8.2, 9 | 2:44 / 3:10 |
| E5 | 15 Example 1, 16 Example 2, 17 Example 3 + alert | Three examples and an alert | 11, 8.3 | 2:46 / 3:13 |
| E6 | 14 Function families, 18 Troubleshooting, 19 Close | Building blocks, problems, and the close | 10, 12, 13 | 2:45 / 3:08 |

Lengths are the rendered episodes with the free Indic-TTS voices (female / male alternating per scene; English at pace
1.15, as it speaks faster), cards and pauses included. The build prints each scene's length against its `target_s`.

## Lines checked against the shipped app (M86 + M90 live)

| Scene | Line | Shipped code it follows |
|---|---|---|
| 6 | "A list holds up to five hundred stocks, and Run scans all of them, within your broker's limits. There is no fixed fifty." | `screener/universe.ts` (`max_symbols` 500, `RUN_MAX` 50 = one ordinary run; a longer list is streamed in batches), `universe-batches` note |
| 6 | "During a run, a line shows the progress, for example 120 / 500 done. If your broker's limit slows it, it says Slowed by broker limit." | `ScreenerPage.tsx` `scan-progress` ("N / M done", "Slowed by broker limit") |
| 13 | "Without a broker: Daily, Weekly and Monthly from ATP's stored end-of-day history. For 1 minute to 1 hour, connect your broker." | `screener/routes.py` `_store_frames` (1d / 1w / 1M from the NSE daily files; intraday from the reader's own broker) |
| 13 | "After a run, hover over the time pill: it names the source, your broker or ATP's stored end-of-day history." | `FreshnessPill.tsx` (`sourceWords`: the pill's tooltip) |

Dropped: the "Last close, with the date" label and the "end-of-day banner at the top" - the Find setups page has
neither (the "Last close <date>" line belongs to the watchlist on Charts); the scene 13 card no longer shows it.

## Before recording (from the outline)

Check which parts are switched on for the recording account (guide section 14) and drop or re-word a scene whose part is
off: Own words (scene 8), the screen library (8), Extra screen functions / RsiDivergence (17), alerts (17).
