# Handover now (25 Oct 2026, noon IST)
Deploy given: eb11fb0a (CI + staging green; migrations b3f5d7e9a1c2, c7e9a1b3d5f2). Merged since: #539 (cf87ccb).
Owner to send: deploy log, releases.log, health, Charts + Terminal screenshots (desktop + phone).
Open: #532 + this PR (docs; merge on green); #536 DM1-DM5 (green, waits owner ✅); #540 TF-1 ✅ with 2 fixes,
then merge: (1) picker button = current interval if not starred, else timeframe icon + "▾", never "More";
(2) phone list = bottom sheet, scrolls inside, above the bottom nav. Then one deploy block (DEP-1).
Rule now: no new jobs until the limit resets; finish, merge, deploy only.
Known: staging rollback --restore-db step fails intermittently (not tied to a PR) - root-cause next.
Traders first (DECISIONS 25 Oct PR-1..4): UI polish frozen after TF-1. Next: data (DATA-H backfill, data_hub,
universe sync) -> day-one test J1/J3 -> T2/T3 + CX PR 3 + SX -> FN2-FN4 -> FN5 + CP-V -> alerts. FN0_JOURNEYS.md.
