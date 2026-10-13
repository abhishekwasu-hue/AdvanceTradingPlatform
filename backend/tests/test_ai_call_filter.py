"""H-C1 c, Phase 0 gap fix: trade calls aimed at an instrument are blocked - and descriptive analysis still passes. A
corpus of both sides, in English: ATP answers in English only, and AI text with Devanagari is withheld outright."""
import re

import pytest

from app.ai import output_filter

SHOULD_BLOCK = [
    "Close/exit INFY.",
    # imperative verbs aimed at an instrument
    "Sell RELIANCE", "Go long RELIANCE", "Go short NIFTY here.", "Accumulate RELIANCE", "Short sell TCS.", "Book profits in TCS.",
    "Price is weak. Exit INFY now.", "Please buy HDFCBANK above 1650.", "Average down INFY.", "Load up on SBIN.",
    "RELIANCE: buy the dip", "TATAMOTORS - sell", "Buy this stock now", "Buy the shares before results",
    # entry / stop / target sets
    "Entry RELIANCE 2500 SL 2450 TGT 2600", "Buy above 2500, SL 2450, target 2600.", "NIFTY: stop 24800, target 25200",
    "Entry 2500 stop-loss 2450 targets 2600",
    # price-target certainty
    "RELIANCE to 3000 for sure", "NIFTY will hit 26000 definitely",
    # HIGH2: markdown, lists, emoji, quotes, brackets, labels and arrows
    "- Buy RELIANCE", "* Buy RELIANCE", "• Buy RELIANCE", "- Buy: RELIANCE", "**Buy** RELIANCE", "`Buy RELIANCE`", "> Buy RELIANCE",
    "🚀 Buy RELIANCE", "(Buy RELIANCE)", "\"Buy RELIANCE\"", "Recommendation - Buy RELIANCE", "BUY: RELIANCE", "Buy -> RELIANCE",
    "1) Buy RELIANCE", "Buy → RELIANCE", "ｂｕｙ RELIANCE", "B\u200buy RELIANCE", "Вuy RELIANCE", "B u y RELIANCE", "8uy RELIANCE now",
    # HIGH3: entry / stop / target across lines and sentences
    "Entry 2500\nSL 2450\nTarget 2600", "Entry 2500. SL 2450. Target 2600.", "Long above 2500, stop 2450, target 2600",
    # HIGH4: instruments by name, in any case and in Devanagari
    "buy reliance", "Buy Reliance Industries", "Sell infosys now.",
    # MED7: more calls
    "Price is weak, sell INFY.", "Long RELIANCE", "Purchase RELIANCE", "Square off all INFY positions", "Exit all INFY positions",
    "Buy a few shares of RELIANCE", "Then buy RELIANCE", "Just buy RELIANCE", "Time to buy RELIANCE!", "Consider buying RELIANCE.",
    "I would buy RELIANCE here", "I'd buy RELIANCE", "I’d buy RELIANCE", "One could buy RELIANCE here", "My pick is RELIANCE",
    "RELIANCE is a buy", "RELIANCE looks like a buy", "NIFTY will surely reach 26000", "It will certainly reach 3000",
    "three thousand for sure",
    # LOW9: an imperative with a level and no ticker, and a modal call on a ticker with a level
    "Buy 25000 CE above 120", "I would sell TCS above 4000",
    # second review of #230: exemptions are scoped to the clause / a record line only
    "Step 1: Buy RELIANCE.", "Example: Buy RELIANCE now", "Backtest: Buy RELIANCE", "Status: Buy RELIANCE (now)", "Template: Buy RELIANCE",
    "Journal: Buy RELIANCE now", "Proposal: Buy RELIANCE now", "Buy RELIANCE when the market opens", "Buy RELIANCE when you can",
    "We never say this, but buy RELIANCE.", "I won't tell you what to do; sell INFY.", "Buy RELIANCE in paper mode.",
    "Buy RELIANCE now - click the button.", "RELIANCE was weak.\nEntry 2500\nSL 2450\nTarget 2600",
    "RELIANCE will hit 3000 for sure, as it did last week.", "Buy calls on NIFTY", "Buy puts on NIFTY", "Buy signal on RELIANCE - act now",
    "Buy setup RELIANCE", "Sell rating on TCS", "Buy\nRELIANCE", "Buy two lots of NIFTY",
    "Buy ten shares of RELIANCE", "You bought RELIANCE, buy more.", "Buy => RELIANCE",
    # third review of #230: records only on record labels and their first clause; rules need a market condition;
    # negation scope; more objects and verbs
    "Summary: momentum was strong, buy RELIANCE.", "Analysis: RELIANCE was strong; buy RELIANCE above 2500.",
    "View: trend was up - buy RELIANCE, SL 2450, target 2600.", "Takeaway: buyers were active, so buy RELIANCE.",
    "Bottom line: sell TCS, it was weak.", "Status: order placed; buy RELIANCE now.", "Order filled: sell TCS now too.",
    "PAPER order placed: buy RELIANCE again tomorrow.", "Note: 10 shares pending - buy RELIANCE.",
    "Journal: you bought RELIANCE; buy more RELIANCE.", "Status: was flat - now sell INFY above 1500, SL 1480, target 1550.",
    "Entry 2500, SL 2450, target 2600 - it was a good level.", "RELIANCE was weak earlier, but entry 2500, SL 2450, target 2600 now.",
    "Per your template: entry 2500, SL 2450, target 2600 on RELIANCE - take it.",
    "In the backtest it worked: entry 2500, SL 2450, target 2600 on RELIANCE today.",
    "Rule: Buy RELIANCE when the market opens.", "Rule: Buy RELIANCE today when it opens.", "Strategy: buy RELIANCE when you see this message.",
    "Template: sell TCS when you can.", "We never say this - but buy RELIANCE.", "We never recommend stocks: buy RELIANCE.",
    "Never say never: sell TCS.", "We never say buy RELIANCE, we say BUY RELIANCE NOW.",
    "Invest in RELIANCE (now)", "Put money in RELIANCE", "that said buy RELIANCE", "Buying RELIANCE here makes sense",
    "RELIANCE: Buy rating",
    "RELIANCE will hit 3000 for sure as it did last week", "Hold RELIANCE", "Add RELIANCE", "Grab RELIANCE", "Dump TCS", "Buy it",
    # review round 4: an earlier verb-shaped word must not hide the real call
    "Long story short, buy RELIANCE.", "Short answer: buy RELIANCE.", "Hold on, sell TCS now.", "Add to that, sell TCS now.",
    "Exit, then buy RELIANCE.", "Exit rules are listed, sell TCS now.", "Exit: SMA cross down, sell TCS now.",
    "Short interest rose - buy RELIANCE now.", "Buy when ready, sell TCS.", "Status: BUY 10 RELIANCE filled - sell TCS now.",
    "Status: BUY 10 RELIANCE filled - buy more RELIANCE.", "Order: SELL 5 TCS at 4000 filled; next buy INFY.",
    "Rule: Buy RELIANCE when RSI < 30 - and buy TCS too.",
    # review round 5: how-to exemptions never cover a trade verb or "now"; " and " / " then " open a clause
    "Buy RELIANCE on the daily chart.", "Buy RELIANCE on the daily chart now.", "Buy RELIANCE on the weekly chart breakout.",
    "Sell TCS on this chart.", "Buy RELIANCE chart setup.", "Buy the RELIANCE chart setup.", "Buy RELIANCE's chart breakout.",
    "Buy RELIANCE from the order page.", "Sell TCS from the positions tab right away.", "Buy RELIANCE from your watchlist page now.",
    "Buy RELIANCE in this view.", "Buy RELIANCE for the long view.", "Buy RELIANCE from the market screen.", "Buy the RELIANCE page dip.",
    "Open the chart and buy RELIANCE.", "Check the levels and sell TCS now.", "Read this and buy RELIANCE.",
    "Buy the 20 EMA pullback in RELIANCE now.", "Buy a 20 EMA bounce on RELIANCE.", "Buy the 50 DMA retest in TCS.",
    "Sell the 200 SMA break in INFY.", "Add RELIANCE to the basket now", "Buy RELIANCE to the basket", "Buy TCS to your watchlist",
    "Sell INFY to the comparison panel",
    # review round 6: exit / close / hold are trades even when a tab or view is named; close / open a position are trades
    "Exit INFY from the positions tab.", "Exit INFY from the positions page.", "Exit INFY from the holdings page.",
    "Exit INFY from the positions tab before the close.", "Exit INFY from the positions tab before results.",
    "Close INFY from the positions tab.", "Close your INFY position from the positions tab.", "Close INFY on the orders page.",
    "Hold RELIANCE for the long view.", "Hold TCS in your holdings view.", "Hold TCS in the portfolio view.",
    "Exit INFY from the comparison view.", "Hold RELIANCE for the next session's analysis view.",
    "Close the RELIANCE position.", "Close RELIANCE.", "Close INFY.", "Close out INFY now.", "Close all INFY positions.",
    "Open a position in RELIANCE.", "Open a long in RELIANCE.", "Open RELIANCE long.", "Open a short position in TCS.",
    "Open short NIFTY.", "Open the positions tab and close INFY.", "Exit the RELIANCE chart before the close.",
    "Buy and hold RELIANCE now.", "You should buy and hold RELIANCE.", "Buy and sell RELIANCE intraday.", "Sell and short TCS.",
    "Go long and buy RELIANCE.", "Close RELIANCE at 2450.",
    "TCS: close", "INFY: close now.", "RELIANCE: hold", "Close RELIANCE above 2500.", "Close TCS at market.",
    "Hold RELIANCE into results.",
    # review round 8: a quantity, "at market" or an order word keeps close / open / hold an order
    "TCS: close at market", "RELIANCE: open at market", "INFY: close 50% now", "INFY: close 100 shares", "TCS: hold 100 shares",
    "NIFTY: open at 25000 with a long", "INFY: hold above 1500 or exit", "RELIANCE - hold above 2500 for 2600",
    "Close of the RELIANCE position now.", "Close 50% of INFY.", "Close 2 lots of NIFTY.", "Open 2 lots of NIFTY long.",
    "Close half of RELIANCE.", "Close at 2450 RELIANCE.", "Sell 50% of INFY.", "Sell half of RELIANCE.",
    # review round 9: urgency / time words, a target, more quantity units, an advice predicate, the instrument after the level
    "INFY: close at 1500 immediately", "INFY: close at 1500 asap", "INFY: close at 1500 right away", "INFY: close at 1500 please",
    "INFY: close at 1500 tomorrow", "INFY: close at 1500 by Friday", "INFY: close at 1500, then re-enter at 1450",
    "TCS: hold above 4000 till 4500", "TCS: hold above 4000 to 4500", "TCS: hold above 4000 towards 4500",
    "TCS: hold above 4000 till results", "TCS: close 2 contracts", "TCS: close 50 pc", "TCS: close 50 per cent",
    "TCS: close 75 quantity", "TCS: close 100 nos", "INFY: close 100 stocks", "INFY: close 1/2", "TCS: hold is the call",
    "INFY: close is wise", "INFY: close and move on", "INFY: close below 1500 and walk away", "INFY: close below 1500 and stay out",
    "INFY: close at 1500 is wise", "Close at 2450 on RELIANCE.", "Close above 2450 in INFY.", "Hold above 2500 with RELIANCE.",
    "Close at 2450 all RELIANCE.", "Hold above 2500 your RELIANCE shares.", "Hold above 2500 - RELIANCE.", "Close half INFY.",
    "Close 50pc of INFY.",
    # review round 10: only a recap tail keeps a price field a recap (everything else stays an order)
    "INFY: close at 1500 pronto", "INFY: close at 1500 at once", "INFY: close at 1500 without delay", "INFY: close at 1500 urgently",
    "INFY: close at 1500, quickly", "INFY: close at 1500 - do it", "INFY: close at 1500 before results",
    "INFY: close at 1500 ahead of results", "INFY: close at 1500 on Monday", "INFY: close at 1500 this week",
    "INFY: close at 1500 by end of day", "INFY: close at 1500 before the close", "INFY: close at 1500 into strength",
    "INFY: close at 1500 on strength", "INFY: close at 1500 is advisable", "INFY: close at 1500 is recommended",
    "INFY: close at 1500 is prudent", "INFY: close at 1500 is ideal", "INFY: close at 1500 makes sense",
    "INFY: close at 1500 is a good idea", "INFY: close at 1500 is the way", "INFY: close at 1500 if you own it",
    "INFY: close at 1500 if you hold it", "INFY: close at 1500 if it breaks", "TCS: hold above 4000 if you own it",
    "TCS: hold above 4000 for gains", "TCS: hold above 4000 for more upside", "TCS: hold above 4000 and ride it",
    "TCS: hold above 4000, don't sell", "TCS: hold at 4000 for the next leg", "TCS: hold above 4000; it can go to 4500",
    "INFY: close at 1500 and lock gains", "INFY: close at 1500 and take profits", "Close at 2450 against RELIANCE.",
    "Close at 2450 via RELIANCE.", "Close 1/2 of INFY.", "INFY: close 1 lakh qty", "TCS: open at 4000 with a small lot",
    "TCS: hold above 4000 up to 4500", "Close at 2450, RELIANCE.",
    # review round 12 (HIGH-1): every tail is read for advice; a subordinate clause must report the past; "hold above / at N"
    # is an order unless a predicate follows; dash asides, "per ...", gains and copula complements are closed forms
    "INFY: close at 1500 after results", "TCS: hold above 4000 after results", "INFY: close at 1500 following the results",
    "INFY: close at 1500 post results, re-buy lower", "INFY: close at 1500 as a precaution", "INFY: close at 1500 as a hedge",
    "INFY: close at 1500 as it is weak", "TCS: hold above 4000 as long as it holds", "INFY: close at 1500 while you can",
    "TCS: hold above 4000 despite the noise", "INFY: close at 1500 amid the selloff to protect capital",
    "INFY: close at 1500 since it is weak", "TCS: hold above 4000 since it is strong", "TCS: hold above 4000 since the trend is up",
    "INFY: close at 1500 - a prudent step", "INFY: close at 1500 - the safest choice", "INFY: close at 1500 – the obvious choice",
    "TCS: hold above 4000 — a no-brainer", "INFY: close at 1500 as per your plan", "INFY: close at 1500 per the strategy",
    "INFY: close at 1500 according to the plan", "INFY: close at 1500 in line with your risk plan",
    "TCS: hold above 4000 for a 10% gain", "TCS: hold above 4000 for a rise to 4500", "INFY: close at 1500 for a quick gain",
    "INFY: close at 1500 is a smart move", "INFY: close at 1500 is a wise move", "INFY: close at 1500 is a good move",
    "INFY: close at 1500 is a sound move", "INFY: close at 1500 is a strong move", "INFY: close at 1500 is the safest",
    "INFY: close at 1500 is the smartest", "INFY: close at 1500 is the cleanest", "INFY: close at 1500 from 1520 entry",
    "TCS: hold above 4000 again", "TCS: hold above 4000 should signal a buy", "TCS: hold above 4000 with RSI above 60 as confirmation",
    "INFY: close at 1500 by 2%", "TCS: hold above 4000", "TCS: hold at 4000", "TCS: hold 4000", "TCS: hold above 4000 for the week",
    "INFY: close at 1500, as it is weak", "INFY: close at 1500, since it is weak", "INFY: close at 1500, a prudent step",
    "INFY: close at 1500, the obvious choice", "INFY: close at 1500, if it breaks", "INFY: close at 1500, to be safe",
    "INFY: close 1500, a no brainer", "INFY: close at 1500 is fine", "INFY: close 1500 is fine to do", "INFY: close 1500 as a safety measure",
    "INFY: close 1500 after results come out", "INFY: close at 1500 after Monday", "INFY: close at 1500 amid the selloff",
    "INFY: close at 1500 because it rallied", "TCS: close 4050; target 4500", "TCS: close 4050, analysts target 4500",
    "Close at 1500 in INFY as a precaution.", "Hold above 2500 in RELIANCE after results.", "Close above 1500 in INFY, the obvious choice.",
    "INFY: close below the 20-DMA and walk away", "INFY: close below the 20-DMA before results", "TCS: close above the 20-DMA for upside",
    "TCS: close above 4000, a buying opportunity", "TCS: hold above 4000 kept the trend, stay long", "TCS: close 4050 is a good sign to add",
    "INFY: close 1500 vs holding", "INFY: close at 1500 for a 2% gain",
    # follow-ups of round 12: verdict asides, a trade verb on a level, "pick it up"
    "INFY: close at 1500 considering the risk", "INFY: close at 1500 in view of results", "INFY: close at 1500 on weakness",
    "INFY: close at 1500 into the rally", "INFY: close at 1500 on a bounce", "INFY: close at 1500 on any bounce",
    "INFY: close at 1500 with a trailing stop", "INFY: close at 1500 with discipline",
    "INFY: close at 1500, no questions asked", "INFY: close at 1500, no hesitation", "INFY: close at 1500, full stop",
    "INFY: close at 1500 at the earliest", "INFY: close at 1500 on priority", "INFY: close at 1500 first thing",
    "INFY: close at 1500 when it opens", "INFY: close at 1500 once it trades there", "INFY: close at 1500 whenever possible",
    "TCS: hold above 4000 through expiry", "TCS: hold above 4000 into results", "TCS: hold above 4000 for the long term",
    "TCS: hold above 4000 for the medium term", "TCS: hold above 4000 with patience", "TCS: hold above 4000 is my advice",
    "TCS: hold above 4000 is my view", "TCS: hold above 4000 is what I'd do", "INFY: close at 1500 is what I'd do",
    "INFY: close at 1500 is the prudent thing", "INFY: close at 1500 - that's the trade",
    "INFY: close at 1500; that is the plan", "INFY: close at 1500. That is the plan.",
    "INFY: close at 1500 and stop worrying", "INFY: close at 1500 and forget it", "INFY: close at 1500 and wait for 1450",
    "INFY: close at 1500 and look for 1450", "INFY: close at 1500, wait for 1450", "INFY: close at 1500, re-buy near 1450",
    "INFY: close at 1500, rebuy near 1450", "INFY: close at 1500, pick it up near 1450",
    "INFY: close at 1500, scoop it up at 1450", "INFY: close at 1500, load up at 1450",
    "INFY: close at 1500, short below 1490", "TCS: close at 4050, go long above 4060",
    "INFY: close at 1500 on strength, for the record", "INFY: close at 1500 with a profit",
    "INFY: close at 1500 at a profit", "INFY: close at 1500 at a loss if needed",
    "INFY: close at 1500; prudent to exit",
    "INFY: close at 1500; it is the wise thing", "INFY: close at 1500; safest option", "INFY: close at 1500; a must",
    "INFY: close at 1500; prudent", "INFY: close at 1500; recommended.", "INFY: close at 1500, period.",
    "INFY: close at 1500. It is the right call.", "INFY: close at 1500. That's my advice.", "TCS: weak, sell below 4000",
    "TCS looks weak; short under 4000.", "INFY: close at 1500, just do it", "INFY: close at 1500, or else",
    "INFY: close at 1500, no doubt", "INFY: close at 1500 without hesitation",
    "INFY: close at 1500, so the risk is capped", "INFY: close at 1500 to end the week flat",
    "INFY: close 1500 and cut losses", "INFY: close at 1500, right call", "INFY: close 1500 to gain 2%; then re-buy",
    "INFY: close at 1500, better than holding",
    # review of #254 (F1): a comparison after an order, a verdict in the next sentence, a "so the risk ..." rationale
    "INFY: close at 1500, better than a bigger loss.", "INFY: close at 1500, better than the alternative.",
    "INFY: close at 1500, better than a stop-out.", "INFY: close at 1500, safer than the alternative.",
    "INFY: close at 1500, smarter than the alternative.", "TCS: close 4050, safer than peers.", "TCS: close 4050, wiser than the index.",
    "TCS: close 4050 before results. That is the plan.", "TCS: close 4050 to end the week up 2%. That is the call.",
    "TCS: close 4050. Full stop.", "TCS: close 4050, so the risk is capped.", "TCS: close 4050, so the loss is limited.",
    "TCS: close 4050, so the profit is locked.", "B.U.Y INFY",
    # #254 merge check: a short order sentence in a paragraph that names an instrument
    "TCS: close 4050 before results. Exit.", "TCS: close 4050 before results. Sell.", "TCS: close 4050 before results. Do it.",
    "TCS: close 4050 before results. Get out.", "TCS: close 4050 before results. Act on it.", "TCS looks weak. Sell.",
    "TCS hit 4050 before results. Exit.", "TCS looks weak. Book it.", "TCS looks weak.\nBuy.",
]

