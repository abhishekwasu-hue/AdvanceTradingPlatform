# Journal module - progress

Spec: `docs/specs/ATP_JOURNAL_MODULE_SPEC.md`. Plan and PR list: `docs/journal/PLAN.md` (section 7). Open questions:
`docs/journal/OPEN_QUESTIONS.md`.

Owner decision (2026-10-11): the module is planned now and built **after Phase 0** (Z1-Z11), in the N-list next to N2.
Phase 0 order does not change.

| Milestone | What | Status | PR |
|-----------|------|--------|----|
| 0 | Plan: spec copied, mapping onto the current ATP code (extends the Z6 journal package), conflicts with standing rules resolved, PR sequence, open questions, roadmap entry | **done** | this docs PR |
| 1 | Scaffold: ADR, migrations, flags, plan gating, i18n shell (English only), empty UI shell (J1-a..J1-d) | after Z11 | - |
| 2 | Fills -> positions -> groups, charges wrapper, file import (Zerodha, Upstox, generic), manual entry, broker API sync (Upstox, Zerodha), ATP PAPER bridge (J2-a..J2-e) | after Z11 | - |
| 3 | Blob store, automatic charts, trade page, replay (J3-a..J3-c) | after Z11 | - |
| 4 | Strategies, trade story, rulesets and checklist, text AI review, BYOK vision review, discipline dashboard (J4-a..J4-c) | after Z11 | - |
| 5 | Trade plan, plan vs actual, guardrail alerts, Telegram and email delivery (J5-a..J5-c) | after Z11 | - |
| 6 | Analytics completion, mistake taxonomy, event calendar, heatmap, streaks, weekly ritual (J6-a, J6-b) | after Z11 | - |
| 7 | Plans and billing (existing Razorpay provider), tax report, exports, verified P&L card (J7-a..J7-c) | after Z11 | - |
| 8 | Mentor mode, remaining brokers, hardening (J8-a..J8-c) | after Z11 | - |

## Log

- 2026-10-11 - Milestone 0. Read Z6-a (#171) and Z6-b (#172) and the existing modules the spec touches (billing and
  Razorpay, plans, broker adapters, BT1/BT1b, `india_costs`, contract notes, tax, lake, `chart_png`, BYOK, AI
  filters, Telegram links and outbox, mailer, `market_events`, workers). Wrote the plan, open questions JM-1..JM-34 and
  the roadmap entry. No application code. Next: nothing until Z11 merges; then J1-a.
