# HANDOVER_NOW (15 Nov)

Prod: edd6592d. live_trading ON (B1: owner org only). .env set: SERVER_EGRESS_IP, OPS_THROTTLE, MARKET_PROTECTION (apply at next deploy).

Merge when CI green, then ONE deploy (migration YES):
#1092 runner, #1093 (#1088-#1091), #1094, #1095, #1097 watch-screen.

Later (owner, 15 Nov):
- Fyers account Off: add On/Off toggle on v2 MultiAccountCard; ask owner Fyers use (own vs secondary copy; multi_account LIVE = his words).
- Watch-screen first check still ~872 Upstox calls: store F&O 1m/30m nightly.
- Owner: register 187.126.125.208 at Upstox + ATP Static IP card, then STATIC_IP_REQUIRED_FOR_LIVE=true.
- Paused: runner S9. Flags OFF: broker_app_links, broker_book, user_runner.