SHOULD_PASS = [
    "Price closed above SMA 20.", "The stop-loss rule in your template exits at 2%.", "Buyers absorbed the selling near 2450.",
    "NIFTY 50 trend is up; support near 24850.", "Bulls hold 25200.00; room to 25550.00, wrong below 24850.00.", "Shorting is risky.",
    "A sell-off below 24850 would weaken the read.", "RSI 14 is above 60.", "Add SMA(close, 20) to the chart.",
    "The template has a stop of 2% and a target of 4%.", "Exit rules: the stop moves to break-even at T1.",
    "Sell signals were rare last month.", "I don't know for sure.", "The index rose to 25000 last week.",
    "This is not a recommendation to buy anything.", "Selling pressure eased after 14:00.", "The buy-side volume doubled.",
    "Entry and exit rules are listed below.", "Your PAPER position in RELIANCE closed at the stop.",
    "RELIANCE closed at 2480, below its 20-day average.", "Exits are never blocked.", "Add RELIANCE to your watchlist.",
    "Enter NIFTY in the symbol box to load its chart.",
    # review of #230, MED6: logs, backtests, percentages, status, rules, help, negations, past certainty
    "Backtest trade 3: entry 2480, stop 2430, target 2580", "Trade log: RELIANCE long, entry 2480, SL 2430",
    "ORB: stop 1%, target 2%", "Proposal: buy RELIANCE, 10 shares, awaiting your approval", "Status: BUY RELIANCE filled",
    "Side: SELL TCS", "Rule: Buy NIFTY when RSI < 30", "How to: Buy RELIANCE in paper mode", "NIFTY: buy signals were rare",
    "We never say: Sell RELIANCE",
    "NIFTY hit 25000 for sure on Friday.", "Buy when the close is above the 20-day high.", "The long-term trend is up.",
    "Short-term momentum faded near 2450.", "Long upper wicks near 2500 show supply.", "Cover image of the report.",
    "Exit all is under the Positions page.", "The trade was closed at the target 2600 after the stop 2450 moved up; entry was 2500.",
    # second review of #230: records, noun phrases, past moves, template levels
    "PAPER order placed: BUY 10 RELIANCE at 2500.", "Drafted: Sell TCS 5 qty (PAPER) - needs your approval.",
    "Your order: Buy RELIANCE x 10 (PAPER).", "Short TCS positions were squeezed.", "Sell HDFCBANK was the most common signal.",
    "Exit INFY positions are shown in the journal.", "NIFTY rose to 25000 surely because the banks rallied.",
    "Buy-side breakout on RELIANCE: entry 2500, stop 2450, target 2600 per the template.",
    # third review of #230
    "Short interest in TCS rose.", "Rule: Buy NIFTY when the close crosses above the 20-day high.",
    "Hold on - the data is still loading.", "Add SMA(close, 20) and RELIANCE to the chart.",
    # review round 4: app how-to wording
    "Add RELIANCE to the backtest.", "Add RELIANCE to your strategy.", "Add RELIANCE to the screener.", "Add RELIANCE to the template.",
    "Add RELIANCE to the comparison panel.", "Add a 20 EMA on the RELIANCE chart.", "Exit the RELIANCE page.",
    "Grab the RELIANCE chart from the Charts tab.",
    # review round 6: the benchmark's name, the candle's close / open, and the app's own open
    "The strategy will buy and hold NIFTY in the backtest.",
    "A buy and hold NIFTY portfolio lagged.", "Close above 2450 would confirm the breakout.", "Close at 2450 would be bullish.",
    "Close below 2400 invalidates the setup.", "RELIANCE close was 2450.", "The close in RELIANCE was weak.",
    "Open: 2440, High: 2460, Low: 2430, Close: 2450", "Open interest in NIFTY rose.", "Open positions in TCS rose.",
    "Open RELIANCE in a new tab.", "Open the RELIANCE chart.", "Close the RELIANCE chart.", "Close the order ticket.",
    "Close the chart and review INFY.", "Open the positions tab.",
    # review round 7: close / open / hold as a bar's price fields, in the "TICKER: verb" form too
    "TCS: close 4050, RSI 62.", "NIFTY: open 25000, close 25100.", "TCS - close 4050.",
    "TCS: close ₹4,050.", "TCS: close 4,050.25.", "TCS: close 4050 for a 1.2% gain.", "NIFTY: close 25100. TCS: close 4050.",
    "TCS: close 4050; INFY: close 1500.", "TCS: close 4050 near its 50-DMA.", "INFY: close 1500 as per NSE.", "RELIANCE: close above SMA 20.",
    "INFY: close below 1500 on volume.", "RELIANCE: close 2480, open 2455.", "HDFCBANK: hold above 1650 kept the trend.",
    "Close price: INFY 1500.", "NIFTY: hold of 25000 matters.",
    # review round 8: recaps with a copula or comparative after the field
    "TCS: close was flat.", "TCS: close unchanged at 4050.", "NIFTY: close higher by 0.4%.", "TCS: open flat, close higher.",
    "HDFCBANK: open gap-up, close near the high.", "TCS: Close/Open ratio 1.01.", "INFY: open and close within 0.2%.",
    "RELIANCE: close at a 52-week high.",
    # review round 9: a later descriptive clause, a duration, a percentage move, more level nouns
    "RELIANCE: close 2480, open interest in long positions rose.", "NIFTY: close 25100 vs target-date 25000 average.",
    "INFY: close at 1500, best close of the month.", "INFY: close near 1500 for 3 sessions.", "TCS: close 50% above its IPO price.",
    "TCS: close at record high.", "TCS: close near the day's low.", "TCS: close in the top quartile.",
    "Close above 2450 confirms the RELIANCE breakout.", "RELIANCE: close above 2500 should confirm the breakout.",
    # review round 10: a recap predicate after the instrument, several fields or instruments, recap clauses
    "Close above 1500 in INFY was the week's best.", "Close at 1500 in INFY marked a 3-week high.",
    "Hold above 1500 in INFY lasted 5 sessions.", "Close above 25000 in NIFTY for the third session.",
    "Open 1490, close 1500 in INFY.", "Close near 1500 on INFY, near 4050 on TCS.", "Close 1500 for INFY and 4050 for TCS.",
    "INFY: close 1500 on TCS results day.", "TCS: close 4050, then 4080 on Tuesday.",
    "NIFTY: close 25100 vs 25000 last week, then 24900 the week before.", "NIFTY: close 25100, now 0.4% above the 20-day SMA.",
    "INFY: close 1500 today, up 1%.", "NIFTY: close 25100, the move from 25000 was 0.4%.",
    "TCS: close 4050 to 4080 range over 5 sessions.", "BANKNIFTY: hold of 52000 support was tested.",
    "RELIANCE: close above the 200-day moving average.", "INFY: close near 1500 for the week.", "TCS: close above VWAP all session.",
    "NIFTY: close 25100 at the bell; trading stopped at 15:30.", "TCS: close 4050 for the week.", "INFY: close 1500 with RSI 62.",
    "INFY: close 1500 in a range of 1480-1520.", "TCS: close 4050; exit poll results due.",
    # review round 12 (MED-1): an advice word before a level / candle noun describes it; "is fine" as the whole complement;
    # a third party's target in another clause; "now up / down"
    "INFY: close 1500 is fine.", "INFY: close 1500 is key support.", "TCS: close 4050 is a strong close.",
    "TCS: close 4050 is a good sign for the trend.", "TCS: close 4050 is the best close in 5 sessions.",
    "TCS: close 4050, now up 3 sessions straight.", "TCS: close 4050; brokerage target 4500 unchanged.",
    "INFY: close 1500 versus 1480; analysts see a target of 1600.", "TCS: close 4050 now 2% above the 50-DMA.",
    "TCS: close 4050 near the consensus target price.", "TCS: close 4050 marks a breakout from the range.",
    # round 12: recaps the structural reading keeps (past evidence, a bare value with an event, hold with a predicate)
    "TCS: close 4050 after results.", "TCS: close 4050 after results, up 2%.", "TCS: close 4050 after results on Thursday.",
    "TCS: close 4050 as IT rallied.", "TCS: close 4050 amid sector weakness.", "TCS: close 4050 despite FII outflows.",
    "TCS: close above 4000 since Monday.", "TCS: close above 4000 after 3 sessions below.", "TCS: close above 4000 again.",
    "TCS: hold above 4000 since the gap.", "TCS: hold above 4000 is intact.", "TCS: hold above 4000 continues.",
    "TCS: hold above 4000 for 5 sessions so far.", "TCS: hold of 4000 failed on Friday.", "INFY: close 1500 since Monday, a 2% gain.",
    "TCS: close 4050 - a 3-week high.", "INFY: close 1500 per the exchange data.", "INFY: close 1500, in line with the sector.",
    "TCS: close 4050 for a weekly gain of 2%.", "TCS: close 4050 for a 2% weekly gain.", "TCS: close 4050 is the highest since March.",
    "TCS: close 4050 was the week's best.", "TCS: close 4050 is bullish.", "TCS: close 4050 was a strong finish.",
    "TCS: close 4050; book value 1200.", "NIFTY: close 25100, smart money flows 2 cr.", "TCS: close 4050; then-CEO comments drew volume.",
    "TCS: close 4050 vs 4000, worth 2,000 cr in turnover.",
    # follow-ups of round 12: advice words only in the field's own ;-clause, recap infinitives, events after before / until,
    # sector and quarter words are never the level's instrument
    "TCS: close 4050, better than peers.", "INFY: close 1500, right at the 50-DMA.",
    "TCS: close 4050, just above the 50-DMA.", "INFY: close 1500 after good results.",
    "NIFTY: close 25100 as banks did well; IT was the best performer.",
    "TCS: close 4050; Q2 results were better than expected.", "NIFTY: close 25100, up 0.4% until the last-hour sell-off.",
    "TCS: close 4050 ahead of results on Thursday.", "TCS: close 4050, up 2% before results.",
    "INFY: close 1500 and cut its weekly loss to 1%.", "TCS: close 4050 to gain 1.2% on the day.",
    "TCS: close 4050 to cap a 3-session rally.", "TCS: close 4050 to stop a 3-day slide.",
    "TCS: close 4050 to end the week up 2%.", "TCS: close 4050, to finish 2% higher for the week.",
    "TCS: close 4050, or 1.2% above Monday.", "TCS: close 4050, so the weekly gain is 2%.",
    "TCS: close 4050; the company plans a buyback.", "TCS: close 4050; FII entry was 2,000 cr.",
     "TCS: close 4050, the right shoulder of the pattern.",
    "TCS: close 4050, a clear breakout.", "TCS: close 4050 on clear volume.",
    "NIFTY: close 25100; FIIs net sold 2,000 cr and DIIs bought 1,500 cr.", "NIFTY: close 25100 while banks lagged.",
    "NIFTY: close 25100 despite weak global cues.", "TCS: close 4050 after the company announced a buyback.",
    "TCS: close 4050 after results beat estimates.",
    "TCS: close 4050, a 3-week high, on 1.4x volume; INFY: close 1500, flat.",
    "TCS: close 4050 (+1.2%), RSI 62, above the 20-DMA; volume 1.1x the 20-day average.",
    "NIFTY: close 25100, up 0.4%; BANKNIFTY: close 52000, down 0.2%; IT led, metals lagged.",
    "TCS: close 4050, the strongest close in 5 sessions.",
    "TCS: close 4050, its best close since July.", "TCS: close 4050, which is 2% above the 20-DMA.",
    "TCS: close 4050, which was a 3-week high.", "TCS: close 4050 and held above the 50-DMA all week.",
    "TCS: close 4050, marking a 3-week high.", "TCS: close 4050, extending gains to a third session.",
    "TCS: close 4050, snapping a 3-day losing streak.", "TCS: close 4050, helped by strong deal wins.",
    "TCS: close 4050, led by IT stocks.", "TCS: close 4050, in a good session for IT.",
    "NIFTY: close 25100. If banks hold, the trend stays up.", 
    "TCS: close 4050, just shy of the high.", "TCS: close 4050, or about 1% higher.",
    "TCS: close 4050 to end the session higher.", "TCS: close 4050 to snap a 4-day losing streak.",
    "TCS: close 4050 to extend gains.",
    # review of #254: comparisons with the market after a bare value, a descriptive next sentence, a plain options greek
    "TCS: close 4050, better than the index.", "NIFTY: close 25100, stronger than the sector.",
    "TCS: close 4050. That is the highest close in 5 sessions.", "TCS: close 4050 before results. INFY: close 1500.",
    "NIFTY 25000 CE: Δ 0.45, θ -12, IV 14%.", "Volatility (σ) rose to 1.2%.",
    "TCS: close 4050. It was the best session in a month.", "TCS: close 4050. No doubt the bulls are in control.",
    "It was the best session in a month.", "No doubt the bulls are in control.", "TCS rose 2%. Buyers held 4000.",
]


