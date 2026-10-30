# CP-UX / SB-UX audit: Copilot and Strategy builder (30 Oct 2026)

Owner order: docs/owner-orders/ATP_ORDER_CP_SB_UX.md (roadmap CP-UX, SB-UX, P1). This note comes first, as the order says:
benchmarks, then every item re-verified against `main` (d4a8240a; the order cites 124eafc, so lines were re-found by grep),
then the build order. "Confirmed" = the problem is in the code today; "not reproduced" = it is not.

## 1. Benchmarks (what the professional products do)

| Product | Pattern we take | Source |
|---|---|---|
| Perplexity Finance | A ticker page: the quote and key numbers first, the AI answer under them, sources linked; the "why it moved" line sits on the page | https://techpoint.africa/guide/perplexity-finance-review/ , https://aiwiki.ai/wiki/perplexity_finance |
| TradeZella Zella AI | One chat over the trader's OWN data: "Start my day" prep, trade review, session review; private to the account | https://help.tradezella.com/en/articles/11201153-what-is-zella-ai-tradezella-s-ai-trading-assistant , https://www.tradezella.com/blog/zella-ai-your-ai-trading-partner |
| TradingView AI Copilot | Lives on the chart: answers about THIS chart / symbol, draws levels, creates alerts; never says buy or sell | https://in.tradingview.com/support/solutions/43000795131-getting-started-with-ai-copilot/ , https://www.fxstreet.com/press-releases/tradingview-launches-ai-copilot-an-ai-assistant-that-works-directly-on-your-charts-202610201208 |
| Streak (Zerodha) | Three steps: Create → Backtest → Deploy; create = instrument / timeframe, entry conditions, exit, SL / target, then review | https://zerodha.com/z-connect/tradezerodha/streak/introducing-streak-v4 , https://zerodha.com/z-connect/streak/introducing-streak-algo-trade-without-coding |
| AlgoTest | Leg builder (segment, lots, side, option type, strike rule), risk settings per leg, then save → date range → run | https://docs.algotest.in/features/leg-builder/ , https://algotest.in/blog/how-to-backtest-algo-trading-strategies/ |
| Sensibull | Ready-made strategy cards (spreads, condors) as the start; payoff graph / table as the result | https://blog.sensibull.com/2018/08/17/announcing-custom-strategy-builder , https://blog.sensibull.com/2023/07/06/payoff-table-on-strategy-builder-analyse-widgets/ |

Common thread: numbers first, one primary action, the symbol always visible, a stepped builder with templates first,
theory behind a tap. Sources are vendor pages and reviews; layouts change, so screenshots go with each PR.

## 2. Re-verification on main (d4a8240a)

### Copilot (CU)

| Item | What the order says | Status | Where today |
|---|---|---|---|
| CU1a | SENSEX / BANKEX missing from `_INDEX_WORDS`; SENSEX in `_NOT_SYMBOLS` | confirmed ("Read the SENSEX chart" silently read NIFTY) | backend/app/ai/copilot_chat.py:231-236; `builder_symbol` :1378 |
| CU1b | Answers do not name the symbol read | confirmed (the default NIFTY was never said; brief / positions summaries name none) | copilot_chat.py `answer()` :1821 |
| CU1c (CP1) | Chart reader on broker candles per TF; drop "full reader is not in ATP yet" | confirmed (reads the shared lake + stored history, not broker candles) | backend/app/ai/tools/copilot.py:154-178; CHART_NOTE copilot_chat.py:43, used :710, :865, :1037 |
| CU2a | 10 job cards duplicated + "Ready" pills | confirmed | backend/app/ai/chat_routes.py:135-137 (`jobs`); frontend/src/copilot/chat/CopilotChatV2.tsx:137-156 (pill :144) |
| CU2b | Intro card | confirmed | CopilotChatV2.tsx:244 (`PageIntro`) |
| CU2c | 3D core in the hero | confirmed | frontend/src/pages/AiCopilotPage.tsx:68 and :132 (`AICore`) |
| CU2d | ~370-char footer | confirmed (347 chars; a second one at :435) | frontend/src/copilot/locales/en.ts:97 |
| CU2e | No numbers strip; question box not the only primary action | confirmed (one market line card, starters, job cards) | CopilotChatV2.tsx:102-117 |
| CU3 | Answer = text sections; strategy answer = theory sections | confirmed | frontend/src/copilot/chat/AnswerV2.tsx; copilot_chat.py strategy_builder :1391-1463 |
| CU4 | Seven separate tabs | confirmed | AiCopilotPage.tsx:28-34 |
| CU5 | No-key state is a card of fixed sentences before the page | confirmed (wording "Add a free AI key to start" / "short, fixed sentences") | copilot_chat.py:45-46; CopilotChatV2.tsx:90-101 |
| CU6 | Conversation lost on reload | confirmed (`turns` is component state only; no server store) | CopilotChatV2.tsx:198 |
| CU7 | Flag-off jobs shown as "Ready" | confirmed (`built` is sent regardless of JOB_FLAGS) | copilot_chat.py:424 (`JOB_FLAGS`); chat_routes.py:135 |

