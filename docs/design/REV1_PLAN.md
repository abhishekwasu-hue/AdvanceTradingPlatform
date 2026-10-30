# REV-1 — plan for docs/reviews/ATP_FULL_REVIEW.md (waits for the owner's approval)

Owner 30 Oct: work through the full review as one roadmap group: High first, then every Medium page by page, then Low;
"plan and take approval first then start". Nothing below is built before the owner approves this plan.

## Rules for every fix
- Root cause in the shared function (one fix for every page that uses it), self-review of every diff, tests on the
  server's real data shapes (real endpoint answers, not hand-made objects), PAPER only, exits never blocked, AI never
  places / blocks / changes orders, no trading rule the owner did not state, nothing deleted.
- Proof: before / after screenshots, desktop + 375 px phone, in ONE comparison page per milestone (M1, M2, M3).
- Rule-§3 items: values come only from the user's own settings; blank = off / not chosen (DEF-0); the platform pre-picks nothing.
- One draft PR per group below; merge on green CI; report by REV-1 ID with the deploy line and migration yes / no.

## M1 — High (5 items, first)
| ID | Finding | Fix (root cause) |
|---|---|---|
| REV-H1 | Every 409 on POST /deployments reads "already runs on paper" | The page shows the server's own reason (no instrument master / no broker / duplicate) through the shared error-sentence helper; one plain sentence per server reason, with the next step. |
| REV-H2 | A stopped bot blocks a new bot forever (unique key ignores status; no delete on the v2 page) | (a) The unique key covers only bots that are not STOPPED (partial unique index; **migration**); a stopped bot stays as history. (b) The v2 Paper bot page gets "Remove" on a stopped bot (the existing DELETE, with a confirm). See decision D1. |
| REV-H3 | Seller packs (F1/F2) on an index start as SINGLE BUY ATM without defaults; a swing bot never gets a structure | The quick form presets no side / structure: with no user default, the pack's structure fields are empty and must be chosen before Start (no BUY ATM fallback); the swing path takes the structure the same way as intraday (tradingDefaults.ts:252). |
| REV-H4 | Terminal P&L wrong when another member holds the same symbol | Exposure is read with the same scope as the positions (the caller's own trades when `mine=true`), fixed in the backend exposure read and the shared `heldRows` price; test with two members holding one symbol. |
| REV-H5 | Risk limits cannot be saved while any limit is "Not set" | Draft of a null limit = blank; blank saves as "Not set" (null); only typed text is validated; plus the two Risk Mediums (0 means "blocks every entry" said in the help; the guardian sentence only for levels that are set). |

## M2 — Medium, page by page (order as in the review)
Paper bot / Signals (pause-reason text + its test; phone chip + button text) · Charts (server reason shown instead of
"check the symbol") · Option chain (no-session vs plan; stale numbers marked old with the real reason) · Sectors &
indices (real retry or honest text) · Custom scan (no raw exception text, no provider / model / prompt ids, swing window
from settings, no 0-100 coloured score) · Plan an options trade (lots from the user's settings, no default width, the
"Place as basket" button honest) · My trades (journal save error shown) · Risk (in M1) · Performance (own trades only,
PAPER not "Live", readable strategy names, Tax on PAPER in Phase 0) · Journal (no design-note text) · Coach (strategy
names) · Strategies (no raw JSON, no platform stop / target shown - decision D2) · Build (delete confirm + error,
no developer text) · Test (intraday unticked unless the user's own holding says so; no pre-filled structure / ATM / width
/ legs; the user's own risk settings sent or the claim removed; no developer text; list-load error) · News (no flag
name, delete error) · Company research (no sample inputs; honest header) · Settings › Alerts (no SMTP / DLT internals
outside Advanced) · Setup ("Start a paper bot" opens the form with nothing pre-chosen - decision D3) · Notifications
(readable event names) · Team (LIVE wording hidden in Phase 0; no permission codes).
NOT built: "directional seller both sides" - logged as REVQ-1 in OPEN_QUESTIONS (default: no change).

## M3 — Low
Developer text and jargon; page titles = sidebar names (My trades, Money, Performance, Plan an options trade, Custom
scan); phone tables in scroll wrappers (Performance, Team); polling and unbounded reads (/trades limit + paging, Today
summary re-read, duplicate list reads, News per keystroke, Notifications refetch); loading / error states (Journal,
Strategies, Account, Notifications, Company research); IST times; Cancelled orders not under "Rejected"; Signals source
filter; Settings header avatar cut at 375 px.

## Needs live check (proved first, fixed only when proved)
The site's data cannot be read from this session and nothing is run on the server by the session. One read-only command
(`atp.sh review-checks`, built in M1, prints counts only: no writes, no personal data) answers all ten checks: byob flag;
members with open trades in one symbol; /trades rows per tenant; AI read texts with order words; risk rows with 0;
members per organisation; strategy ids in history; ai_one_key; news rows; the six flag states. Abhi runs it once after
the M1 deploy; each check item is then fixed or closed by its result.

## Owner decisions (defaults used if not answered)
- D1 Stopped bot: partial unique index (stopped bots kept as history) + Remove button. Default: yes (migration, after 15:30 IST).
- D2 Ready-made strategies carry their own stop / target numbers (atr_mult_sl, target_rr, min_rr). Default: hide them
  and use only the user's own stop / target (blank = off). This changes how those bots exit - built only on the owner's yes.
- D3 Setup "Start a paper bot" / Test "intraday": default: no one-tap bot (opens the form, nothing pre-chosen); intraday
  unticked unless the user's own setting says intraday.

## Estimate
M1 about 4 hours, M2 about 10 hours, M3 about 4 hours (up to 4 agents in parallel, each on its own branch).