@pytest.mark.parametrize("text", SHOULD_BLOCK)
def test_calls_aimed_at_an_instrument_are_blocked(text):
    for lang in ("en", "mr", "hi"):                                   # the request language never matters
        s = output_filter.screen(text, lang)
        assert not s.ok, (text, lang)
        ok, shown, why = output_filter.check(text, lang)
        assert not ok and shown is None and ("advice/guarantee" in why or why == output_filter.NOT_ENGLISH_REASON)


@pytest.mark.parametrize("text", SHOULD_PASS)
def test_descriptive_analysis_passes(text):
    for lang in ("en", "mr", "hi"):
        s = output_filter.screen(text, lang)
        assert s.ok and s.text == text, (text, lang, s.blocked)


def test_short_build_up_after_a_price_field_is_not_blocked():
    for text in ("NIFTY: close 25100, short build-up in futures.", "NIFTY: close 25100, short covering seen.",
                 "TCS: close 4050; brokerages kept a buy rating."):
        s = output_filter.screen(text, "en")
        assert s.ok and not s.blocked, text                  # review round 9: framed (the word "short" / "buy"), never blocked


def test_price_field_floods_stay_linear():
    import time
    from app.ai.call_detect import detect
    path = "app/ai/data/advice_terms.json"
    for text in ("TCS: close 1, " * 3000, "Close above 2450 in INFY. " * 1500, "INFY: close at 1500 - x " * 1700,
                 "TCS: close 1 after " * 2000, "- a " * 9500, "as per " * 5500, "is the a a " * 3500, "1.1.1." * 6400,
                 "TCS: close 1; analysts target 1; " * 1200, "Close above 1 in INFY was the a " * 1200,
                 "INFY: 1 la close then " * 1800, "INFY: weak, short below 1 " * 1500, "TCS: close 1; a " * 2400):
        started = time.monotonic()
        detect(text[:38000], path)
        assert time.monotonic() - started < 5, text[:30]            # review round 10: once per sentence, never per field


