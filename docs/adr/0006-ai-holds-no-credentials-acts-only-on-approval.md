# ADR 0006: AI providers hold no credentials and never act without approval

**Date:** 2026-08 · **Status:** accepted

## Context
Phase L added a strategy generator, a regime engine and a monitoring agent backed by an LLM
provider (Claude by default, OpenAI or a rule-based fallback). LLM output is untrusted; a prompt
injected through market news or a strategy name must not be able to place an order or read a
broker token.

## Decision
- Provider API keys are tenant data entered in Settings, encrypted like broker credentials, and
  used only to call the provider. Broker credentials are never part of any prompt or tool.
- The generator produces DSL documents only (ADR 0005); a draft must be backtested before it can
  be approved, and approval is a human action that records who approved.
- The monitoring agent may *propose* actions (pause, reduce size, stop) with evidence; a human
  decides, and proposals expire after 24 hours. The only automatic path is the existing risk
  hierarchy, which needs no AI.

## Consequences
- The AI layer can be switched off per tenant (plan feature, `ai_copilot` flag) with no effect on
  trading.
- Slower than an autonomous agent, by design.
