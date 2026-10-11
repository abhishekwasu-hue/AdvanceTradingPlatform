# Part H - ATP AI Copilot v2: design note and plan

Source: `docs/specs/ATP_COPILOT_SPEC.md` (Abhi, after a review of main fd217a4 and outside research). This note details
MASTER SPEC part H. The same working rules apply:
- ADR-0004 (exits never blocked) and ADR-0006 (AI never places orders) stand;
- no merge and no LIVE change without the owner;
- every change comes with a test.

**Naming.** The spec numbers its parts C1..C12, which collides with MASTER SPEC part C (backtest realism, C1..C6).
In code, docs and PR titles the Copilot parts are called **H-C1 .. H-C12** (open question H-4).

## Review findings, checked against main (fd217a4)
| # | Finding | Checked | Where it is fixed |
|---|---|---|---|
| G1 | Single-shot `complete(system, user) -> str`; no tool use | yes (`app/ai/providers.py`) | H-C2 |
| G2 | Approval evidence runs on client-supplied candles | **yes**: `POST /api/ai/drafts/{id}/backtest` runs `body.candles`; the interview plan and the strategist accept `candles` too | **H-C1 a** |
| G3 | No server-side advice/guarantee filter on LLM text | yes (only the UI string lint) | **H-C1 c** |
| G4 | No evals; prompt_version missing on copilot/knowledge/thesis | yes | H-C1 f (prompt_version), H-C10 |
| G5 | No rate limit on `/api/ai/*` | yes (only Telegram inbound has one) | **H-C1 b** |
| G6 | No conversation memory | yes | H-C5 |
| G7 | Keyword-substring intent router | yes | H-C9 |
| G8 | Grounding treats numbers in the user's question as allowed; no check on bullish/bearish claims | yes | **H-C1 d** |
| G9 | No data timestamps in the context | yes | H-C2 (tool outputs carry `as_of`) |
| G10 | No streaming; dynamic facts in the system prompt break caching | yes | H-C8, H-C9 |
| G11 | `llm_calls` keep raw text forever, no PII redaction, not cleared on erasure | yes | **H-C1 e** |
| G12 | Keyword-only retrieval; wrong `monitor.py` docstring | yes | H-C7, H-C1 f |
| G13 | No explain-this-trade, no charts in answers, no voice | yes | H-C6, H-C8 |

## Order
- **H-C1 (safety)**
- **H-C2** (tool core and audit), with **H-C10** (evals) built alongside it
- H-C3 (research loop)
- H-C9 (router and cost)
- H-C5 (memory)
- H-C6 (explain and coach)
- H-C8 (UX)
- H-C4 (multi-view analysis)
- H-C7 (retrieval)
- H-C11 (governance)
- H-C12 (MCP)

Each part goes: design note or ADR, then build, tests, self-review, and a draft PR. ADR-0019 (agent tools and loop) comes
before H-C2. ADR-0020 (AI evals and governance) comes before H-C10 and H-C11.

## H-C1 plan (one PR per item where they grow)
- **a. Server-side evidence only.** Status: **built** (broker session; lake source once part B merges).
  - The draft backtest, the interview plan and the strategist fetch candles on the server: from the market-data lake
    (B5 `history`) when it has the window, otherwise through the tenant's broker session.
  - A request's `candles` field is ignored, and the response says so (`candles_source`). After one release the field
    is removed.
  - Sample mode stays available for demos. Its runs are stamped `data_source=sample` and can never satisfy the approval
    gate.
  - Test: client candles change nothing, and a sample-data run cannot approve.
- **b. Rate limit.** Status: **built**. `/api/ai/*` gets a limit per user and per tenant, with plan-wise limits in config. It uses Redis
  where Redis is reachable and falls back to an in-process window (the same pattern as Telegram inbound). Heavy jobs
  (strategist, market study) count as N units. Test: the limit is enforced, it resets, and the per-plan values apply.
- **c. Output filter.** Status: **built** (copilot, guide, thesis; generator and scanner text with d). `app/ai/output_filter.py` checks every LLM text output on the server.
  - The advice and guarantee word list is config (en + mr: recommend, guaranteed, sure-shot, खात्रीशीर, हमखास, …).
  - A hit gets a neutral rewrite, or falls back to the rule text, and is flagged in the audit.
  - A specific buy/sell/strike call gets educational framing plus a disclaimer.
  - Tests in both languages.
- **d. Grounding.** Status: **built** (copilot, thesis, generator explanation, scanner read; the guide is vocabulary and is exempt from the direction check).
  - Numbers in the user's question are tagged `user_provided`, never `verified`.
  - A directional claim (bullish/bearish) must match the regime/trend facts.
  - The number check is extended to the generator's explanation and the scanner's free text.
- **e. llm_calls hygiene.** Status: **built** (rows never deleted; text masked, scrubbed on erasure, retention off by default - H-6).
  - Retention in days (config) through the retention job.
  - PII redaction (emails, phones, account ids) before storage.
  - Profile erasure clears the user's rows.
  - The DPDP note goes in OPERATIONS.
