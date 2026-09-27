# ADR 0001: Row-level multi-tenancy with a server-derived `tenant_id`

**Date:** 2025-11 (recorded 2026-09) · **Status:** accepted

## Context
The product is a SaaS: many organisations (tenants) share one deployment. Options were a database
per tenant, a schema per tenant, or shared tables with a tenant column. Broker credentials and
trade history are the most sensitive data the platform holds, so isolation has to be provable, but
the operator is small and the plan/billing model needs cross-tenant queries (admin console, usage
metering, platform-wide kill switch).

## Decision
Shared tables. Every tenant-owned row carries `tenant_id`, and that value is **always derived
server-side from the authenticated user** (`get_current_user` -> `user.tenant_id`), never accepted
from the client. Every read and write filters on it. Registration creates a tenant with the
registering user as OWNER; teammates join only through an owner's invite.

## Consequences
- One migration path, one backup, cross-tenant admin queries are ordinary SQL.
- Isolation is a code discipline, so it is tested: cross-tenant tests exist for every module that
  stores data, and a 2026 bug where paper-trading state leaked across tenants was fixed by exactly
  this rule (`tests/test_paper_isolation*`).
- Per-tenant encryption keys (ADR 0008) add a cryptographic boundary on top of the row filter.