DEVANAGARI = ["RELIANCE खरेदी करा", "TCS बेचें।", "बाजार 24850 च्या वर बंद झाला.", "RELIANCE का भाव 20 दिन के औसत से नीचे है।",
              "NIFTY closed at 25100 (निफ्टी).", "TCS: close 4050 रहा, 1.2% ऊपर।", "Price closed above SMA 20. ठीक आहे"]


@pytest.mark.parametrize("text", DEVANAGARI)
def test_ai_text_with_devanagari_is_withheld_with_a_neutral_reason(text):
    from app.ai.call_detect import SCRIPT_LABEL
    for lang in ("en", "mr", "hi"):                                   # English only, whatever the request language
        ok, shown, why = output_filter.check(text, lang)
        assert not ok and shown is None and why == output_filter.NOT_ENGLISH_REASON, (text, lang)
    assert SCRIPT_LABEL in output_filter.call_terms(text)
    assert "English only" in output_filter.retry_hint(output_filter.findings(text))


NOT_PLAIN_ENGLISH = [
    "RELIANCE ખરીદો", "TCS কিনুন", "INFY வாங்கு",                       # Gujarati, Bengali, Tamil
    "КУПИТЬ INFY 1500", "INFY خریدیں 1500", "ΑΓΟΡΑΣΤΕ INFY 1500", "买入 INFY 1500", "INFY 매수 1500",   # review of #254, F2
    "INFY \U0001160e\U00011630\U00011628 1500", "INFY \u1c60\u1c64\u1c71 1500",          # Modi, Ol Chiki
    "B\u3164UY INFY", "B\u0301UY INFY", "S\u0336E\u0336L\u0336L\u0336 INFY", "B\u034fUY INFY", "BUY\U000e0101 INFY",
    "\u0299\u1d1c\u028f INFY", "B\u00f9y RELIANCE", "B\u00adUY INFY",                    # small caps, an accent, soft hyphen
    "B\u20ddUY INFY", "B\u0903UY INFY", "BU\u093eY INFY",                                   # enclosing and spacing marks
    "\U0001f1e7\U0001f1fa\U0001f1fe INFY", "\U0001f171\U0001f184\U0001f188 INFY", "\u249d\u24b0\u24b4", "\u24b7UY INFY",   # letters as symbols
]


