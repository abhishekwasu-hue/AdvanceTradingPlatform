# HANDOVER_NOW (26 Nov) - SRV2 bot chain

Done (merged / in PR): Vision chart candles (7-day 1m windows), the bot's ✅/❌ presses and typed replies routed from ATP's webhook
to the bot (atp-private inbox, #32/#33), Vision service heartbeat + TEST card (private #27-#31), Terminal "My trading agent" line,
OI-reversal exit no longer blocks exits (private #33), /status /positions /paper (private #29).

Next session:
1. Deploy + TEST card after 15:35 IST; check the card's chart and ✅.
2. #1175 Analytics Token for the bot (owner: later).
3. Owner decision pending - one Telegram system for all bots: default B (each bot's own card + one router by callback
   prefix, one status line / TEST card / heartbeat per bot); /status: ATP answers and adds the SRV2 line.
4. #1170 AMW Breakout: merge on green (owner said Merge); keep AMW_BREAKOUT_SCAN_ENABLED=0.
