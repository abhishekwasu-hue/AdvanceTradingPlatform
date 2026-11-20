# ADR 0029: The owner's trading agent runs in `human_confirm` or `auto` mode

**Date:** 2026-11-20 · **Status:** accepted (owner decision AGENT-MODES, DECISIONS 20 Nov); build pending (SRV2 P3)

## Context
ADR-0006 keeps every AI action behind a human approval. The owner builds ATP to trade his own account automatically:
the chart reader (his method), the bot, Vision and the Telegram card already exist (Trade bot; SRV2 port into ATP).

## Decision
- The trading agent of a deployment has a mode, chosen by the user in that deployment's settings:
  - `human_confirm` (the default when created): the agent sends a Telegram card with the setup, the area and his own
    settings; only his ✅ places the order; no ✅ before the card expires = no entry.
  - `auto`: the bot places the entry itself when his rules (and Vision, when on) agree, on his own settings only.
- Switching to `auto` is his explicit action, recorded in the audit log with who and when; it is never a default.
- Unchanged from ADR-0006 and the platform rules: exits are never blocked (ADR-0004); provider keys never see broker
  credentials; prompts and Vision output are untrusted input that can only add a NO (filter), never invent a rule he
  did not state; the risk hierarchy and kill switch apply in both modes; LIVE stays the owner's own switch.
- Other users: `auto` stays off for every other organisation until the owner decides (and legal advice before LIVE).

## Consequences
- The same code path serves PAPER and LIVE and both modes; the mode only decides who presses "place".
- Tests: a `human_confirm` card that expires places nothing; `auto` places exactly what `human_confirm` would have
  proposed for the same bars (parity); switching modes is audited.
