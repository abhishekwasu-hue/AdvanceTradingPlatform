# Video outline - "Automatic trading bots in ATP: create, deploy, how it works"

Length: about 10-12 minutes. Screen recording of the real ATP screens, PAPER only, synthetic symbol "DEMO".
On-screen line for the whole video: "Information and education only, not investment advice. Practice (PAPER) first."

| # | Time | Scene (screen) | What to say (short) |
|---|------|----------------|---------------------|
| 1 | 0:00 | Title card | A bot = your strategy, switched on for one symbol, checked by ATP on its own. It gives signals; your settings decide the trade. |
| 2 | 0:40 | Figure 1 (pieces) | Strategy → backtest → bot → worker → PAPER fill. LIVE is locked: Phase 0 is PAPER only; LIVE is the owner's switch. |
| 3 | 1:30 | Sign-in screen | Step 1: Create account / Sign in; Two-factor check if on. |
| 4 | 2:00 | Settings › Broker accounts | Step 2: Connect your broker → "Connected ✓". Even a paper bot reads prices through the broker. Upstox: Analytics Token = data; Upstox login = orders, once each trading day. |
| 5 | 3:00 | Settings › Trade settings | Step 3: "ATP sets no trading value for you." Save Starting capital, Holding, Order type, Position size, New entries. |
| 6 | 4:00 | Settings › Risk limits | Every check is off by default; turn on only what you want; Save risk limits. Exits are never checked. |
| 7 | 4:45 | Strategies › Library / Build | Step 4: ways to get a strategy - Library, Templates, Build (5 steps), Copilot draft, strategy interview. Pine Script makes a screen, not a bot. |
| 8 | 6:00 | Build › Result / Test | Step 5: test on past prices; read Trades, Win rate, Net P&L, Max drawdown, Average R. "Past results do not promise future ones." |
| 9 | 7:00 | AI Trading Bot › Start a trading bot | Step 6: Pick a strategy → Pick a symbol → fill any box the form asks for → Start on paper. Show one "needs" line and fix it. |
| 10 | 8:00 | Bot card + Signals | Step 7: Running, Checked just now, No signal yet, "Why no trade?". Ask me first → Signals → Approve paper trade / Skip. Inbox and Telegram. |
| 11 | 9:00 | Figure 3 (cycle timeline) | One cycle: closed candle → market open → login check → exits first → closed candles → strategy → checks → the one order path → card. |
| 12 | 10:00 | Bot card / My trades | Step 8: Pause / Resume, Stop ("an open paper trade is still watched"), Exit now, Exit all. Stop does not close a trade. |
| 13 | 10:45 | Safety boxes | AI never places orders (you press, or your saved rules do). Exits are never blocked. Your own limits. Nothing is pre-chosen. |
| 14 | 11:30 | Closing card | Practise on PAPER first. No advice, no performance claims. |

Rules for the recording: no real stock names with prices; no buy / sell wording; no "should" about trades; no performance claims;
no ids or flag names on screen; any number shown is made up and labelled so.

Before recording, confirm the open points listed in the guide's administrator appendix (Python lab, user kill switch button,
where Library stop / targets are edited, Telegram availability).
