# Security

## Threat model (summary)
| Asset | Threat | Control |
|---|---|---|
| Host, secrets, other tenants | Malicious Strategy Lab code | Separate process; Linux: `unshare -n -m` (no network, private /tmp,/var/tmp,/dev/shm), `setpriv` to uid/gid 65534 with `no_new_privs`, rlimits (CPU, memory, file size 0, open files), empty environment, random work dir under a non-listable parent; app `data/` and `.cache/` are mode 0700. Python adds restricted builtins, import allow-list and an audit hook. Self-test verifies isolation empirically and execution is disabled if it fails. |
| Accounts / private data | Cross-tenant access | Every private table has `user_id`; Store methods are scoped by the caller's id; API tests verify 404 for other users' resources. |
| API | Unauthenticated / abusive use | Bearer tokens (only SHA-256 hashes stored), roles admin/analyst/viewer, per-key rate limit, CORS allow-list, pydantic validation, size limits, safe error bodies with request ids. |
| Billing | Forged entitlement | Entitlements change only from HMAC-verified webhook events; idempotent by provider event id. |
| AI layer | Hallucinated facts / prompt injection | LLM receives only the computed evidence JSON; any number not present in the evidence causes rejection; the LLM cannot change status, score, risk or data verdicts; daily call budget. |
| Users' AI API keys (BYOK) | Theft, cross-user use, leakage in logs/responses, company-funded usage | AES-256-GCM at rest bound to user+provider, master key from `AMW_SECRET_KEY`/0600 file, decrypt only in the gateway for the owner's request, masked display, never returned by the API, log/exception redaction, provider-format checks, no env/company key code path (test-enforced), session-only option, delete control. See docs/AI_BYOK.md. |
| Signals, jobs, reports, alerts | Cross-tenant access (found and fixed this release: SEC-001..005) | Owner columns and owner-filtered queries, job ownership, per-user alert evaluation and delivery, owner-only report files; regression tests. See docs/autonomous/SECURITY_FINDINGS.md. |
| Telegram | Token theft, wrong recipient | Official bot token only in the operator environment; user bot tokens encrypted and masked; chat linking via one-time code; no Telegram account credentials ever requested |
| Broker data connection (FYERS) | Credential/token theft, login CSRF, cross-user data, account blocks | App secret + access token AES-256-GCM bound to user and purpose, never returned, App ID masked; one-time hashed OAuth state bound to the user (15 min); token/secret/auth code redacted; bars/status/stream owner-filtered; one collector per account (database lease) and a shared daily rate-limit guard. See docs/LIVE_DATA_FYERS.md. |
| Trading | Unintended orders | No order, position or funds endpoints exist; the FYERS client calls only the login and market-data history URLs; tests assert both. |

## Known limitations (honest)
* **Windows local trusted mode** has no OS sandbox. In-process guards are defence in depth, not a security boundary.
  Use it only for your own code on your own PC. Multi-user deployments must use Linux/Docker with a verified sandbox.
* The Docker sandbox requires `CAP_SYS_ADMIN` and `apparmor:unconfined` for the app/api containers. This widens the
  container's privileges; isolate the host (dedicated VM), keep the image minimal, and consider moving the Lab workers
  into a separate, network-less container (gVisor/Firecracker) before accepting untrusted public users.
* Rate limiting is in-memory per process (single instance). Use a gateway limiter for multiple instances.
* FYERS: the per-minute request budget is per process; two FYERS-sourced scans started at the same time in two
  processes can together exceed it (the collector itself is single per account). The FYERS integration was verified
  against a mocked API only.
* Desktop (Streamlit) mode is a single local admin user without login; expose it only on localhost or behind an
  authenticating reverse proxy.
* Unit tests are not proof of sandbox security; review runtime configuration and repeat the self-test after any
  OS/base-image change.

## Checklist before going live
- [ ] `.env` created from `.env.example`; no secrets in the repository; `POSTGRES_PASSWORD` strong.
- [ ] `AMW_SECRET_KEY` generated and stored in a secret manager (and backed up separately from database backups).
- [ ] TLS reverse proxy; ports 8501/8000 not publicly exposed directly.
- [ ] `GET /api/v1/lab/capabilities` shows `sandbox_status.verified = true` on the server.
- [ ] Admin token created with `python -m src.api.manage create-user <name> admin` and stored in a secret manager.
- [ ] `AMW_CORS_ORIGINS` restricted; `AMW_AUTH_MODE=token`.
- [ ] Backups scheduled and a restore tested (docs/OPERATIONS.md).
- [ ] CI green (lint, tests on Linux with sandbox + PostgreSQL, Windows), `pip-audit` reviewed.
