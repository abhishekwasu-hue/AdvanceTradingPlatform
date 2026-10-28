# OWNER ORDER - UX-11b "Numbers first, logical colour" (28 Oct 2026)

Verbatim as received (Session B). Roadmap ID UX-11b; extends UX-11 (ATP_ORDER_UX11.md).

---

OWNER ORDER — UX-11b "Numbers first, logical colour" — WHOLE-APP PASS (28 Oct). Extends UX-11 (do not restart it). File in INBOX
verbatim / DECISIONS / roadmap / README first. An overseer audit (read-only, file:line, repo 77a5005) found the items below — re-verify,
first report = confirmed / not reproduced, one line each. Owner: "too much theory; attractive tables; logical multicolour".

ROOT CAUSE: the Today briefs and every dish card arrive from the backend as finished sentences (rhythm/build.py:70-85 Section.lines:
List[str]; growth/dishes.py), so the frontend has no numbers to colour. The UX-11 primitives exist (ui/numbersFirst.ts, Details,
Signed, KeyValueTable, ProgressStat, PageFootnote, Chip, StatusChip, RangeBar, ResponsiveTable, e2e/text-budget-v2) but Today,
PageDishV2 and Markets use none of them.

COLOUR RULES (one table, used everywhere; colour always with ▲ ▼ ◆ or an icon, never alone; WCAG AA in dark + light; colour-blind
mode keeps working):
  green  --up   = up / profit / pass / inside limits / low VIX
  red    --down = down / loss / fail / breached / high VIX
  amber  --warn = caution / elevated / stale / near invalidation / CHoCH / holiday / ≥ 80% of a budget
  blue   --info = information / link / action
  grey   --fg-muted = neutral / none / sideways / normal
Colour encodes meaning only — never decoration. Add an AA contrast test for every chip tone on every surface token.

C1 Typed payloads (backend): rhythm Section gets `kind` + typed `rows` (numbers, signs, states); `lines` stay only for Telegram
   (rhythm/deliver.py). Same for growth/dishes.py (dish cards return typed facts + one short line). Frontend renders by kind.
C2 Today briefs (DailyRhythmCardV2.tsx:20-46, types.ts:5-14):
   MORNING (build.py:525-533) — Global cues (214-225, global_cues.py:220-232): mood chip Positive▲green / Negative▼red / Mixed◆grey +
   table Market | Last | Chg % (Signed) for every market, amber "delayed" chip if stale. India VIX (226-233): value | Chg % | level chip
   Low / Normal / Elevated / High (one shared band table for backend + frontend; today they disagree: market_memory.py:226-233 vs
   dashboard/model.ts:139-146) | 1-year percentile bar. FII / DII (234-237): two Signed ₹ cr + "as of" date. Events (241-265): chips;
   empty → one muted line + the News & events link. Structure (177-212; merge the separate NIFTY line at :210): table Index | Daily |
   Weekly | Last event | Location — trend chips UP▲green / DOWN▼red / SIDEWAYS◆grey, BOS chip coloured by direction, CHoCH amber,
   location chip (in buyer area green / in seller area red / neutral grey), amber "last read <date>" if stale. Setups (268-308): Symbol |
   side chip | source | ⓘ. Positions (329-349): Symbol | Side | Open P&L (Signed) | amber "near invalidation". Risk budget (360-372): bar
   (amber ≥ 80%, red 100%) + "losses in a row 2 / 3" chip.
   PULSE (536-543): same Structure table + positions near invalidation + new setups + Day block (391-409): Booked / Open P&L Signed,
   limits chip Inside✓green / Breached✕red, discipline score number + bar.
   WRAP (546-547; 412-462): Index | Close | Chg %; breadth advances / declines green-red bar; strongest / weakest sector rows Signed;
   tomorrow chips.
   WEEK (550-560; 465-505): 5-session move table + Structure table; Closed / Won / Lost / Net (Signed); discipline score ▲▼ vs last
   week; days inside limits progress bar; costliest pattern Signed row; next week's events as chips.
   Footer: build.py:36 disclaimer → PageFootnote (once).
C3 Dish pages: Positions (dishes.py:224, 232-236, 252 → KeyValueTable + Signed + progress bars; remove "Phase 0 has PAPER only."
   jargon PositionsV2.tsx:309); Signals (dishes.py:111 → table Strategy | Closed | Won | Lost | Win %; :123 waiting → chip; :140-144 →
   chip row; "Nothing is placed until you approve it" ×3 (dishes.py:123, SignalsV2.tsx:110-111, :128) + DayOneV2.tsx:281 → once in the
   footer); Journal (dishes.py:305-311 score → number + ▲▼ + sparkline; :316-319 habits → Tag | Trades | Net; :338-342 best / worst →
   2-row table; dev text JournalPage.tsx:18 out); Charts dish (dishes.py:389-458 → trend chips).
C4 Markets (markets/model.ts:179, 186, 191; MarketsPageV2.tsx:96-97): Signed values, leader / laggard chips, sectors-up progress bar.
C5 Risk (RiskV2.tsx:163, 220, 285, 298: "exits never blocked" ×4 → once in the footer; :233, :357 → Details); Paper bot
   (PaperBotV2.tsx:246 botWhat() → key-value; :173, 273, 465 → one line + Details); Screener (covered by SCR-FIX PR D/E); Copilot
   (answers: structured rows + Signed where data exists; locales/en.ts:203 dev text out); Option chain intro (OptionChainPageV2.tsx:189),
   Settings (TradingDefaultsV2.tsx:254), Options builder (OptionsBuilderPage.tsx:207) → one line + Details.
C6 Shared components: add TrendChip (UP / DOWN / SIDEWAYS) and LevelChip (bands: VIX, mood, budget); merge the two Signed components
   (components/primitives/Feedback.tsx and ui/Signed.tsx) into one; DirectionChip stays for long / short.
C7 Guard: e2e/textBudget.ts:12 skips `li`, so bullet prose passes — include list items; add "a signed number rendered without Signed"
   lint where feasible. Extend text-budget-v2 to Today (all four tabs), Markets, dish pages.
C8 Rules: global_cues.py:236-240 clock-time line ("first 15 minutes", "opening range") reaches the Copilot via describe()[:2] — remove
   from user output (no clock framing, CLAUDE.md §5).
Owner decision (default, do not wait): support / resistance values (words.py:88-89 hides them) — show the area as a price range inside
ⓘ Details on the user's OWN brief only (CLAUDE.md §3); never on public / education surfaces.
Order: PR 1 C1 + C6 (+ tests) → PR 2 C2 Today → PR 3 C3 dishes → PR 4 C4 + C5 → PR 5 C7 + C8. Behind ui_v2; Telegram text unchanged.
PROOF per PR: phone 390 px dark + light and desktop, before / after, visible-word count per page before → after, in ONE comparison
page. Self-review every diff; deploy line + migration yes / no. Reports ≤ 15 lines.
