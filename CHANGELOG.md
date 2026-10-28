# Changelog

One section per release, newest first. A release is a semver tag `vX.Y.Z` on `main` (AUD-V7, owner audit 28 Oct 2026);
its one plain line also goes into the in-app "What's new" (frontend/src/settings/whatsNew.ts).

## v1.0.0 - 28 Oct 2026

First tagged release: main at the tag (DEF-0 first deployed from `ece32fd8`).

- DEF-0: no platform defaults in order placement and risk - every limit, gate and exit is the trader's own setting
  (blank = off); My limits card; the ticket says which of your limits held an order back; broker margin estimate.
- SCR-FIX A-E: screener data path and intervals.
- AUD-V1: the Autopilot decides on closed bars only.
- AUD-V2: open P&L after hours from the last stored mark ("last mark HH:MM").
- AUD-V3: India VIX bands from the one shared table.
- AUD-V4: the discipline score says "n/a until a trade closes".
