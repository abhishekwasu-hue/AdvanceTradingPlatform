# OWNER ORDER - CP-UX + SB-UX: Copilot and Strategy builder redesign (30 Oct 2026)

Verbatim, as received (roadmap CP-UX and SB-UX, P1). Audit note: docs/design/CP_SB_AUDIT.md (written first).

```
After that work 
OWNER ORDER — COPILOT + STRATEGY BUILDER REDESIGN (30 Oct). Roadmap rows "CP-UX" and "SB-UX" (P1). File INBOX verbatim / DECISIONS /
roadmap / README. Also file ATP_ORDERS_FULLTEXT_29OCT.md (attached) — CP-FIX is still a stub; its CP1 / CP3 are P1 inside this order.
Owner (Marathi, translated): "Copilot and Strategy builder are confusing, far too much theory, not user friendly, one page crammed,
no proper arrangement, not professional; their usefulness is doubtful." Overseer audit (main 124eafc, file:line; benchmarks: Perplexity
Finance, TradeZella Zella AI, TradingView Copilot, Streak, AlgoTest, Sensibull — sources in docs/design/CP_SB_AUDIT.md, write it first).
Rules: re-verify each item (first report = confirmed / not reproduced); one PR per group; self-review every diff; tests; behind ui_v2;
before / after phone 390 px (dark + light) + desktop in ONE comparison page per PR; ≤ 15-line reports; UI-28 rules apply (numbers
first, one disclaimer per page, no jargon, no native selects, 44 px targets). Nothing deleted — old pages stay reachable under Advanced
until the owner approves removal.

COPILOT (CP-UX)
CU1 P1 data fixes first: SENSEX/BANKEX into _INDEX_WORDS and out of _NOT_SYMBOLS (copilot_chat.py:229-246; every job + builder_symbol
    :1378); every answer names the symbol it read. CP1 chart reader with broker candles per TF (tools/copilot.py:154-178) and drop the
    "full reader is not in ATP yet" line (copilot_chat.py:43, :707) once CP1 lands.
CU2 Screen 1 (phone): numbers strip (NIFTY / BANKNIFTY / SENSEX last + Signed %, VIX level chip, FII net) → 4 chips (Brief · Read
    chart · Check my trade · My positions) → the question box pinned as the ONLY primary action; small "AI on / off" chip opens key
    setup. Remove: the duplicate 10 job cards (chat_routes.py:137, CopilotChatV2.tsx:136-156), the 10 "Ready" pills, the intro card
    (→ "?" icon), the 3D core (AiCopilotPage.tsx:61-70), the 370-char footer (en.ts:96). One disclaimer line in the page footer.
CU3 Answer card: headline number row first (price, trend chip, nearest area from–to, distance), mini chart, ONE action button; "How I
    worked this out", "What I don't know", tools used → one "Details" tap. Strategy answer (copilot_chat.py:1432-1464): result card
    (rules as chips + "Open in builder" / "Backtest") — theory sections into Details.
CU4 One Copilot: fold old tabs — Market Pulse + News Radar → Brief; Strategy Lab + Idea Builder → Strategies › Build (link, no second
    builder); Coach → Review; Watchtower → Signals. Old tabs stay under Advanced until owner ✅. Tab bar targets ≥ 44 px.
CU5 No-key state: say once "AI is off — answers are from rules" with a Connect button; never a page of fixed sentences first.
CU6 Keep the conversation across reloads (per user, server-side, deletable in Settings → My data).
CU7 Jobs behind OFF switches (screener_copilot, ai_multiview, ai_agent, copilot_chat.py:423-426) are hidden, not shown as "Ready".

STRATEGY BUILDER (SB-UX) — one stepped v2 flow (Streak / AlgoTest pattern), one primary action per step, 4–6 fields per step
SU1 Step 1 Start: template cards (bull put / bear call spread, iron condor, pullback to a demand / supply area, breakout-retest) OR one
    plain-words box using ONE parser (sb.build); remove the builder's separate parse route (StrategyBuilderPage.tsx:197) and the
    Idea Builder duplicate (IdeaBuilderTab.tsx:23) — they open this flow.
SU2 Step 2 Market: symbol (incl. SENSEX), timeframe incl. 1D / 1W (today 1–60 min only, :31), side.
SU3 Step 3 Entry: condition blocks as readable chips. Add price-action blocks (move out of UNSUPPORTED, strategy_builder.py:55-81):
    area (demand / supply, fresh / tested / flipped), pullback into area (corrective), candle pattern at area (engulfing / pin /
    tweezer / star), LTF change of character, RSI divergence (regular), trendline touch, opening gap; plus the existing indicator
    blocks. All generic, user-chosen — no owner strategy content in the public repo.
SU4 Step 4 Exit & risk — the user's own values, blank = off (DEF-0): stop, target, minimum R:R, lots, option structure + strikes +
    expiry rule (incl. "next weekly on expiry day") moved here from the Test page. Remove prefilled 1.0 ATR / 1.5 / 2.0 R
    (types.ts:737-739); the interview stops adding risk rules (interview.py:99, 431-457: "Professionals risk 0.5-1%", 3-loss cap).
SU5 Step 5 Result card: ₹ net P&L (Signed), win %, max drawdown ₹ and %, trades, avg R, equity sparkline; trades / chart / analytics
    behind tabs; units everywhere (BacktestPage.tsx:543-550 tiles lack ₹).
SU6 Step 6 "Start on paper" → Paper bot with this strategy (today none; add after the result). Save → Backtest → Paper is one chain.
SU7 Advanced (collapsed): HTF per operand, ATR period, pricing model / IV, CSV upload, sample bars, optimiser and robustness — the
    optimiser's "Grid as JSON" (:653-658) becomes fields; no raw JSON anywhere (StrategiesPage.tsx:46-50 Library → readable rule chips).
SU8 Copy: remove "deterministic, rule-based parser", "no AI-provider credentials are configured" (:236-237), PLUS_DI / OR_HIGH / PDH
    raw ids (show "Previous day high" etc.), "id {strategy_id}"; tables get scroll wrappers (:627, :669, :709), no min-w-[260px].
PROOF: phone flow video or screenshot strip — Copilot screen 1 → ask "Read the SENSEX chart" → answer card; Builder: template "bull put
on pullback to demand, NIFTY 15m, next weekly" → backtest result → Start on paper; before / after, dark + light.
Order: CU1 → SU4 (DEF-0 leak) → CU2–CU3 → SU1–SU6 → CU4–CU7 → SU7–SU8.
Then start in sequence 
```

## Filing notes

- ATP_ORDERS_FULLTEXT_29OCT.md: named as attached, but no attachment reached this session (30 Oct). CP-FIX stays a stub until
  the file is sent again; CP1 / CP3 are built inside CP-UX as this order says.
- Sequence: after TERM-2 ("After that work"), in the order the text gives: CU1 -> SU4 -> CU2-CU3 -> SU1-SU6 -> CU4-CU7 -> SU7-SU8.