@pytest.mark.parametrize("text", NOT_PLAIN_ENGLISH)
def test_text_that_is_not_plain_english_is_withheld(text):
    ok, shown, why = output_filter.check(text)
    assert not ok and shown is None and why == output_filter.NOT_ENGLISH_REASON, text


def test_plain_english_with_emoji_and_options_greeks_is_read():
    from app.ai.call_detect import not_plain_english
    for text in ("\u26a0\ufe0f NIFTY closed at 25100.", "Price closed above SMA 20 \U0001f680.", "NIFTY 25000 CE: \u0394 0.45.",
                 "Team \U0001f468\u200d\U0001f4bb notes: \u20b94,050 \u2013 RSI 62 \u2014 \u201cflat\u201d."):
        assert not not_plain_english(text), text
    assert not_plain_english("\u0394\u03b5\u03bb\u03c4\u03b1 rose")                      # greek letters next to letters


def test_the_strategy_interview_never_routes_its_stored_translations_through_the_filter():
    import inspect

    from app.ai import interview
    src = inspect.getsource(interview)
    assert "output_filter" not in src and "call_detect" not in src   # fixed, stored strings - not AI text
    assert any(re.search("[\u0900-\u097f]", str(q.as_dict())) for q in interview.QUESTIONS)


def test_a_price_field_is_read_at_its_own_offset():
    from app.ai.call_detect import _price_field, lists
    L = lists("app/ai/data/advice_terms.json")
    s = "WIPRO: close 4050 on volume as a precaution; TCS: close 4050 on volume"
    assert not _price_field("close", " 4050 on volume", L, s, s.index(" 4050 on volume"))
    assert _price_field("close", " 4050 on volume", L, s, s.rindex(" 4050 on volume"))   # review round 12, LOW: its own copy