- **f. Housekeeping.** Status: **built** (hash-based prompt versions; a CI scan keeps every caller stamped). Fix the `monitor.py` docstring, and give every LLM call a `prompt_version`.

## Acceptance (from the spec)
- A low-VIX NIFTY question gives a cited answer.
  - Every number in it comes from a tool.
  - It contains no advice words.
  - Any proposal it makes is a PAPER draft only.
- An injected headline leads to zero action tool calls (eval).
- Client-supplied candles cannot produce an approval (test: H-C1 a).
- The research loop records N trials in its ledger, and its report gives deflated results.
- The eval suite runs in CI, and a prompt change shows up as a regression.
- Streaming gives the first token in under 2 s (cached system prompt).

## Open questions (provisional answers taken, work continues)
- **H-3. Does H-C1 jump the queue?** MASTER order puts H after G1-G2, and the spec keeps "the same ordering rule".
  - Provisional: **yes, for H-C1 only.** Its items are safety bugs: G2 lets fabricated evidence pass the approval gate,
    G5 is an abuse/cost hole, and G3 is a compliance exposure. They are small and touch only `app/ai`.
  - H-C2 onwards stays in MASTER order.
- **H-4. Naming.** Use H-C1..H-C12 to avoid clashing with part C. Provisional.
- **H-5. Server data when the lake has no window and the broker session is down.** Provisional: the draft backtest
  answers 409 ("no server data for this window"). It never falls back to client data.
- **H-6. llm_calls text retention.** P0.8-D keeps every LLM call forever for the audit, while the spec asks for a
  retention in days.
  - Provisional: rows, hashes and costs are never deleted. Personal data is masked at write time. The text is scrubbed
    on the trader's erasure / "forget me". Age-based scrubbing exists (`RETENTION_LLM_TEXT_DAYS`) but is off by default.
  - Deleting data is the owner's decision (§14), so the period is left to the owner.
- **H-7. Agent proposals: which actions, and the late-news gap.** ADR-0019 lists `propose_strategy_draft` and
  `propose_deployment` (PAPER) as well.
  - Provisional: this slice ships only de-risking proposals (pause, tighter risk, review). They reuse the monitor's
    approve/execute path unchanged.
  - Draft and deployment proposals need a new execute path, so they wait for the owner's answer.
  - The untrusted-text quote check covers news read before the proposal call. (a) and (b) always apply, so the
    trader's message must ask for the action in every case.
- **H-8. Plain-text agent answers.** Provisional: they are accepted without claims (grounding and the filter still
  apply), and the evals measure the rate. Strict JSON-only can follow once the rate is known.

## H-C2a (built): tool registry, bounded loop, audit - read tools only
- **Tools.** `app/ai/tools/` is a typed registry:
  - each tool has a pydantic input model whose JSON schema has `additionalProperties: false`, a kind, cost units and a
    timeout;
  - the tenant comes from the session (an extra argument such as `tenant_id` is refused);
  - every result is `{data, as_of, source, data_timestamps}`;
  - news results are `<untrusted_data>`, escaped.
  - The first tools are `get_market_snapshot`, `get_news`, `get_positions`, `get_pnl_today` and `get_risk_limits`.
- **Loop.** `app/ai/agent.py` stops on max steps, max tool calls, wall time or a provider error.
  - Tools run one at a time on the request's session, because an AsyncSession is not safe for concurrent use.
  - The answer contract: every number must come from the tool outputs or the question (H-C1 d), and the H-C1 c filter
    applies. A failing answer gets one rewrite; after that the deterministic data summary is shown.
- **Provider seam.** `AnthropicProvider.complete_tools` uses provider-native tool use with our tool specs. The assistant
  content, thinking blocks included, is sent back unchanged. `MeteredProvider.complete_tools` meters and logs every
  step. Providers without tool use (the rules, and OpenAI until H-C2b) fall back to the ordinary Copilot answer.
- **Audit.** One `agent_runs` row per request (question as a hash, prompt version, model, limits, outcome) and one
  `agent_steps` row per tool call (arguments, output hash, ok, duration). Both tables are in `NEVER_DELETED`, like
  `llm_calls`.
- **Route.** `POST /api/ai/agent/ask` sits behind the new `ai_agent` flag, which is off by default, and keeps the
  existing acknowledgement gate and AI rate limit.
- **H-C2b next:** proposal tools with the injection guard (built below), then the JSON answer contract (claims with
  their source tool call), an OpenAI tools adapter, and candles, quote, chain and backtest tools.

