# ADR-0019: Copilot agent - typed read-only tools, a bounded loop, an answer contract, proposals only

**Date:** 2026-10-11 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** H-C2

## Context
The Copilot is single-shot today: `provider.complete(system, user) -> str`.
- Facts are assembled by our code, then the model narrates them.
- It cannot ask for a backtest, an option chain or a regime.

The Copilot v2 spec (`docs/specs/ATP_COPILOT_SPEC.md`, §1-§2 C2) asks for an agent that acts only through typed,
validated tools. In that design:
- tools compute the numbers, not the model;
- tools are point-in-time (`as_of`);
- news and other third-party text is untrusted data that can never trigger a tool;
- actions are proposals a human approves (ADR-0006).

Every step is audited: agent action, prompt version and model version.

## Options
| | A: keep single-shot, add more fact builders | B: provider-native tool use behind our own loop | C: a hosted agent framework / remote agent service |
|---|---|---|---|
| Model chooses what to look up | no | yes, from a fixed registry | yes |
| Who runs the loop, budgets, audit | n/a | our code (`ai/agent.py`), per tenant | the framework |
| Tenancy, as-of, server-data rules | ours | ours, enforced in each tool | must be re-implemented around it |
| Rule-based (no key) path | yes | tool-less path kept | no |
| Provider lock-in | none | thin adapters (Anthropic tool_use, OpenAI tools) | high |

## Decision (provisional) - option B
1. **Tool registry (`app/ai/tools/`).**
   - Every tool declares: name, input JSON schema, output JSON schema, `kind` (`read` or `proposal`), cost units (the
     H-C1 b weights), timeout and `as_of` behaviour.
   - Tools are tenant-scoped: the tenant comes from the session, never from the model.
   - Read tools return `{data, as_of, source, data_timestamps}`.
   - Proposal tools return a proposal id from the monitor state machine (PROPOSED). No tool sends, modifies or cancels
     an order, and none changes LIVE settings.
   - The first set:
     - market: `get_candles`, `get_quote`, `get_option_chain`, `get_market_regime`, `get_sentiment`, `get_news`
       (wrapped untrusted), `get_global_cues`, `get_events_calendar`;
     - research: `run_backtest` (server data only, H-C1 a), `validate_dsl`, `run_scanner`;
     - account: `get_positions`, `get_orders`, `get_pnl`, `get_risk_limits`;
     - proposals: `propose_strategy_draft`, `propose_deployment` (PAPER only), `propose_risk_change`.
2. **Loop (`app/ai/agent.py`).**
   - Steps: plan, then tool calls (independent ones in parallel), then observe, then answer.
   - Hard limits come from config and the plan: max steps, max tokens, max ₹ per request, wall-clock timeout.
   - On hitting a limit, the loop answers from what it has, with a note.
3. **Injection guard.**
   - Untrusted text (news, uploaded text, tool outputs that carry third-party strings) is passed only inside
     `<untrusted_data>`.
   - A proposal tool is allowed only if (a) it is on the allow-list for the request's intent, and (b) the model's stated
     reason cites the trader's own message, not untrusted text.
   - Any other proposal call is refused and audited.
   - Eval set: injected headlines must produce zero proposal calls (H-C10).
4. **Answer contract.**
   - The final answer is JSON: `{text, claims[{statement, source_tool_call_id}], numbers[{value, source}], charts[],
     proposals[], disclaimers[]}`.
   - The verifier checks every number against tool outputs (grounding, H-C1 d), directions against regime/trend tool
     outputs, and the H-C1 c output filter.
   - A failed answer gets one retry, then the deterministic summary of the tool outputs.
5. **Audit.**
   - `agent_runs`: request, user, intent, prompt version, model, limits, outcome, cost.
   - `agent_steps`: each tool call with input, output hash, duration and cost; untrusted payloads stored by hash.
   - Both are append-only, linked to `llm_calls`, with retention like `llm_calls` (H-C1 e).
6. **Providers.**
   - `complete_tools(system, messages, tools) -> ToolTurn` is added to the provider seam (Anthropic `tool_use`, OpenAI
     `tools`).
   - The rule-based provider keeps the tool-less path: deterministic routing to the same tools, then rule text.

## Consequences
- **New tables.** `agent_runs` and `agent_steps` (migration with a down-migration).
- **Tools are code with tests.** Each tool gets a schema test, a tenancy test and an as-of test. The registry is also
  the future MCP surface (H-C12).
- **Cost.** It grows with steps; the per-request ₹ cap and the H-C1 b rate units bound it.
- **Behaviour change.** Answers change from "narrate our facts" to "fetch then answer". The old single-shot path stays
  as the fallback and for the rule-based provider.
- **Reversible.** A feature flag `ai_agent` (off by default) routes Copilot requests through the agent. With the flag
  off, behaviour is unchanged.
