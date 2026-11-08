# M92 V1 - "Find setups" episode map

Source: the 19-scene outline and the Find setups guide (screener guide). Six episodes, 2 to 4 minutes each. Every scene
ends with "Information and education only, not investment advice." Scripts: `scripts/<E>.<lang>.json`; stills:
`frontend/e2e/video/scenes.ts` (one function per scene).

| Episode | Scenes | Title | Guide sections | Length with the placeholder voice (English / Marathi) |
|---|---|---|---|---|
| E1 | 1 What a screener is, 2 What it does NOT do, 3 Sign in, 4 Open Find setups, 5 Page tour | What it is, and the page | 1, 2.1, 2.2, 3 | 3:02 / 3:28 (rendered) |
| E2 | 6 Pick a list, 7 Pick a chart | Your list and your chart | 4, 5 | 2:39 / 3:00 |
| E3 | 8 Start a scan, 9 Build blocks, 10 Bars ago and timeframe chip | Start a scan and build it | 6, 7, 7.3 | 2:36 / 2:56 |
| E4 | 11 Run, 12 Read results, 13 Data source | Run, results, and the data | 7.5, 8, 8.1, 8.2, 9 | 2:25 / 2:52 |
| E5 | 15 Example 1, 16 Example 2, 17 Example 3 + alert | Three examples and an alert | 11, 8.3 | 2:29 / 2:49 |
| E6 | 14 Function families, 18 Troubleshooting, 19 Close | Building blocks, problems, and the close | 10, 12, 13 | 2:32 / 2:58 |

Lengths are the sum of the placeholder (espeak-ng) clips plus the pauses and cards; a neural voice will differ. Real lengths depend on the voice: the build prints each scene's length against its `target_s` and warns when an episode
is outside 2 to 4 minutes.

## Lines written for the shipped behaviour - verify after the M90 deploy

| Scene | Line | Shipped by |
|---|---|---|
| 6 | "A list can hold up to five hundred stocks, and Run scans the whole list. There is no fixed batch of fifty." | M90 (PR #953, not merged when written) |
| 6 | "During a run, a line shows how far it is, for example 120 / 500 done. If your broker's limit slows it down, the line says so." | M90 |
| 13 | "Without a broker: Daily, Weekly and Monthly from ATP's stored end-of-day history. These candles carry the label Last close, with the date." | M86 (on main) |
| 13 | "The page says it at the top: Daily scans run on the last close from the end-of-day files; intraday scans need your broker." | M86 |

These lines carry `"verify": "verify after M90 deploy"` in both scripts. Before the final recording: check them on the
live site, re-capture E2 and E4 (the stills then show the deployed screens), and adjust a line only if the live wording
differs.

## Before recording (from the outline)

Check which parts are switched on for the recording account (guide section 14) and drop or re-word a scene whose part is
off: Own words (scene 8), the screen library (8), Extra screen functions / RsiDivergence (17), alerts (17).