### Strategy builder (SU)

| Item | What the order says | Status | Where today |
|---|---|---|---|
| SU1 | Builder's own parse route + Idea Builder duplicate | confirmed | frontend/src/pages/StrategyBuilderPage.tsx:197 (`parseStrategyDescription`); frontend/src/copilot/tabs/IdeaBuilderTab.tsx:88 (interview flow) |
| SU2 | Timeframes 1-60 min only | confirmed | StrategyBuilderPage.tsx:31 (`TIMEFRAMES`), HTF :22 |
| SU3 | Price-action blocks unsupported | confirmed (divergence, candle patterns, demand / supply, BOS / CHoCH listed as unsupported) | backend/app/ai/strategy_builder.py:55-81 |
| SU4a | Prefilled 1.0 ATR / 1.5 / 2.0 R | confirmed | frontend/src/types.ts:737-739 (`defaultCustomStrategyConfig`) |
| SU4b | Interview adds risk rules | confirmed ("Professionals risk 0.5-1%" text; daily loss capped at 3 losses) | backend/app/ai/interview.py:100, :445-451 |
| SU5 | Result tiles lack ₹ / units | confirmed | frontend/src/pages/BacktestPage.tsx:543-551 |
| SU6 | No "Start on paper" after the result | confirmed (no link to the Paper bot from the result) | BacktestPage.tsx (grep: no deployments link near the result) |
| SU7a | Optimiser "Grid as JSON" | confirmed | BacktestPage.tsx:653-658 (parsed :87; params as JSON :675) |
| SU7b | Raw JSON in the Library | confirmed | frontend/src/pages/StrategiesPage.tsx:46-49 (`JSON.stringify(s.default_params)`) |
| SU8a | "deterministic, rule-based parser" / "no AI-provider credentials are configured" | confirmed | StrategyBuilderPage.tsx:236-237 |
| SU8b | Raw ids PLUS_DI / OR_HIGH / PDH; "id {strategy_id}" | confirmed | StrategyBuilderPage.tsx:19-20, :59, :301 |
| SU8c | Tables without scroll wrappers; `min-w-[260px]` | confirmed (tables :627, :669, :709 unwrapped; :589, :728, :769 already wrapped) | BacktestPage.tsx:627, :653, :669, :709 |

Not reproduced: none. All 15 items stand.

## 3. Build order (from the order)

CU1 → SU4 (DEF-0 leak) → CU2-CU3 → SU1-SU6 → CU4-CU7 → SU7-SU8. One PR per group, behind `ui_v2`, old pages kept under
Advanced until the owner approves removal.

## 4. CU1 in this PR, and what CP1 needs

- Built: SENSEX / BANKEX in `_INDEX_WORDS` (also "BSE Sensex", "BSE:SENSEX"), SENSEX out of `_NOT_SYMBOLS`; one
  `named_symbol()` for every job and `builder_symbol`; BSE exchange for the builder's backtest and the screen's alert;
  every answer carries `symbols` / `symbol_default` and names the symbol in its summary (the AI wording too); with no
  symbol in the question it says "You did not name a symbol, so I read NIFTY."
- Not built (CP1): the chart reader on the user's broker candles per timeframe. It needs (a) a decision whether broker
  candles replace or back the lake bars (BYOB budget, `live_access`), (b) the per-TF fetch in `get_chart_structure`
  through `price_action_layer` with a broker source, (c) the full Price Action reader port (Trade reader) before the
  CHART_NOTE line can go. A separate PR after the owner's ruling.
