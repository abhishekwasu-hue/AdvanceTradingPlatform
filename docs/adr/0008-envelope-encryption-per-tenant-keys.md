# ADR 0008: Envelope encryption: one wrapped data key per tenant

**Date:** 2026-09 (Phase N1) · **Status:** accepted

## Context
Until Phase N every secret (broker credentials, alert channel tokens, AI keys, MFA seeds) was
Fernet-encrypted directly under one master key from `SECRETS_ENCRYPTION_KEY`. Rotating that key
meant re-encrypting every row, and a bug that decrypted one tenant's row could decrypt any row.
Master prompt section 48 asks for per-tenant envelope encryption and a secret-manager path.

## Decision
`tenant_keys` stores one random Fernet data key per tenant, wrapped by the master key. New
ciphertexts are `t1:<tenant_id>:<token>` under the tenant key; legacy tokens still decrypt. Keys
are unwrapped into an in-process ring on demand (`ensure_tenant_key`), warmed at startup, on every
authenticated request, per worker tenant, per alert channel and per webhook tenant. A write with a
cold ring falls back to the master format rather than failing. `scripts/reencrypt_secrets.py`
upgrades legacy rows per tenant and re-wraps all keys when the master changes (idempotent, resumable).
The master itself stays an environment secret so any secret manager (Vault, AWS/GCP secret stores,
Docker/Kubernetes secrets) can supply it; no provider SDK is baked in.

## Consequences
- Master rotation is O(tenants), not O(secrets), and touches no credential row.
- A tenant's key can be dropped (`forget`) or, later, held in an HSM/KMS without changing callers.
- The ring must be warm before a sync decrypt; the entry points above guarantee that, and a cold
  decrypt fails loudly with an actionable message instead of returning garbage.
