# HANDOVER_NOW (15 Nov)

Prod: edd6592d deployed (Terminal strip/chain/watchlist, chart draw + Buy/Sell, Route 3, B1 owner-only LIVE). live_trading reported ON.

Merge when CI green (squash, expectedHeadSha), then ONE deploy (migration YES):
- #1092 runner S1-S8 (migrations c1e3a5b7d9f2, d2f4b6c8e0a1)
- #1093 integration 15 Nov (#1088-#1091)
- #1094 Copilot reading on chart; #1095 Copilot 1H for stocks + health text

Paused by owner: runner S9 (annual Pro, VPS in customer's name, plan expiry).
Owner flags to decide (all OFF): broker_app_links, broker_book, user_runner.
Disk tight: worktrees in /dev/shm.
