# ADR-0020: AI evals in CI and AI governance - golden sets per prompt version, a model registry, a kill switch

**Date:** 2026-10-11 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** H-C10, H-C11

## Context
Every LLM call now carries a hash-based prompt version (H-C1 f), but nothing measures a prompt or model change before
it ships (review gap G4).

SEBI expects:
- human oversight of AI output (AI/ML consultation, June 2025);
- full responsibility of the regulated entity for that output (10 Feb 2025);
- disclosure of AI use (RA Regulation 19(vii)).

FINRA's 2026 agentic expectations ask for an audit trail of every agent action.

## Decision (provisional)
1. **Evals live in the repo** (`backend/evals/`), with golden sets as data (JSONL):
   - (a) Q&A over facts: grounding pass rate, no advice words, answer language;
   - (b) tool-use tasks: the right tools and parameters, no forbidden calls;
   - (c) DSL generation: valid JSON, validator pass, compliance pass;
   - (d) injection set: headlines carrying instructions must give zero proposal calls;
   - (e) leakage: no tool returns data after `as_of`.
2. **Two runners.**
   - **CI (every PR):** a fake/scripted provider plus deterministic checks. It exercises the pipelines (verifier, filter,
     injection guard, tool schemas) and gates on regressions against the committed baseline per prompt version.
   - **Nightly (flag, budget cap):** the real provider. It writes per-(prompt_version, model) scores. An LLM-as-judge
     rubric is used only where a deterministic check cannot decide, and the judge's own prompt is versioned too.
3. **Gate.** A PR that changes a prompt (its version hash changes) must update that prompt's baseline in the same PR.
   A score below the baseline minus a tolerance (config) fails CI. Baselines are never tuned to a single run's result.
4. **Model registry** (`ai_model_registry` table + admin page):
   - each row holds provider, model, prompt versions, eval scores, approval date and approver;
   - a model or prompt not approved for a task runs only in shadow mode: it answers into the log and is never shown.
5. **Kill switch.** An `ai_features` operator flag works per tenant and globally. Off means every AI path falls back to
   the rule-based answers immediately.
6. **Disclosure.**
   - The UI states which model is used, for what, and its limits.
   - `docs/AI_GOVERNANCE.md` lists the human-oversight points:
     - draft approval;
     - candidate deploy;
     - monitor proposals;
     - LIVE step-up.
   - A research-analyst-boundary flag covers specific actionable ideas shown to subscribers. Whether to offer them is
     a business decision for the owner.

## Consequences
- CI gets an eval job of a few seconds; it uses fake providers and spends no money.
- The nightly job spends money within a cap the operator sets, and is off until set.
- Prompt changes become slower to merge, which is the point.
- The registry and shadow mode add one table and one admin page (H-C11).
