# ATP_ORDER_M60 - Sidebar / tabs rationalisation (owner order, 30 Oct, verbatim; proposal first, build only after the owner's ✅)

M60 · ATP · Sidebar / tabs rationalisation (proposal for owner ✅; inside M49 IA; implement only after the owner's yes)
Finding (shell/navModel.ts:68-109, pageGroups.ts:28-40, main 56d7b64): 17 user items in 5 sections — Markets: Today, Charts, Watchlist, Option chain, Sectors & indices, Find setups · Trading: Terminal, Signals, Trading bot, My trades · Insights: Performance, Strategies, AI Copilot, News & events, Learn · Account: Settings (+ Broker pinned) · Admin. Duplicates: WATCHLIST appears three times (own page /watchlist, the right column of Charts, the Terminal panel per M43 UX-3); OPTION CHAIN twice (page + Terminal panel); CHARTS twice (page + Terminal chart panel); strategy work in four places (Strategies tabs Library/Ideas/Build/Test, Signals, Trading bot, Find setups + Custom scan); "Today" sits under "Markets"; Performance/Journal/Coach are a separate section from My trades although they describe the same trades.
Proposed sidebar (10 items, 4 sections; phone bottom bar = first 4 + More):
TRADE — Today · Terminal (chart + option chain + watchlist + order; Charts = a "Chart only" layout preset; Option chain full page = the panel's expand button) · My trades (tabs Positions · Orders · Money · Results · Journal)
FIND — Find setups (screener; Custom scan merged; saved scans as tabs) · Signals · Sectors & indices
BUILD — Strategies (tabs Library · Build · Test) · AI Trading Bot
LEARN & AI — AI Copilot (Ask, Read the chart, Analyze a chart/upload, Coach) · Learn (lessons + Ideas + News & events)
ACCOUNT — Settings (Broker accounts inside, with the connection dot on the Settings row) · Admin (super-admin only)
Rules: one feature = one home; the Watchlist is a PANEL (Terminal + Charts preset), never a page — /watchlist redirects to the Terminal with the panel open; every removed page keeps its address as a redirect and stays searchable; each tab row ≤ 4 tabs; section titles short; icons + accent per section (M49 colour).
Deliver first: a one-page before/after table (item, today's place, proposed place, redirect) + a phone and desktop mockup of the new sidebar/bottom bar (docs/ui/, per M49 step 1). Wait for the owner's ✅, then implement with redirects and tests (every old route resolves; search finds every page).
