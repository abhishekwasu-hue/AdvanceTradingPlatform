# M72 review: the AI Copilot rebuild against the code (CLAUDE.md §6, before any build)

Order: docs/owner-orders/ATP_ORDER_M72.md (CP-0..CP-11). Base: `origin/main` 86fe4e40 (1 Nov). Read by grep + line ranges.
Builds nothing. CP-0 is built in parallel on `claude/m72-cp0-eval`; §5 says what it should reuse.

## 1. What runs today (facts from the code)

| Part | Where | What it does |
|---|---|---|
| Chat route | `backend/app/ai/chat_routes.py:147-178` | `POST /api/ai/chat` (flags `ui_v2` + `ai_copilot`, Copilot terms accepted): `copilot_chat.answer` builds a RULE answer, then `narrate` re-words it only when a provider is ready |
| Keyword router | `backend/app/ai/copilot_chat.py:200-231` | 10 fixed jobs (`JOBS` :63-87), keyword lists; refusals first (`refusal_kind` :175-189) |
| AI wording | `copilot_chat.py:2080-2130` | `CHAT_PROMPT` (≤120 words, FACTS only); `copilot.grounded` (numbers + tickers + direction + banned words, `copilot.py:80-95`), output filter, `advice_in` for describe-only jobs; 2 attempts, else rules |
| Agent loop | `backend/app/ai/agent.py:150-260` | real tool-calling loop (ADR-0019): ≤6 steps, ≤12 tool calls, 60 s, JSON answer contract with claims citing tool-call ids, one rewrite, audit rows `agent_runs` / `agent_steps`. Route `POST /api/ai/agent/ask` (`routes.py:880-905`), flag `ai_agent` (+`ai_copilot`). NOT used by the chat page |
| Tool registry | `backend/app/ai/tools/__init__.py:1-120` | typed pydantic tools, `read` / `proposal` kinds, per-tool flag, `agent=False` = rules-only; tenant + user from the session, never from the model |
| Tools (23) | `tools/read.py:85-90`, `copilot.py:229-235`, `copilot_trades.py:236-239`, `market.py:304-310`, `screener.py:463-467`, `proposals.py:155-157` | market, news, positions, P&L, risk limits, context, structure, zones, chain (+analyses), my trades, weekly review, candles, quote, regime, backtests, screens, 4 proposal tools |
| Provider choice | `backend/app/ai/resolve.py:1-17,52-55` | flag `ai_one_key` (needs `ui_v2`): 1) the user's OWN key, 2) the organisation's key (`settings.provider_for`, `settings.py:70-97`), 3) rules |

## 2. Per item: exists / missing / risks

### CP-0 Eval first
- Exists: the eval runner and gate (above); `tests/test_hc10_evals.py`, `test_copilot_v2_*` read the sets; new eval files are pinned by sha256 in `scripts/check_public_repo.py:114` (ALLOWED_TEST_DATA).
- Missing: real trader questions in MR / HI; the "answered / correct / useful / format" score; a live-model run (runner.py:17-19 says the real-provider runner is "a later slice").
- Risk: the CI gate stays deterministic (recorded tools + scripted turns); a live score is opt-in with a spend cap (§5).

### CP-1 Real agent
- Exists: `agent.run_agent` + registry + audit + JSON claims contract + injection guard - the planner the order asks for is mostly built, only not wired to the chat page and OFF (`ai_agent`).
- Missing: (a) the chat route calling the agent (today: rules first, LLM only re-words); (b) tools for orders, user settings (Trade settings registry `me/trade_settings.py`), journal entries, Greeks / payoff / what-if, the M66 engine read, the watchlist; (c) calc tools for derived numbers (F6); (d) a platform key (F1); (e) a per-user daily cost cap for a non-own key (F2); (f) fallback "rules only when the model is down" (today the rules ALWAYS run first).
- Risks: F3 (tenant-scoped tools), F5 (weaker answer check in the agent), F6, F15 (data path), cost per answer unmeasured (F20).

### CP-2 Answer format
- Exists: headline row, tables (`blocks` with typed columns, `copilot_chat.py:533-535,615,891`), structure card, embedded chart with levels, folded "How I worked this out", Markdown subset, one `followup`.
- Missing: an answer contract where the MODEL picks which tool results become cards (code renders every number from the tool data, the model writes only the headline and short lines); several follow-up chips ("Show on chart", "Make this an alert", "Backtest this", "Open in Terminal"); an annotated chart with strikes; phone-first check of every card.
- Risk: a model-written table bypasses grounding unless the cards are built from tool-call ids (F7b).

