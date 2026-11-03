# Personal-data breach runbook (AUD-G1.2)

Source: owner order docs/owner-orders/ATP_ORDER_AUDIT_28OCT.md G1.2 ("add docs/runbooks/BREACH.md (72-hour Data
Protection Board notice)", "90-day SLA per DPDP Rules") and docs/design/AUDIT_28OCT.md (G1.2 row).

Placeholders in `[CAPITALS]` are for the owner and the lawyer to fill. This page holds no legal text: every notice's
wording, and anything marked "lawyer", is confirmed by the lawyer before use. Nothing here is run by a Claude session;
the owner runs every command on the server.

## Who

| Role | Who |
|---|---|
| Data fiduciary | [COMPANY NAME], [REGISTERED ADDRESS] |
| Incident lead (decides, keeps the timeline) | [INCIDENT LEAD NAME, PHONE] |
| Grievance officer (answers users) | [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL] |
| Lawyer (confirms every notice) | [LAWYER NAME, PHONE, EMAIL] |
| Hosting / broker contacts | [HOSTING PROVIDER SUPPORT CONTACT], [BROKER APP SUPPORT CONTACT] |

The same names go into the privacy notice (backend/app/legal/content/privacy.md: "Who is responsible" and "Grievance
officer").

## Clock

| When | What |
|---|---|
| T0 | Someone suspects personal data was read, changed, lost or sent where it should not be. Write T0 down. |
| T0 + 1 hour | Incident open in Admin > System > Incidents; containment started (below). |
| Within 72 hours | Notice to the Data Protection Board (order G1.2). Lawyer: when the clock starts, what the notice holds, and [BOARD NOTICE CHANNEL]. |
| [USER NOTICE TIMELINE - lawyer] | Notice to each affected user ([USER NOTICE TEXT - lawyer]). |
| Within 90 days | Every grievance about the breach answered (order G1.2, "90-day SLA per DPDP Rules"; the privacy notice's `[GRIEVANCE RESPONSE TIME]` must say the same). |
| [OTHER NOTICES - lawyer] | Whether any other authority must be told, and by when. |
| [POST-MORTEM DEADLINE] | Post-mortem written and shared (below). |

## 1. Detect

Signals that start this runbook:

- An `ops-check` alert to the operator's Telegram / e-mail (`atp.sh ops-check`, every 5 minutes; runbook
  docs/deploy/HOSTINGER_RUNBOOK.md section 10b), or `atp.sh health` showing something unexpected (an extra public port,
  a changed flag, a LIVE switch).
- The audit log: Admin > Compliance (audit-trail exports), Admin > Support > "One person's status" (recent sign-ins,
  broker connections), a user's own System Logs page (login history and security events).
- The AI call log (`llm_calls`, Admin > Users & business > AI cost) - the stored AI texts have personal data masked.
- A user, broker, hosting provider or outsider reports it (support ticket: Admin > Support).

Write down at once: T0, who reported it, what was seen, and where. Do not "test" by repeating the access.

## 2. Contain (stop it getting worse, keep the evidence)

Pick what fits; each step is reversible and none deletes data. Exits are never blocked (ADR-0004).

- Record first: Admin > System > Incidents > new incident (status OPEN). Every later step goes into its notes.
- New entries off for everyone: Admin > Controls > Global kill switch (open positions still exit).
- Close a way in: Admin > Controls > Feature flags - `self_signup` (sign-up), `public_api` (API keys), or the flag of the
  feature involved. Admin > Controls > maintenance when the whole site must pause.
- One account involved: `atp.sh reset-password EMAIL` (new password, signs out all its sessions); the user's own
  Account page > "Log out other devices".
- A leaked server secret: rotate it by name (HOSTINGER_RUNBOOK.md section 6d). `SECRETS_ENCRYPTION_KEY` is never rotated
  by atp.sh - follow section 6d and ask the lawyer whether stored broker keys must be treated as exposed.
- A broker token or key exposed: revoke it at the broker ([BROKER APP SUPPORT CONTACT]); users reconnect afterwards.
- A release caused it: `atp.sh rollback` (code only; section 9 says when a migration makes that unsafe).
- Never run `atp.sh` with `bash -x` (it would print `.env` values). Never paste personal data, logs or secrets into a
  chat, a public issue or this repository.

## 3. Assess (what, whose, how many)

Answer in the incident notes, facts only, with the source of each fact:

1. What data: which kinds from the privacy notice's list (account, settings, trades / orders, journal, AI history,
   broker connection status, support tickets).
2. Whose and how many: user count and organisation count; whether minors or LIVE accounts are involved (Phase 0 is PAPER
   only).
3. When: first and last moment of the exposure (audit log rows, `.deploy/logs/`, `atp.sh releases` for what ran then).
4. How: the cause, or "not known yet".
5. Harm to users: [HARM ASSESSMENT CRITERIA - lawyer].
6. Still going: yes / no, and why we know.

Is it a notifiable personal-data breach? Decided by the incident lead with the lawyer: [NOTIFIABLE BREACH CRITERIA -
lawyer]. If unsure, treat it as notifiable and keep the 72-hour clock.

## 4. Notify

| To | When | How | Text |
|---|---|---|---|
| Data Protection Board | within 72 hours | [BOARD NOTICE CHANNEL] | [BOARD NOTICE TEXT - lawyer] |
| Each affected user | [USER NOTICE TIMELINE] | [HOW USERS ARE TOLD - e-mail works only once SMTP is configured on the server] | [USER NOTICE TEXT - lawyer] |
| Status page (public) | when users are affected | the incident's public note (Admin > System > Incidents; one plain sentence, no personal data) | written by the incident lead |
| Others | [OTHER NOTICES - lawyer] | | |

Keep a copy of every notice sent, with the time it was sent, in the evidence store.

Grievances after a notice: logged as support tickets (Admin > Support), answered by the grievance officer within 90 days.

## 5. Evidence kept

Kept in [EVIDENCE STORE - private, access-limited location; never this public repository] for [EVIDENCE RETENTION
PERIOD - lawyer]:

- The incident record (Admin > System > Incidents) and its timeline.
- Audit-trail exports (Admin > Compliance) covering the exposure window.
- An encrypted, sealed backup taken after containment: `atp.sh backup --verify`. Routine backups are pruned
  (7 daily / 4 weekly / 6 monthly), so copy the one that covers the window to the evidence store before it ages out.
- `atp.sh releases`, `atp.sh health` output, and the relevant `/opt/atp/.deploy/logs/` files.
- The proof the audit chain is intact: `atp.sh restore-check` (it checks the chain on the newest backup).
- Every notice sent (section 4) and every user grievance with its answer.

Nothing is deleted while the incident is open.

## 6. Post-mortem

Written by [POST-MORTEM DEADLINE]; shared with the owner and the lawyer; kept with the evidence (not in this repository
when it names people or data).

1. Timeline from T0 (detect, contain, notify times against the clock above).
2. Root cause, and why the checks did not catch it earlier.
3. Data, users and organisations affected (counts).
4. Notices: to whom, when, on time or late.
5. Fixes: each with a roadmap ID (docs/ROADMAP.md) and an owner decision where it changes behaviour.
6. Changes to this runbook.

Then set the incident to RESOLVED with its root cause, actions, data loss and downtime (Admin > System > Incidents).
