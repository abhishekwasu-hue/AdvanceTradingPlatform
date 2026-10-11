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