## H-C2b-1 (built): proposal tools and the injection guard
- **Tools** (`app/ai/tools/proposals.py`, kind `proposal`). Each one only files a PROPOSED row in the monitor's
  `ai_actions` queue (ADR-0006), through `monitor.raise_proposals`. That gives the same notification, dedupe, 24 h
  expiry and approve/reject flow as the monitor's own proposals.
  - `propose_pause_deployment`: PAUSE_DEPLOYMENT, rule `AGENT_PAUSE`; the deployment must be this organisation's and
    ACTIVE.
  - `propose_risk_reduction`: REDUCE_RISK, rule `AGENT_RISK`. Only a tighter value is accepted: lower for risk %, loss
    %, counts and drawdown levels; higher for min R:R and cooldown. On approval the action is acknowledged and the
    person changes the setting on the Risk page; no setting is changed automatically.
  - `propose_strategy_review`: REVIEW_STRATEGY, rule `AGENT_REVIEW`.
  - Nothing here sends, modifies or cancels an order, and nothing touches a LIVE setting.
- **Guard** (ADR-0019 §3):
  - (a) Intent allow-list. A proposal tool is offered to the model only when the trader's own message asks for that
    kind of action (English and Marathi words, `INTENT_WORDS`). Text inside a headline cannot add a tool.
  - (b) The call's `quote` must be the trader's own words. It must be a piece of the message, contain the words that
    allowed the tool, and not also appear in untrusted text read during the request.
  - (c) At most one proposal per request (`AgentLimits.max_proposals`).
  - `run_tool` refuses a proposal tool unless the loop has cleared it, and `register` refuses a proposal tool without
    `quote` and `reason`.
  - A refused call is told to the model as `guard: ...`, stored on the `agent_steps` row, and written to the audit log
    as `agent_proposal_refused`. A created one is logged as `agent_proposal_created`.
- **Route.** `POST /api/ai/agent/ask` returns `proposals` (id, action). They are decided under AI Copilot like any
  other proposal.
- **Tests.** `tests/test_hc2b_proposals.py`. An injected headline ("pause deployment N") produces zero proposals in
  three variants:
  - the trader did not ask;
  - the quote was taken from the headline;
  - the trader's words also appear in the headline.
- **Limit (provisional, H-7).** The untrusted-text check sees only data read before the proposal call. When the model
  reads news after proposing in the same turn, only checks (a) and (b) protect that call. Both still require the
  trader's own message to ask for the action.

## H-C2b-2 (built): the JSON answer contract and the OpenAI tools adapter
- **Contract** (ADR-0019 §4). The final answer is one JSON object, `{text, claims[{statement, source}],
  disclaimers[]}`, where `source` is the id of a tool call in this request. It is parsed by `agent.parse_answer`,
  which tolerates a ```json fence. The checks are:
  - every number in the text comes from the tool outputs or the question (H-C1 d);
  - each claim cites a successful call of this request, and its numbers come from that very call's result;
  - the output filter runs on the text, the claims and the disclaimers.
  - A failure gets one rewrite (the model is told which claim and why), then the data summary.
  - The route returns `claims`, `disclaimers`, `agent.contract` and each tool call's `id`, so the UI can cite sources
    (H-C8).
- **Plain text (provisional, H-8).** A plain-text answer is still accepted, as text without claims
  (`contract: "plain"`); grounding and the filter still apply. H-C10 evals will measure how often it happens, and
  strict mode can follow.
- **OpenAI** (ADR-0019 §6). `OpenAIProvider.complete_tools` uses Chat Completions function tools.
  - The loop's Anthropic-shaped messages are translated: assistant blocks become `tool_calls`, and each `tool_result`
    becomes one `tool` message.
  - The reply comes back as Anthropic-shaped blocks, so `agent.py` stays provider-neutral.
  - Arguments that are not valid JSON are refused by the tool's input model.
  - A `length` stop or an HTTP error is a `ProviderError`.
  - `MeteredProvider.supports_tools` now turns on for OpenAI as well.
- **Tests.** `tests/test_hc2b2_contract_openai.py` (7).

## H-C2b-3 (built): market and research read tools
- `app/ai/tools/market.py` adds five tools: `get_candles`, `get_quote`, `get_option_chain`, `get_market_regime` and
  `run_backtest`.
- **Server data only.** Candles come through `evidence.server_frame`, the H-C1 a path through the organisation's broker
  session. The quote and the chain come from the same session. No tool accepts candles; an extra argument is refused by
  the input model.
- **No broker session.** Every tool fails closed with the fix location (Settings > Brokers). None falls back to sample
  data.
- **Small outputs.**
  - candles: the range, the change and the last 5 bars;
  - chain: totals, PCR, max pain, the top 3 OI strikes each side and ±5 strikes around the ATM;
  - backtest: statistics only, plus `sample: insufficient` below the compliance minimum (30 trades) and a note that the
    results are simulated.
- **Backtest execution.** It runs off the event loop (`asyncio.to_thread`) under a 45 s timeout, with the
  organisation's risk settings.
- **Tests.** `tests/test_hc2b3_market_tools.py` (6) covers schema, no-session, as-of, chain math, backtest and an agent
  answer that cites `get_candles`.
- **Still open from ADR-0019's tool list:** `get_sentiment`, `get_global_cues` and `get_events_calendar`
  (market_snapshot already carries the sentiment and cues), `validate_dsl`, `run_scanner`, and `get_orders`. These are
  for H-C3, the research loop.