### CP-3 Context everywhere
- Exists: page context in the shell (`askAiEntry.tsx:14-25`), Copilot link `copilotPath(symbol)` -> `/copilot?symbol=X` (:79-80), chain Ask AI (`optionChain/AskAiV2.tsx`).
- Missing: the Copilot page never reads `?symbol=` (no `useSearchParams` in `copilot/` or `AiCopilotPage.tsx`) - the context is dropped today (F8); `ChatBody` has only `message`, `wording` (`chat_routes.py:40-44`); no object context (chain row, position, order, signal, screener result, journal entry); no "Ask AI about this" on Positions / Orders / Signals / Screener / Journal.
- Risk: a context object sent by the client must be re-read on the server for this user (ids only, never client numbers - the `evidence.require_server` rule, `ai/evidence.py:29-43`).

### CP-6 Options-seller toolkit
- Exists: selectors (`options_builder/selectors.py:30-272`: iron condor / butterfly / credit spreads / ITM), payoff + combined Greeks (`payoff.py:13-66`), value curves with IV / days shift, POP, expected move (`options_builder/model.py:1-20`), margin estimate (`metrics.py:64`, PAPER has no SPAN - said so), strike breach model (`strike_breach.py`), chain analyses tool, expiry checklist + combined exit in the frontend (`fno/expiryChecklist.ts`, `fno/combinedExit.ts`), ticket / basket previews (`paper/fno_routes.py:160,544`).
- Missing: Copilot tools over these (they are HTTP routes taking a client chain body); the strike finder "by the user's own rules" - the registry has `strike_rule`, `strike_offset`, `delta_tolerance`, `strike_search_steps` (`me/trade_settings.py:205-240`) but no target delta, no premium band, no OI-wall rule, and the Options section is M68 PR-4 (not wired yet).
- Risks: F12 (pre-chosen values in the selectors / Greeks rate); adjustment scenarios must be numbers only, never "do this".

### CP-4 Chart understanding
- Exists: chart reader job on ATP's zone layer (`tools/copilot.py:154-183`), M59 chart attach in chat (`ChartAttachV2.tsx`, `ChartTurnV2.tsx`), Analyze a chart with the private analysis boundary + hybrid (`chart_analyze/provider.py:1-17`, flags `chart_analyze` "unfinished", `chart_analyze_hybrid`), chart PNG (`ai/chart_png.py`), M66 engine contract (`engine_provider/contract.py:125-289`: areas, swings, higher side, signals, data problems; flag `trade_engine` admin / owner only, `flag_catalog.py:144`; the provider is absent until the M65 snapshot + atp-private tag).
- Missing: an engine read tool for the Copilot; the annotated image back in chat (areas, structure, nearest areas, levels).
- Risks: F9 (private boundary + "invalidation level" as a single price).