def test_buy_and_hold_as_a_benchmark_is_not_blocked():
    s = output_filter.screen("Buy and hold NIFTY returned 12%.", "en")
    assert s.ok and not s.blocked                             # review round 6: framed (an imperative-shaped start), not blocked
    assert output_filter.call_terms("Buy and hold NIFTY returned 12%.") == []


def test_the_call_labels_name_what_was_found():
    assert output_filter.call_terms("Sell RELIANCE") == ["call: sell reliance"]
    assert output_filter.call_terms("x" * 40_001) == ["call: text too long to check (over 40000 characters)"]
    assert output_filter.call_terms("Entry RELIANCE 2500 SL 2450 TGT 2600") == ["call: entry / stop / target levels"]
    assert output_filter.call_terms("Price closed above SMA 20.") == []


def test_a_modal_call_on_a_level_without_an_instrument_stays_framed():
    s = output_filter.screen("One could buy the 25000 CE above 120.", "en")
    assert s.ok and s.framed                                  # documented: the spec allows framing here (call_detect docstring)
    assert not output_filter.screen("Buy 25000 CE above 120", "en").ok     # the imperative is blocked (consistent, LOW9)


def test_the_support_input_guard_does_not_refuse_how_to_questions_with_trade_words():
    from app.support import guard
    for question in ("Where do I enter stop 2% and target 4% for NIFTY?", "Sell TCS: where is the sell button?",
                     "How do I place a stop loss on a PAPER trade?"):
        assert guard.classify(question) is None, question             # calls are an output check, not an input one (MED5)
    assert guard.classify("Should I buy RELIANCE?") == "advice"
    assert output_filter.blocked_terms("Sell TCS") == [] and output_filter.findings("Sell TCS") == ["call: sell tcs"]


