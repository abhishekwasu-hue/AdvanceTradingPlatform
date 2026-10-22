From Abhi — ATP MASTER ORDER 3: a FULL USER-FRIENDLINESS AUDIT of every page, then the fixes (English).

Owner vision: ATP must be 100% user-friendly. A beginner trader should enjoy working with it. Most pages today are crowded with things the user doesn't care about. A user logs in to work: show what he needs, make the important things stand out at first sight, and hide or remove everything else. A confused user leaves.

This covers EVERY user-facing page. Already ordered (Master Orders 1 and 2: Broker, Dashboard front page, sign-in, Option chain, AI keys, page tours) are audited too, against the same standard, and their fixes stay as ordered. Same rules: under ui_v2, self-review, tests, PAPER only, LIVE untouched, English only, no secrets; before / after screenshots (desktop + phone, light + dark).

1. THE STANDARD (every page is checked against all of it)
U1 Purpose in 5 seconds: a new user can tell what the page is for and where to start without reading anything long.
U2 One primary action per page, visually the strongest element (e.g. "Connect", "Start paper bot", "Analyze"). Secondary actions are smaller; rare actions go under a "More" / "Advanced" menu.
U3 The important things stand out: the key numbers and states (P&L, open positions, today's signal, broker status, market open / closed) are large, bold and at the top. Colour carries meaning only: green / red for up / down or good / bad, amber for "needs attention". Use status chips and icons, not paragraphs.
U4 Nothing the user doesn't need:
 - no developer text;
 - no internal ids, row counts, heartbeat seconds, cache times or raw JSON;
 - no technical terms (OAuth, redirect, request token, TOTP secret, lookback, sample) outside "Advanced";
 - no sample / demo data;
 - no manual inputs the system can fill itself.
U5 Plain words: short labels and one-sentence help; numbers formatted for traders (₹, %, lakhs / crores where natural; strikes as traders write them).
U6 Progressive disclosure: basic view first; details on tap / expand; "Advanced" collapsed by default.
U7 Every state is designed: loading (skeletons, not blank), empty (a one-line hint + the next action), error (what happened + what to do, one sentence), no broker (the BrokerGate card), market closed (said plainly).
U8 Consistency: the same components, spacing, chart, table and card styles on every page (one design system); the same words for the same thing everywhere.
U9 Phone first: no sideways-scrolling tables (cards / lists instead), thumb-size targets, the primary action reachable at the bottom, bottom sheets for pickers.
U10 Navigation: the user always knows where he is (page title, active nav item) and how to go back. The sidebar has only the pages users need, in task order (Broker first). Rarely used or admin pages are not in the main nav.
U11 Speed: the first useful content is visible fast; long calls show progress; nothing freezes the page.
U12 Accessibility: readable contrast in light and dark, font sizes readable on phone, keyboard reachable, labels on icons.

2. THE AUDIT (do this FIRST; report before changing pages)
- List every route / page in the app (including the settings sub-pages, the admin pages, the modals and the old non-v2 pages still reachable).
- For each page:
 - a screenshot (desktop + phone);
 - its purpose in one line;
 - a score ✓ / ✗ for U1–U12;
 - the list of elements to REMOVE, HIDE under Advanced, MOVE (to which page), MERGE, or MAKE PROMINENT;
 - the proposed primary action;
 - a mock (wireframe or annotated screenshot) of the cleaned page.
- Pages: also say which pages to merge or drop because they overlap or no user needs them, and the proposed new sidebar (task order).
- Propose a "Simple / Pro" view switch: Simple (the default for new users) shows only the core pages and the basic view of each page; Pro shows everything. List what each mode shows.
- A fresh-eyes test: give an agent with NO knowledge of the codebase the app on a fresh account and these tasks:
 - sign in;
 - connect the broker;
 - open a NIFTY chart on 15m and the daily;
 - read the option chain for the nearest expiry;
 - start a paper bot;
 - set up Telegram alerts;
 - analyze an uploaded chart;
 - find today's P&L.
 Record for each task: done or not, the steps and the time, and where the agent got confused. These findings go into the fix list.
- Deliver the audit as one report (docs/design/UX_AUDIT_FULL.md + the screenshots), with a plain Marathi summary for Abhi: the 10 biggest problems and the order of the fixes.

3. THE FIXES (after the audit report; don't wait for Abhi's reply, he reviews in parallel)
- Fix the pages in order of user impact: the pages a new user meets first (sign-in, Broker, Dashboard, Charts, Option chain, Signals, Positions, Paper bot) before the rest.
- One PR per page or per small group, each with before / after screenshots and the U1–U12 checklist ticked.
- Remove the dead code paths that only served the removed elements (keep the test fixtures).
- Add a CI guard: a list of banned UI words (developer terms, internal labels) that fails the build if they appear outside "Advanced".
- Re-run the fresh-eyes test at the end. Every task must succeed without confusion, and the results go in the final report.

4. DONE MEANS
- Every page passes U1–U12 (the checklist is in the PR).
- The fresh-eyes test passes all tasks.
- No developer text anywhere outside "Advanced".
- Simple mode is the default for new users.
- After each merge, give Abhi the one deploy line (say whether a migration is needed; if yes, after 15:30).
