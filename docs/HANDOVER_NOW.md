# HANDOVER_NOW (19 Nov)

Main f2c263fb (merged 19 Nov: #1114-#1122); deploy pending, migration no; flag `strategy_chart` OFF until the owner turns it on.

Open PRs (draft, CI): #1123 SFC P3 (why), #1124 P4 (stats, stacked), #1126 P5 (builder, stacked), #1125 cash-share
product NRML->CNC + OPEN_QUESTIONS UPX-TB-Q1 (Upstox TradeButton not in current docs: not built).

Next, in order:
1. Merge #1123 -> #1124 -> #1126 when green (each after the one below it).
2. SFC P6: audit first (order path): bot form lists only v1 (/api/custom-strategies skips v2 since P5) - add v2 to
   the deploy picker; worker/runner parity = chart on recorded bars; LIVE only by the owner's switch.
3. Then the 18 Nov list: CA-CHAT (BT-DRAW 2c merged #1110; follow-up claude/bt-draw-2c), SRV2 P3 (atp-private CI blocker SRV2-Q2).