def test_names_from_the_instrument_master_count_in_any_case():
    from app.ai import instrument_names
    assert output_filter.screen("Buy Zydus Wellness here", "en").ok
    instrument_names.add(["Zydus Wellness Limited", "ACC", "Trent"])
    try:
        assert not output_filter.screen("Buy Zydus Wellness here", "en").ok
        assert not output_filter.screen("buy trent", "en").ok
        assert output_filter.screen("Add a cc line.", "en").ok           # one-word names under five letters are not added
    finally:
        for name in ("zydus wellness", "trent"):
            instrument_names._NAMES.discard(name)
            instrument_names._FROM_MASTER.discard(name)


def test_long_inputs_are_linear_and_capped():
    import time
    started = time.monotonic()
    assert output_filter.screen("1" * 4_000 + " for sure", "en") is not None          # a digit run: no cubic backtracking
    assert output_filter.screen(("stop 1 target 2 word " * 1_500) + "\n", "en") is not None
    assert time.monotonic() - started < 3
    assert not output_filter.screen("\n" * 39_000 + "Buy RELIANCE", "en").ok
    assert output_filter.screen("a, " * 13_000, "en").ok
    assert time.monotonic() - started < 5
    assert not output_filter.screen("x" * 40_001, "en").ok


def test_refresh_loads_long_symbols_and_rolls_back_a_failed_read():
    import asyncio

    from app.ai import instrument_names

    class _Rows:
        def all(self):
            return [("KAYNES", "Kaynes Technology India Limited"), ("ABB", "ABB India Limited")]

    class _Session:
        rolled_back = False

        async def execute(self, *_a, **_k):
            return _Rows()

        async def rollback(self):
            _Session.rolled_back = True

    class _Broken(_Session):
        async def execute(self, *_a, **_k):
            raise RuntimeError("no table")
    try:
        assert asyncio.run(instrument_names.refresh(_Session())) >= 2
        assert not output_filter.screen("buy kaynes now", "en").ok              # a symbol of five letters or more, any case
        assert not output_filter.screen("Buy Kaynes Technology India", "en").ok
        assert output_filter.screen("abb is short for an abbreviation", "en").ok
        broken = _Broken()
        assert asyncio.run(instrument_names.refresh(broken)) == 0 and _Broken.rolled_back
        import time
        assert time.monotonic() - instrument_names._LAST_REFRESH["at"] > instrument_names.REFRESH_SECONDS - instrument_names.RETRY_SECONDS - 5
        assert "campus" not in instrument_names.names() and instrument_names.add(["CAMPUS", "SAFARI"]) == 0
    finally:
        for name in ("kaynes", "kaynes technology india", "abb india"):
            instrument_names._NAMES.discard(name)
            instrument_names._FROM_MASTER.discard(name)
