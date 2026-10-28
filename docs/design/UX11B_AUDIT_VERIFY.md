# UX-11b audit re-verify (Session B, 28 Oct, repo 77a50057d)

One line per audit item: CONFIRMED / PARTLY / NOT REPRODUCED.

- Root cause: rhythm Section is `lines: List[str]` (build.py:70-85), types.ts:5-14 `lines: string[]` - CONFIRMED.
- DailyRhythmCardV2 (rhythm/, not dashboard/) renders lines as `<li>` text; 0 uses of Signed / KeyValueTable / ProgressStat / PageFootnote / Details - CONFIRMED.
- PageDishV2 and MarketsPageV2: 0 uses of the UX-11 primitives - CONFIRMED.
- Morning = mood, events, structure, setups, positions, budget (build.py:525-533) - CONFIRMED.
- Global cues one sentence with drivers (global_cues.py:220-232) - CONFIRMED.
- VIX bands disagree: backend 12/16/20 "very calm / normal / elevated / high fear" (market_memory.py:226-233) vs frontend "Very calm / Calm / Elevated" (dashboard/model.ts:141-146) - PARTLY: same thresholds, different labels (12-16 = "normal" vs "Calm").
- FII / DII as one sentence (build.py:234-237) - CONFIRMED.
- Separate NIFTY line inside the structure section (build.py:210) - CONFIRMED.
- Pulse / Wrap / Week section lists (build.py:536-560) - CONFIRMED.
- Disclaimer NOTE (build.py:36) - CONFIRMED.
- dishes.py:111 strategy sentence, :123 "Nothing is placed", :140-144 source join, :224 / 232-236 / 252 Positions sentences, :305-311 score, :316-319 habits, :338-342 best / worst, :389+ chart story - CONFIRMED (all sentences).
- PositionsV2.tsx:309 "Phase 0 has PAPER only." - CONFIRMED (footer added by UX-11 #586; owner now calls it jargon).
- "Nothing is placed until you approve it": SignalsV2.tsx:110-111 (desktop) + :128 (phone) + dishes.py:123 + DayOneV2.tsx:281 - CONFIRMED.
- JournalPage.tsx:18 developer text ("discipline_engine feature") - CONFIRMED.
- markets/model.ts:186 strongest / weakest sentence; MarketsPageV2.tsx:96-97 - CONFIRMED (179 / 191 are the surrounding branches).
- RiskV2 "exits never blocked" repeated: :163, :216, :220, :285 ("Closing a trade always works") - CONFIRMED as 4 places (the audit's :298 is the losses-in-a-row help, not a repeat).
- RiskV2.tsx:233, :357 help lines - CONFIRMED.
- PaperBotV2.tsx:246 botWhat() as a line - CONFIRMED; :173, :273 sentences - CONFIRMED; :465 - NOT REPRODUCED (already under Details since #594).
- copilot/locales/en.ts:203 "Global cues are switched off on this platform." - CONFIRMED (admin wording on a user surface).
- OptionChainPageV2.tsx:189, TradingDefaultsV2.tsx:254, OptionsBuilderPage.tsx:207 long intros - CONFIRMED.
- Two Signed components: components/primitives/Feedback.tsx:31 and ui/Signed.tsx:44 - CONFIRMED.
- e2e/textBudget.ts:12 SKIP includes `li` - CONFIRMED (bullet prose passes the guard).
- global_cues.py:236-240 "first 15 minutes" / "opening range" lines reach the Copilot through market_memory.describe -> view(...)[:2] (market_memory.py:240); also briefing.py:149 and knowledge.py:307 take all lines - CONFIRMED (wider than the audit).
- words.py:88-89 support / resistance numbers used but never shown - CONFIRMED.
