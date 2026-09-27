# ADR 0009: Fine-grained scopes layered over roles, not replacing them

**Date:** 2026-09 (Phase N2) · **Status:** accepted

## Context
Roles (OWNER, USER, STRATEGY_CREATOR, VIEWER, SUPPORT, SUPER_ADMIN) are easy to reason about but
coarse: an owner could not let a trader manage alerts without also letting them store broker
credentials, or stop one trader from going LIVE while keeping paper access. V4.12 asks for scopes.

## Decision
`app/auth/scopes.py` defines a small catalogue (`trading:write`, `trading:live`, `brokers:write`,
`team:manage`, ...) and a role -> default scopes matrix. A tenant OWNER can deny a member scopes
the role has, or grant scopes it lacks, capped at the owner's own. The two historic gates
`require_trader` / `require_owner` now also check `trading:write` / `team:manage`; LIVE entries
check `trading:live`; credential storage checks `brokers:write`. SUPER_ADMIN holds every scope and
cannot be overridden. `/api/auth/me` returns the effective scopes so the UI can hide what a user
cannot do. Exits are never scope-gated (ADR 0004).

## Consequences
- Roles stay the mental model; scopes are the exception mechanism.
- Denials are enforced server-side at the gates, not only hidden in the UI.
- New write endpoints should use `require_scope(...)` or a scoped `require_role(...)`.
