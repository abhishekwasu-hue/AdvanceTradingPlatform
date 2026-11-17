# HANDOVER_NOW (15 Nov)

Prod: edd6592d. live_trading ON (B1: owner org only). .env set: SERVER_EGRESS_IP, OPS_THROTTLE, MARKET_PROTECTION (apply at next deploy).

Merge when CI green, then ONE deploy (migration YES):
#1094, #1095, #1097 watch-screen, #1098 owner method owner-only (#1092, #1093 merged; main 5b837e7d, deploy pending, migration yes).

Later (owner, 15 Nov):
- Fyers account Off: add On/Off toggle on v2 MultiAccountCard; ask owner Fyers use (own vs secondary copy; multi_account LIVE = his words).
- Watch-screen first check still ~872 Upstox calls: store F&O 1m/30m nightly.
- Owner: register 187.126.125.208 at Upstox + ATP Static IP card, then STATIC_IP_REQUIRED_FOR_LIVE=true.
- Paused: runner S9. Flags OFF: broker_app_links, broker_book. user_runner ON (owner 15 Nov); runner image not released (no runner-v* tag) - push tag runner-v0.1.0 on owner ok.
- NEW owner order 15 Nov (M79 scope): port Trade bot "15M SRv2" (strategy_key 15m_dynamic_sr; Trade one_bot.py, dynamic_sr_instant_trader.py, bot_view.py) AS IS with its dashboard settings into ATP (code in atp-private trade_engine, ATP boundary only); settings under a button; modes PAPER + LIVE; on activation its signal charts go to Vision -> Telegram with buttons (human_confirm) -> paper/live entry, exactly as Trade does. No logic change. Audit first (~2-3 lakh tokens), then estimate, wait for owner ok.
- Analyze-a-chart redesign plan (awaits owner ok, ~11-12 lakh): one input, one "Analysis style" dropdown (PA, VSA, Wyckoff, S&D, Fib/EW, indicators, books), one Read button, one result card with overlay, cost in Rs, voice intent in Copilot chat.
