# Closed beta: invite codes, cohort cap, install metrics, feedback (PW6)

Beta spec section 4a, Phase 0: a free, PWA-only beta for 300 to 500 invited users. This page covers the three pieces
PW6 adds: the cohort gate on sign-up, the PWA install/update counters and the in-app "Send feedback" button.

## 1. The cohort gate

The gate is the feature flag **`beta_cohort_gate`**. It is **off by default**. While it is off, `POST /api/auth/register`
behaves exactly as before: the `self_signup` flag decides whether public sign-up is open, and any `invite_code` in the
request is ignored (not checked, not spent).

While it is on (and `self_signup` is on - with `self_signup` off nobody can register, gate or not):

- a sign-up must carry a valid invite code (`invite_code` in the JSON body; the sign-up form shows the field);
- the code must not be revoked, expired or used up (`used_count < max_uses`);
- the cohort must not be full: **cohort size = the sum of `used_count` over every invite code** (revoked and expired
  codes included), i.e. the number of accounts created through the gate. Accounts that existed before the gate went on,
  and teammates who join an organisation through an owner's team invite, are not counted;
- the cap is **`BETA_COHORT_CAP`** (environment variable, default **500**, accepted range 1 to 1,000,000; any other
  value falls back to 500);
- one use of the code is spent in the same database transaction that creates the account, so a sign-up that fails
  (duplicate email, weak password) does not spend it. On Postgres, redemptions are serialised with an advisory lock, so
  two sign-ups at the same moment cannot both take the last place or the last use of a code;
- the addresses in `SUPER_ADMIN_EMAILS` are exempt: they issue the codes, so they cannot need one.

Refusals are `403` with a machine-readable code and a sentence for the user:

| `detail.code` | Meaning |
| --- | --- |
| `beta_invite_required` | No code was sent |
| `beta_invite_invalid` | Unknown code, or not in the code format |
| `beta_invite_revoked` | The code was revoked |
| `beta_invite_expired` | The code is past its expiry |
| `beta_invite_used` | Every use of the code is spent |
| `beta_cohort_full` | The cohort has reached the effective cap, min(stage, `BETA_COHORT_CAP`) (section 1a) |

### 1a. Staged rollout (Z10-e): 50 -> 200 -> 500

The cohort grows in stages, `BETA_COHORT_STAGES` = 50, 200, 500. The gate stops at the **effective cap,
min(stage, `BETA_COHORT_CAP`)**. With the defaults (stage 50, cap 500) sign-up stops after 50 accounts.

**Release note (behaviour change with the gate on):** before Z10-e the gate stopped at `BETA_COHORT_CAP` (500). Now,
with no stage stored, it stops at min(50, `BETA_COHORT_CAP`). Move the stage up in Admin > Beta rollout (or set
`BETA_COHORT_STAGE`) to allow more. With `beta_cohort_gate` off (the default) nothing changes.

- The stage is moved in **Admin > Beta rollout** (or `PUT /api/admin/beta/cohort/stage`, SUPER_ADMIN with an
  MFA-verified session). It is stored in `platform_controls` (key `beta_cohort_stage`); no migration.
- **Up**: one stage at a time (50 -> 200, then 200 -> 500), with a reason of at least 3 characters. Skipping a stage or
  leaving out the reason is refused with `400` and changes nothing.
- **Down**: to any lower stage, no reason needed. Existing accounts are never touched; sign-up just stops sooner.
- Every move is written to the audit log: `beta_cohort_stage_raised` / `beta_cohort_stage_lowered`, with
  `<from> -> <to> (effective cap, BETA_COHORT_CAP, used): reason`. Setting the current stage again writes nothing.
- The **`BETA_COHORT_STAGE`** environment setting (default **50**; a value that is not a stage falls back to 50) is the
  stage only until one is set in the console.
- The admin card shows the stage, the effective cap and why (stage or `BETA_COHORT_CAP`), places used, the headroom
  left and when the stage was last set. The on-call page for moving up is `docs/OPERATIONS.md` 1.9.6.

### Turning it on

Admin console, or the API (SUPER_ADMIN with an MFA-verified session):

```
PUT /api/admin/controls/flags/beta_cohort_gate   {"on": true}
```

Turning it off again (`{"on": false}`) opens sign-up to everyone again (subject to `self_signup`).

### Invite codes

All endpoints are SUPER_ADMIN only and need an MFA-verified session, like the rest of the admin console.

