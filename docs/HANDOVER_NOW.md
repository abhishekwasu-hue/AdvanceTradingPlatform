# HANDOVER_NOW (15 Nov)

Prod: 5b837e7d; next deploy 45d62adf (no migration). live_trading ON (B1: owner org only). .env set: SERVER_EGRESS_IP, OPS_THROTTLE, MARKET_PROTECTION (apply at next deploy).

Merge when CI green, then ONE deploy (migration YES):
#1094, #1095, #1097 watch-screen, #1098 owner method owner-only (#1092, #1093 merged; main 5b837e7d, deploy pending, migration yes).

Later (owner, 15 Nov):
- Fyers account Off: add On/Off toggle on v2 MultiAccountCard; ask owner Fyers use (own vs secondary copy; multi_account LIVE = his words).
- Watch-screen first check still ~872 Upstox calls: store F&O 1m/30m nightly.
- Owner: register <server-ip> at Upstox + ATP Static IP card, then STATIC_IP_REQUIRED_FOR_LIVE=true.
- Paused: runner S9. Flags OFF: broker_app_links, broker_book. user_runner ON (owner 15 Nov); release: owner publishes runner-v0.1.0 on GitHub Releases (PR #1100 merged).
- NEW owner order 15 Nov (M79 scope): port Trade bot "15M SRv2" (strategy_key 15m_dynamic_sr = srv2_momentum_reversal_strategy.py + engine resident; audit: atp-private docs/SRV2_PORT_AUDIT.md) AS IS with its dashboard settings into ATP (code in atp-private trade_engine, ATP boundary only); settings under a button; modes PAPER + LIVE; on activation its signal charts go to Vision -> Telegram with buttons (human_confirm) -> paper/live entry, exactly as Trade does. No logic change. APPROVED 18 Nov. Built: atp-private #19 glue, #20 Trade PAPER engine + runner (tick: engine 5M close, bot :30, exits 15/5 s); ATP #1103 srv2bot (IO, worker loop, flag srv2_bot owner-only OFF). Next: P2 settings page + Trade settings copy cmd, P3 Vision/Telegram (copy vision/*), P5 parity. Blocker: atp-private CI (SRV2-Q2).
- Analyze-a-chart redesign plan (awaits owner ok, ~11-12 lakh): one input, one "Analysis style" dropdown (PA, VSA, Wyckoff, S&D, Fib/EW, indicators, books), one Read button, one result card with overlay, cost in Rs, voice intent in Copilot chat.