### CP-5 Plain English -> tools
- Exists: job 9 screens (`build_screen` / `run_screen` / `explain_results`, `propose_alert` as a PROPOSED row), job 6 strategy draft + draft backtest (values only from the trader's words, `backtest_draft` agent=False, `market.py:309`), Pine subset in Strategy Lab (`docs/design/PINE_SUBSET.md`).
- Missing: custom indicator drafts from chat; one editable-draft card type for all five; the agent offering these (screener tools are behind `screener_copilot`).
- Risk: the model must never fill a value the user did not state (the CP6-2 rule, `tools/__init__.py` `agent` field) - keep `backtest_draft` rules-only.

### CP-7 Proactive watch
- Exists: notifications + channels (in-app, Telegram, e-mail, push; `alerts/channels.py`, `alerts/push_kinds.py` - phone pushes carry no symbol / price, MA-7), alert rules engine (`alerts/engine.py`, `barclose.py`), OI alerts (`option_chain/oi_alerts.py`), daily rhythm brief / pulse / wrap (`rhythm/build.py`, flag `daily_rhythm`), monitor proposals (`ai/monitor.py`).
- Missing: opt-in per alert type for: near the user's own SL / target, short-strike distance shrinking, delta / IV jump, OI shift at his strikes, a signal on his watchlist; short cards with "Ask why"; a personal brief / wrap from HIS positions and watchlist.
- Risks: F13 (`ai/briefing.py` day-type framing and hidden sizing lines), F14 (monitor's EXIT proposals execute on approval - not to be reused), Telegram carries prices: only the user's own data, to his own chat.

### CP-8 Coach
- Exists: `get_weekly_review` (flag `discipline_engine`), journal review + stats + tags + checklist + manual trades (`journal/review.py`, `stats.py`, `routes.py:38-409`), `advice_in` describe-only check (`journal/review.py`), post-trade coach job.
- Missing: rule adherence vs the user's OWN saved rules / checklist, mistakes by tag, best / worst setups as a table, questions back.
- Risk: a review must not instruct ("next time ...") - keep `advice_in` on all coach text.

### CP-9 Ticket preparation
- Exists: the ticket (`fno/OrderTicketV2.tsx:44-141`: `useOrderTicket({target, side})`, `newDraft(side, defaults)`, `draftFromDefaults`), the basket store (`fno/basket.ts:34-59`, browser storage), previews on the server (`paper/fno_routes.py:160`, `paper/ticket_routes.py:90`). No AI code path reaches an order call: the only one is `monitor.execute` after a human approval (`ai/monitor.py:356-371`).
- Missing: a way to open the ticket from outside with a prefill (no URL / intent entry); a record of where each field came from.
- Risks: F11 (no model-chosen values), ADR-0006 (`docs/adr/0006-...md`): the Copilot prepares, the user presses Place; PAPER / LIVE: the same ticket the Terminal opens for the account (`fno_routes.py:6` has no LIVE path today; LIVE stays the owner's switch).

### CP-10 Languages + voice
- Exists: nothing. English is forced: `agent.py:47,153`, `routes.py:148-162` (language accepted and ignored), `output_filter.py:12-15,38` (Devanagari fails closed), `users.ai_language` DROPPED (`alembic/versions/e6b8d0f2a4c7_drop_user_ai_language.py`, EO-4). The strategy interview is trilingual from fixed translations (`ai/interview.py`). No voice input anywhere (no SpeechRecognition in `frontend/src`).
- Risk: F4 / F16; MR / HI advice wording has no filter list today.

### CP-11 Memory
- Exists: per-user chat history (`ai/chat_history.py`, `copilot_chat_turns`, delete under Settings › My data), per-user JSON prefs with an allow-list of namespaces (`me/prefs.py:514`, `user_prefs` per tenant + user), Settings › AI path (`frontend/src/settings/paths.ts:17`).
- Missing: a learned-preferences namespace, a Settings › AI card to show / edit / delete it.
- Risk: never across users; never read by an order path or a bot.

## 3. Findings (fix before or during the build)

1. **F1 `ai_one_key` is not a platform key.** It means "the user's own key in one paste" (`resolve.py:1-17`); an operator-wide key does not exist (every provider needs a key row, `providers.py:946-958`; `settings.provider_for` reads only the organisation's row). The Free plan never calls out (`settings.py:82`). CP-1 "works out of the box with the platform key (flag ai_one_key)" needs a NEW provider source (an operator secret on the host, never in the repo), plan budgets including Free, and must not silently change the meaning of a flag that is "ready" (OKT-9). -> M72Q-1. Fix in PR-1b.
2. **F2 No per-user daily cap except on an own key** (`byok.py:293-351`); the organisation key has only a monthly per-organisation INR budget (`metering.py:40-110`). CP-1 needs a per-user / day cap (operator ceiling + the user's own lower cap) checked before every model call, like `UserKeyProvider._check_cap` (`resolve.py`). PR-1b.
3. **F3 Tenant-scoped tools are offered to the agent.** `get_positions` / `get_pnl_today` filter by `tenant_id` only (`tools/read.py:58,69`) and are `agent=True`: in an organisation with several members one member's Copilot reads the others' positions and P&L. The chat jobs use the user-scoped `copilot_trades.py:119-124`. Scope them to `user_id` (or mark `agent=False`) BEFORE the agent becomes the default. PR-1a.
4. **F4 Languages contradict a standing ruling.** CP-10 asks MR / HI answers; EO-4 dropped `users.ai_language` and CLAUDE.md §2 says "English only in code and UI (strategy interview: EN / MR / HI)". The output filter rejects every Devanagari answer, so MR / HI answers would all fall back to rules. Needs: the owner's M72 read as superseding EO-4 for Copilot ANSWERS only (UI stays English), language in a `user_prefs` namespace (no migration), MR / HI (Devanagari + romanised) advice lists, numbers and symbols kept in Latin digits. -> M72Q-2. PR-10.
5. **F5 The agent's answer check is weaker than the chat's.** `agent._verify` (`agent.py:126-148`) checks numbers, claim sources and the output filter, but not tickers, direction or banned words that `copilot.grounded` checks (`copilot.py:80-95`). One combined check for every AI answer. PR-1a.
6. **F6 Derived numbers need calc tools.** Grounding accepts only numbers present in tool data or the question (`grounding.py:45-105`, with rounding variants). Distance to a strike, points to his SL, a combined P&L would all be dropped. Add deterministic `calc_*` tools (recorded as `calc:` steps, the `_calc` pattern `copilot_chat.py:511-518`), never let the model compute. PR-1c.
7. **F7 Cards must come from tool-call ids, not model text.** (a) A model-written table or chart level escapes the per-claim check. (b) The CP-2 contract: the model returns a headline, short lines and `cards: [{tool_call_id, view}]`; code renders the numbers from that call's result. PR-2.
8. **F8 Context is dropped today.** `/copilot?symbol=X` is linked (`askAiEntry.tsx:79-80`) but nothing in `copilot/` reads it; `ChatBody` has no context field. Only 2 pages register context. PR-3.
9. **F9 Engine output and the private boundary.** The M66 contract carries model ids, an internal invalidation reference, evidence tokens and the user's engine settings (`engine_provider/contract.py:125-268`). These are method content: never into an external model's prompt, the public evals, fixtures or screenshots (atp-private CLAUDE.md). Only the generic, public words go to the model (area range, state, side, structure). CP-4's "invalidation levels" must follow CLAUDE.md §3 (area as a price range, no stop wording); today's card already shows a "protected level". The engine tool exists only where `trade_engine` is on (owner only now). -> M72Q-3. PR-5.
10. **F10 Several answer stacks** (`copilot.copilot_answer`, chat jobs, `option_chain/ask_ai.py`, `screener/scanner/ai.py`). Keep all (§8); the agent plans the chat, the jobs become the fallback and composite tools.
11. **F11 Ticket prefill values.** The ticket has no outside entry. A prefill may carry only: the user's saved Trade settings, numbers he typed in that message, and the object he opened Ask AI from (e.g. the chain row). Everything else stays "Not set" and the ticket asks (M68). Never a model-chosen strike, lots or SL. A test: no Copilot route or tool imports an order-placing function; the ticket's Place is the only path. -> M72Q-4. PR-9.
12. **F12 Pre-chosen values the toolkit would reuse (M68).** `SuggestBody.pop_threshold_pct=70`, `strikes_otm=2` (`options_builder/routes.py:152-153`) are not in the TS1 audit; the Greeks' risk-free rate is "fixed 7 %" (`me/trade_settings.py` `risk_free_rate_pct`); the agent's `run_backtest` uses `RiskConfig()` and `PAPER_STARTING_CAPITAL` (`tools/market.py:257`; M68Q-4 says capital Not set). Add them as TS1 rows; Copilot tools pass only the user's values, else ask. -> M72Q-5. PR-4 (CP-6) and PR-1c.
13. **F13 `ai/briefing.py` breaks two rules.** Day-type framing ("A volatile day", "Event today: position size is cut automatically", "ATR-based stops are wider", `briefing.py:121-161`) is day framing (CLAUDE.md §5) and a hidden sizing rule (M68). CP-7's personal brief / wrap is built on `rhythm/` + the user's own positions / watchlist, not on `briefing.py`; flag briefing.py's lines as a TS1 row.
14. **F14 Proactive watch is information only.** `monitor.execute` closes positions on APPROVED proposals (`monitor.py:356-371`, allowed by ADR-0006). CP-7 adds no new proposal kinds; cards carry "Ask why" only; exits never affected (ADR-0004).
15. **F15 Data path (M71Q-2).** With `byob_data_tier` OFF the market tools read through the organisation's primary broker record (`tools/market.py:81-94`, `evidence.py:57-61`) - any member reads through one credential. The M71 store reader is compliant (`market_store/reader.py:1-12`: own rows only; owner's rows only for the owner). Every new tool reads via `market_store.reader.read_bars` / `live_access.user_source` only. Also: sending broker market data to an outside AI provider is not covered in docs/BROKER_DATA_TERMS.md (no mention) - add it to the PL-5 terms check. PR-1a / PR-1c.
16. **F16 Number words in MR / HI.** `grounding._MULTIPLIERS` knows Latin `k` / `lakh` / `crore` only; the Devanagari words for lakh, crore and thousand are not read (fail closed: answer dropped). Rule: digits and symbols as in the tool data (the order says "numbers and symbols unchanged"), checked. PR-10.
17. **F17 Proposal intent words are English** (`tools/proposals.py:62-65`): an MR / HI ask cannot unlock a proposal (safe); fixed MR / HI phrases in PR-10 with injection cases.
18. **F18 Missing read tools for CP-1:** orders (user-scoped), user Trade settings (registry), journal entries, watchlist, Greeks / payoff / what-if on the server chain, engine read. Each tool: user-scoped, typed, flag, timeout, cost units, `as_of`.
19. **F19 Prompt hash.** `baselines.json` is tied to the agent prompt version: each PR that edits the planner prompt re-baselines and re-pins its sha256 in the same PR (ADR-0020 §3).
20. **F20 Cost per answer is unmeasured on the agent path**; limits are fixed in code (`AgentLimits` `agent.py:60-66`: 6 steps, 12 calls, 4000 tokens / step). Move them to admin settings; each PR posts cost per answer (`metering.spent_by`, `metering.py:226`) from an opt-in capped live run.
21. **F21 docs/OPEN_QUESTIONS.md is 106 KB** (rule ≤ 30 KB; tail rows M71Q-2, M71Q-5, M68Q-6..8 appear twice). A docs PR moves provisional rows to the archive (never deleted); not done here (parallel branches).
22. **F22 "Rules only when the model is down"** inverts today's order (rules first, `chat_routes.py:153-160`). Keep the rule answer as the fallback AND as the "AI is off" mode for a tenant with no key at all; the page text "AI is off" stays only there.

## 4. Build plan (one PR at a time, owner's order; R2: at most 5 build items in progress)

Every PR: eval table before / after, phone + desktop screenshots (one comparison page), cost per answer, tests (grounding, no-advice, no order without a click), deploy line + migration; behind `ui_v2`.

| PR | Item | Files (main) | Flag | Migration | Tests |
|---|---|---|---|---|---|
| PR-0 | CP-0 eval set (other agent) | `backend/evals/sets/*`, `runner.py`, `baselines.json` | - | no | gate |
| PR-1a | CP-1 safety fixes first (F3, F5, F15) | `ai/tools/read.py`, `ai/agent.py` (`_verify` = one combined check with `copilot.grounded`), `ai/tools/market.py` (`_broker` / `_frame` via user source / store) | none (fixes) | no | member A cannot read B's positions; ticker / direction / banned-word cases in `qa.jsonl`; no tenant-primary read when byob is on |
| PR-1b | CP-1 platform key + caps (F1, F2) | `ai/resolve.py` (source "platform" after the own key), `ai/settings.py`, `ai/metering.py` (per-user day cap), `plans/registry.py` (budgets read from env, no number in code), Admin flag catalog | new `ai_platform_key` (M72Q-1) | no (caps in `platform_controls` + `user_prefs`) | cap hit = hard stop then rules; Free plan behaviour per M72Q-1; key never logged |
| PR-1c | CP-1 planner in the chat (F6, F10, F18, F22) | `ai/chat_routes.py` (agent first when a tool-capable provider is ready; rules on provider error), `ai/agent.py` (planner prompt, limits from admin settings), new user-scoped tools: `get_my_orders`, `get_my_trade_settings`, `get_journal_entries`, `get_watchlist`, `calc_*`; existing tools reused | new `copilot_agent` (needs `ai_copilot`) | no (`agent_runs` / `agent_steps` exist) | eval gate; every tool `kind` read / proposal only; static test: no `app.ai` module imports an order-placing function; fallback when the provider raises |
| PR-2 | CP-2 answer format (F7) | `ai/agent.py` (contract: headline, lines, `cards[{call_id, view}]`, `chips[]`), `copilot/chat/AnswerV2.tsx`, `model.ts`, `RichText.tsx`, annotated chart via `EmbeddedChartV2` levels / strikes | `copilot_agent` + `ui_v2` | no | a card number not in its call's result is refused; no raw Markdown (`hasRawMarkdown`); phone 360 px snapshot |
| PR-3 | CP-3 context (F8) | `chat_routes.ChatBody.context {page, symbol, timeframe, object{kind,id}}` (server re-reads own rows), `copilot/chat/CopilotChatV2.tsx` (read `?symbol` / context), `useRegisterAskAi` on Positions, Orders, Signals, Screener results, Journal, chain rows | `copilot_agent` | no | another user's object id -> 404; client numbers ignored |
| PR-4 | CP-6 options-seller toolkit (F12) | tools over `options_builder/selectors.py`, `payoff.py`, `model.py` (what-if spot / IV / days), `metrics.margin_estimate`, `strike_breach.py`, expiry checklist + combined exit computed server-side (port of `fno/expiryChecklist.ts`, `combinedExit.ts` logic as calc tools, frontend kept); reads `me/trade_settings.py` Options fields (M68 PR-4) | `copilot_agent` + existing `options_builder*` | no (prefs JSON) | a Not set value -> the answer asks, never fills; same numbers as the Options Builder page |
| PR-5 | CP-4 chart reading (F9) | `get_engine_read` tool (only with `trade_engine` + provider installed; public words only), zone-layer fallback, M59 hybrid in chat (`chart_analyze`), annotated PNG (`ai/chart_png.py`), TradingView link via the existing items route | `chart_analyze_hybrid`, `trade_engine` | no | no engine token / model id in any prompt or response (test with the synthetic fake provider); "area of interest" wording guard |
| PR-6 | CP-5 plain English -> drafts | one editable draft card for screen / indicator / alert / strategy / backtest; reuse `build_screen`, Pine subset, `propose_alert`, `strategy_builder`, `backtest_draft` (rules-only) | `screener_copilot`, `copilot_agent` | no | nothing saved or run without the user's tap; unstated values empty |
| PR-7 | CP-7 proactive watch (F13, F14) | worker evaluator over the user's own trades / strikes / watchlist / signals; opt-in per type in Settings › Alerts (`user_prefs`); `notifications.notify` cards with "Ask why" deep link; brief / wrap on `rhythm/` | new `copilot_watch` | likely no; yes only if a dedupe table is needed (said in the PR) | opt-in off = nothing sent; no proposal rows; push text without symbol / price (MA-7) |
| PR-8 | CP-8 coach | journal tools + weekly table (adherence to his own checklist, tags, best / worst setups), questions back | `discipline_engine`, `copilot_agent` | no | `advice_in` on every line; numbers from journal rows |
| PR-9 | CP-9 ticket prefill (F11) | frontend intent `{target, side, legs, fields_from}` -> `OrderTicketV2` / basket; source chip per field; server unchanged (previews only) | new `copilot_ticket` (owner default ON per order) | no | no Place without a click (UI test); a model-only value is rejected; PAPER / LIVE same ticket |
| PR-10 | CP-10 languages + voice (F4, F16, F17) | `user_prefs` namespace `ai` (language), planner prompt per language, MR / HI filter lists, Latin-digit check, browser speech input (opt-in, phone) | new `copilot_languages` | no | MR / HI advice cases fail; digits unchanged; interview untouched |
| PR-11 | CP-11 memory | `user_prefs` namespace `ai_memory` (instruments, timeframes, style; learned from own settings / chats), Settings › AI card show / edit / delete; deleted with "Delete my Copilot conversation" | new `copilot_memory` | no | never read across users or by an order path / bot |

## 5. CP-0: what the eval set should reuse (no eval files created here)

- One entry: `evals/runner.py` - add the set beside `run_copilot_jobs` (:201) / `run_copilot_safety` (:270); keep `gate` (:301) and `baselines.json` (tolerance 0.0 = "never fall"); pin new files' sha256 in `scripts/check_public_repo.py:114`.
- Recorded, synthetic tool results via `evals/build_copilot_fixtures.py` -> `copilot_tools.json` and `recorded_runner` (:173); no real broker data, nothing from his own signals or the private engine (F9).
- Case keys as `copilot_jobs.jsonl` (`question`, `expect_job`, `expect_tools`, `expect_facts`, `model_answer`) plus the M72 scores: correct = facts present + numbers grounded; format = headline + cards + no raw Markdown; useful = a rubric (person or opt-in live run, never the CI gate).
- Today's score = `copilot_chat.answer` on the recorded runner (the rules Copilot without a key); MR / HI questions route by English keywords only (`copilot_chat.py:200-231`) - an honest low baseline.

## 6. Owner decisions (full text and defaults: rows M72Q-1..5 in docs/OPEN_QUESTIONS.md)

- M72Q-1 platform key (F1, F2): new flag `ai_platform_key`; owner's organisation only until he sets plan budgets + the per-user daily cap.
- M72Q-2 languages (F4): M72 supersedes EO-4 for Copilot answers only; UI stays English.
- M72Q-3 engine output to an outside model (F9): generic public words only.
- M72Q-4 ticket prefill values (F11): saved settings, his typed numbers, the opened object; rest Not set.
- M72Q-5 strike-finder fields (F12): added to M68 PR-4 as Not set; listed defaults become TS1 rows.
