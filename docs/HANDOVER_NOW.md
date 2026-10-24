# Handover now (24 Oct 2026, night)
Merged today: #515 DATA-H (migration d4a6c8e0b2f1), #516 probe tenant-key fix + CI Postgres 17, docs #514 #517-#523.
Owner deploys commit 4b8e46964f4e once main CI + staging pass (one command; migration runs inside), then
probe-chain / probe-candles / lake-backfill --check (output screenshot), then lake-backfill.
In review: #493 T1 + fixes T1-1..T1-7 (needs #510 merged first; owner ✅ on T1_fixes page), #524 CX PR 1
(order path + F1 PAPER hedge hold: owner OK before merge; migration b3f5d7e9a1c2), #510 Wave 0 (owner ✅).
Next: CX PR 2 (routes) after #524; CXQ-1/5/6 answers; WL-1 phone Watchlist entry; TP-1 only after Phase 1.