| Call | What it does |
| --- | --- |
| `POST /api/admin/beta/invite-codes` `{"max_uses": 1, "expires_in_days": 30, "note": "wave 1"}` | Creates a code. **The plain code is in this response only.** `max_uses` 1 to 1000 (default 1), `expires_in_days` 1 to 365 (omit for no expiry), `note` up to 200 characters. |
| `GET /api/admin/beta/invite-codes` | Lists codes: id, note, max_uses, used_count, expires_at, revoked_at, created_by, created_at and `state` (active, revoked, expired, exhausted). Never the code, never its hash. |
| `POST /api/admin/beta/invite-codes/{id}/revoke` | Revokes a code; accounts already created with it are not affected. |
| `GET /api/admin/beta/cohort` | `{"gate_on", "cap", "used", "remaining", "stage", "stages", "next_stage", "env_cap", "capped_by_env", "headroom", "stage_source", "stage_set_by", "stage_set_at"}` - `cap` is the effective cap (section 1a) |
| `PUT /api/admin/beta/cohort/stage` `{"stage": 200, "reason": "wave 1 ran a week cleanly"}` | Moves the rollout stage (section 1a). Audited. |

A code looks like `K7QM-2XRA-PZ9D-H4WN`: 16 characters from an alphabet without look-alikes (no 0/O, 1/I/L), about
80 bits. Case, spaces and dashes do not matter when it is typed. Only the SHA-256 of the normalised code is stored
(table `beta_invite_codes`), so a lost code cannot be recovered - revoke it and issue a new one. Creating and revoking
codes, and each redemption (by code id), are written to the audit log.

## 2. Install and update metrics

The app reports a handful of PWA events to `POST /api/pwa/events` (no login; rate-limited to 30 per minute per IP).
The body is exactly two values from closed lists - anything else, including extra fields, is refused with `422`:

- `event`: `installed`, `launched_standalone`, `update_applied`, `install_prompt_shown`, `install_prompt_accepted`,
  `install_prompt_dismissed`;
- `platform`: `android`, `ios`, `desktop`, `other` (derived in the browser from the User-Agent).

Each event increments the Prometheus counter **`atp_pwa_events_total{event, platform}`** on the API process (scrape
`/metrics`). Nothing is written to the database. **Not recorded:** user id, account, organisation, IP address (the rate
limiter keeps a short-lived per-IP counter in memory or Redis and nothing else), User-Agent, app version, free text.

When the browser sends them (`frontend/src/pwa/events.ts`):

| Event | When |
| --- | --- |
| `installed` | The browser's `appinstalled` event |
| `install_prompt_shown` | The browser's `beforeinstallprompt` (it offered to install), at most once per browser session |
| `install_prompt_accepted` / `install_prompt_dismissed` | The outcome of that prompt, when something shows it (the PW1 install button); this module never shows or suppresses the prompt itself |
| `launched_standalone` | The app opened in standalone display mode (installed), once per browser session |
| `update_applied` | The installed app opened a build version different from the one this device last opened |

Sending is fire-and-forget: a failed request is ignored and never shown to the user. The counts are self-reported by
browsers and the endpoint is unauthenticated, so treat them as indicative (trends, ratios between platforms), not as
an exact census.

## 3. Send feedback

Logged-in users get a "Send feedback" button in the top bar. The dialog shows exactly what is attached before anything
is sent:

- **app version**: the build id (`package.json` version plus the short git commit when the build has one, e.g.
  `0.1.0+56f832a`; `dev` as a fallback);
- **screen**: the current path, e.g. `/option-chain` - the query string and fragment are always cut off (reset, invite
  and verification links carry tokens there), at most 200 characters.

`POST /api/feedback` (`message` 1 to 2000 characters, `app_version`, `screen`; rate-limited to 10 per hour per user)
stores one row in `feedback`: tenant id and user id (from the session, never from the request body - extra fields are
refused), the message, the app version, the screen path, the User-Agent cut to 200 characters, and the time.

**Not attached:** positions, orders, P&L, market data, broker details, settings, API keys, tokens, request bodies,
the page's query string.

SUPER_ADMIN reads it with `GET /api/admin/beta/feedback` (optional `?tenant_id=` and `?limit=`, newest first; includes
the sender's email so the team can follow up). Tenant owners cannot list feedback, their own organisation's included.
