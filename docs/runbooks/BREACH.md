# Personal-data breach runbook (AUD-G1.2)

Source: owner order docs/owner-orders/ATP_ORDER_AUDIT_28OCT.md G1.2 ("add docs/runbooks/BREACH.md (72-hour Data
Protection Board notice)", "90-day SLA per DPDP Rules") and docs/design/AUDIT_28OCT.md (G1.2 row). Extended by P3-4-B14 /
B16 (docs/design/P3_4_SECURITY_DPDP_CHECKLIST.md rows D16, D17, D18) with the DPDP Rules 2025 and CERT-In steps in
section 7.

Sources (the IDs of the checklist's section 0; [U] = unconfirmed / secondary there, and unconfirmed here too):

| ID | Source |
|---|---|
| S1 | Digital Personal Data Protection Act, 2023 (No. 22 of 2023) - s.8(6) breach intimation. https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf |
| S2 | DPDP Rules, 2025 - G.S.R. 846(E) - Rule 7 (breach intimation). https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf |
| S3 | [U] Shardul Amarchand Mangaldas alert - commencement dates. https://www.amsshardul.com/wp-content/uploads/2025/11/Regulatory-Alert-Enforcement-of-DPDP-Act-and-Notification-of-DPDP-Rules.pdf |
| S4 | [U] Reports of a MeitY proposal to cut the 18-month phase to 12 months; no amending notification found. https://ssrana.in/articles/meity-plans-to-cut-short-dpdp-compliance-timeline-and-notify-cross-border-restrictions-for-sdfs/ |
| S5 | CERT-In Directions under s.70B(6) IT Act, 28 Apr 2022 - directions (ii) 6-hour report, (iii) Point of Contact. https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf |

When the DPDP breach duties apply: Rule 7 is in the group that starts 18 months after the Rules were published
(13/14 May 2027 per S2 Rule 1 and S3 [U]). **Unconfirmed:** S4 [U] reports a proposal to bring this to 12 months
(about Nov 2026); no notification was found - re-check the Gazette before relying on May 2027. Until the lawyer says
otherwise, follow section 7 for every incident now. The CERT-In directions (S5) are already in force.

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
| Within 6 hours of noticing it (or of being told of it) | CERT-In incident report, if the incident is of a type CERT-In lists (S5 direction (ii)); section 7.4. |
| Without delay | First intimation to the Data Protection Board (S2 Rule 7(2)(a)); section 7.2. |
| Without delay | Notice to each affected user (S2 Rule 7(1)); section 7.1. |
| Within 72 hours | Notice to the Data Protection Board (order G1.2). Lawyer: when the clock starts, what the notice holds, and [BOARD NOTICE CHANNEL]. This is the detailed report of S2 Rule 7(2)(b) (or a longer period the Board allows on a written request); section 7.3. |
| [USER NOTICE TIMELINE - lawyer] | Notice to each affected user ([USER NOTICE TEXT - lawyer]). S2 Rule 7(1) says "without delay" (row above); the lawyer confirms what that means in practice. |
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
| CERT-In | within 6 hours of noticing (S5 direction (ii)) | [CERT-IN REPORTING CHANNEL AND FORM - from S5 / CERT-In's site, confirmed by the lawyer] | section 7.4 fields |
| Data Protection Board - first intimation | without delay (S2 Rule 7(2)(a)) | [BOARD NOTICE CHANNEL] | section 7.2 fields |
| Data Protection Board | within 72 hours | [BOARD NOTICE CHANNEL] | [BOARD NOTICE TEXT - lawyer]; the S2 Rule 7(2)(b) fields in section 7.3 |
| Each affected user | [USER NOTICE TIMELINE] (S2 Rule 7(1): without delay) | [HOW USERS ARE TOLD - e-mail works only once SMTP is configured on the server] | [USER NOTICE TEXT - lawyer] |
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

## 7. DPDP Rules 2025 and CERT-In steps (P3-4-B14, B16)

The fields below are what the sources ask for; they are templates to fill, not legal text. Every notice is confirmed by
the lawyer before it is sent (lawyer question L10 in the checklist: when "becoming aware" starts, the Board's channel
and form, the user text, and whether the PAPER-only phase changes any of this). The time each notice is sent goes into
the incident notes and the evidence store (section 5).

### 7.1 Notice to each affected user - without delay (S2 Rule 7(1))

Sent to every affected user, in a concise, clear and plain way, through their user account or the communication channel
they registered with us (in-app notification and / or e-mail; e-mail works only once SMTP is configured on the server).
It holds:

1. A description of the breach: its nature, extent, and the timing and location of its occurrence.
2. The consequences relevant to the user that are likely to arise from it.
3. The mitigation measures we implemented or are implementing to reduce the risk.
4. The safety measures the user may take to protect their interests (for example: change the password, log out other
   devices, reconnect the broker after revoking its token).
5. The business contact information of a person who can answer the user's questions on our behalf:
   [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL].

The privacy notice (backend/app/legal/content/privacy.md, breach paragraph) says the user and the Data Protection
Board are told "without delay" (S2 Rule 7(1); checklist row D17, P3-4-B15 wording fix). Known gap: there is no in-app
tool yet to send one notice to a list of affected users (P3-4-B15).

### 7.2 Data Protection Board - first intimation, without delay (S2 Rule 7(2)(a))

Sent as soon as the breach is known, without waiting for the full facts. It holds a description of the breach:

1. Its nature.
2. Its extent.
3. Its timing.
4. The location of its occurrence.
5. Its likely impact.

Channel and form: [BOARD NOTICE CHANNEL] (lawyer, L10).

### 7.3 Data Protection Board - detailed report within 72 hours (S2 Rule 7(2)(b))

Sent within 72 hours of becoming aware of the breach, or within a longer period the Board allows on our written request.
It holds:

1. Updated and detailed information on the description sent in 7.2.
2. The broad facts of the events, circumstances and reasons that led to the breach.
3. The measures implemented or proposed to mitigate the risk.
4. Any findings about the person who caused the breach.
5. The remedial measures taken to prevent it happening again.
6. A report on the intimations given to the affected users (7.1): who was told, when, and how.

If 72 hours will not be enough: [WRITTEN REQUEST FOR MORE TIME - lawyer], sent before the 72 hours end.

### 7.4 CERT-In incident report - within 6 hours (S5 directions (ii), (iii))

- Which incidents: those of the types listed in the CERT-In directions (S5); the incident lead decides with the lawyer
  whether this incident is one of them: [CERT-IN INCIDENT TYPE - lawyer]. If unsure, report.
- When: within 6 hours of noticing the incident or of being told about it (S5 direction (ii)).
- How: [CERT-IN REPORTING CHANNEL AND FORM - from S5 / CERT-In's site, confirmed by the lawyer].
- Point of Contact (S5 direction (iii)): [CERT-IN POINT OF CONTACT NAME, DESIGNATION, PHONE, EMAIL] - designated before
  go-live and named to CERT-In as the directions require.
- What it holds: the fields of CERT-In's own form; keep the facts in line with the Board intimation (7.2).
- Related (not part of the 6-hour step): S5 direction (i) on clock sync and direction (iv) on keeping ICT logs for 180
  days within India - checklist row A22, build item P3-4-B27.

### 7.5 Open points (unconfirmed until the lawyer answers)

- The date the DPDP breach duties start (S2 Rule 1, S3 [U], S4 [U]) - see the top of this page.
- When "becoming aware" starts the clocks, the Board's channel and form, the user notice text, and whether the PAPER-only
  phase changes any of this (L10).
- Which incidents CERT-In's list covers for ATP, and its channel and form (7.4).
