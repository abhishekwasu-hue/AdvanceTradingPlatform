# Owner order SCR-FIX - Screener audit + fix pack (27 Oct 2026, verbatim)

Roadmap: P1 row SCR-FIX (blocks the day-one test). Replaces all earlier Screener messages of 27 Oct (incl. "SCREENER FIX PACK").

```
OWNER ORDER — SCREENER AUDIT + FIX PACK (27 Oct). Replaces ALL my earlier Screener messages today (incl. "SCREENER FIX PACK").
Roadmap: one P1 row "SCR-FIX" (blocks the day-one test). File in INBOX verbatim / DECISIONS / roadmap / README first.
An overseer code audit (read-only, file:line) found the items below. Re-verify each against the code before fixing (one line each in
the first report: confirmed / not reproduced). PRs in order A → E, each its own draft PR, self-review every diff, tests for each item.

OWNER DATA (phone 390 px, dark, 22:04–22:14): NIFTY 50 · 15 min · RangeBreakout(...) → every symbol "Could not be read · BrokerAPI:
Interval accepts one of (1minute,30minute,day,week,month)"; page said "50 stocks did not match"; Run button invisible; ScreenQL ran
off the screen; Show on chart = static drawing; Telegram send = dead sentence; ULTRACEMCO dropped by the 50-symbol session budget.

PR A — DATA PATH (P1; one shared fix in the market-data layer, every caller)
A1 Non-native intervals go raw to the broker: screener/routes.py:98 (_FETCH), :456, :494. Upstox (upstox.py:39 map) fails 5m/15m/1h;
   Dhan (dhan.py:61,415) likely fails 30m. Fix once: a per-adapter list of native intervals; a non-native interval is fetched at the
   largest native interval that divides it and resampled with the existing anchored resampler (as Charts do, candles_routes.py:422).
   Verify and state Upstox's history limit for 1minute bars vs MAX_INTRADAY_FETCH_DAYS = 60 (use 30minute for 1h if 1minute history
   is too short; say whether the adapter supports Upstox v3 custom units — no new API dependency without stating it).
A2 Same bug elsewhere — all fixed by A1, each with a test: alerts/barclose.py:486 (Daily alerts' extra "15m" fetch fails on Upstox and
   the error is dropped at :510 → Daily alerts silently decide on yesterday's bar; report the problem, never drop it);
   screener/breakouts/routes.py:41,205 (15m / 1h / 2h / 4h); ai/evidence.py:59 via ai/tools/market.py:48 and backtest/bar_source.py:186
   (Copilot market / backtest); ai/tools/screener.py:359 (Copilot screener).
A3 Daily window: market_data/service.py:316-318 always fetches 400 days → 1w screens needing > ~57 bars and 1M > ~13 bars always fail
   after broker login (stored_history.py sizes by need). Size from fetch_lookback (or get_daily_range).
A4 Today's finished session: 1d/1w/1M screens run after the close never include today's bar (service.py:346-351, calendar.py:180-193),
   while Charts and alerts (with_today) do. Attach it the same way.
A5 Last 1h / 30m bucket: runtime.py:42,128-131 gives the session's final partial bucket a full duration → treated as never closed;
   end it at the session close, so results match Charts and every broker.
A6 Session budget (entitlement.py:218-220, fanin.py:51-61,135): a NIFTY 50 run with any chart open drops symbols with "EOD data for it
   until a slot frees up" but never uses EOD. Owner default: historical scan reads finish every symbol (auto-batch through the existing
   batch path, or stored daily bars for daily TFs), and the run shows "N held back by the session budget" once. Explain in ≤ 3 lines.
A7 Index volume: apply index_volume.replace_volume in fetch_frames like Charts (candles_routes.py:191) — verify first (unverified).
A8 Batch plan uses the first credential alphabetically (universes.py:63-66) while the run uses "primary" (routes.py:477) — use primary.
A9 Tests (P1 gap): parametrized test — every screener TF × every adapter map → native or resampled; parity: resampled screener bars ==
   Charts bars on the same window. Today's fakes accept any interval (e.g. test_s1d_screener_api.py:103), so A1–A2 could not be caught.

PR B — TRUTHFUL RESULTS
B1 No-data is never "did not match": ScreenerPage.tsx:405-411,594 counts unread symbols as not matched; runtime.py:1392 returns
   Match(sym, False) with no reason for NaN. Separate counts everywhere (funnel, Conditions card, Results, bottom bar, stored run):
   "N matched · M could not be read", grouped reason in one line + ONE action (Log in / Connect broker / Retry / other timeframe).
B2 Run payload (routes.py:686-694): add unread counts + a run-level error when nothing could be read (today 200 with 0 matches); the
   preview carries the unread count and the first error.
B3 Plain reasons: no exception class names to users (routes.py:499, runtime.py:1386-1390, library_routes.py:249) — machine reason code +
   plain sentence; keep the full text (not cut at 120 chars, routes.py:496-499) in the log; catch BudgetUsed by class, not by name.
B4 One count source: FunnelCanvas.tsx:55 uses funnel.with_data while the bar uses symbols.length → "Matched 0 of 0" vs "1 of 50".
   Show "Matched N of 50 · K had data" from one source.
B5 Why it matched (evidence.py:177-185, 672-673): carry group operator + negated from parent groups into every row; with OR, mark the
   branch that was true (✅) and the others grey / ✗; a condition required to be false never shows ✅ for being true. Verify the owner's
   "≠ DOWN" (text) vs "≠ UP" (table) report.
B6 Replay parity (evidence_routes.py:172-180): attach the lake daily bars as "1d" so replay equals the run; hide the replay controls
   when replay is not available (EvidencePanel.tsx:66-86, 116, 205, 231 — today buttons + "Replay is not available" side by side).

PR C — REAL CHART + TELEGRAM IN PLACE
C1 "Show on chart" (EvidenceChart.tsx:19-73, a 720×360 SVG shrunk to ~330 px, 10 px labels, no time axis / volume / zoom, grey candles):
   render the existing EmbeddedChartV2 / ChartV2 (same as the Charts page: up / down colours, axes, volume, zoom, crosshair, timeframe
   shown), scrolled to the matched bar, matched bar marked, thresholds as level lines, boxes / bands as overlays. "Open in Charts" button.
C2 Real-data proof: evidence bars == Charts bars (parity test); show the matched bar's time + O/H/L/C beside the chart.
C3 Telegram image rendered from the same chart (axes, colours, symbol, timeframe, bar time).
C4 Telegram in place: the evidence API returns telegram_linked; when false, EvidencePanel shows <TelegramConnectV2 title={false}
   onChanged={reload}/> (one-time code → t.me/<bot>?start=<code>), then sends automatically once linked. Machine reasons (not_linked /
   bot_not_configured / blocked_by_user / send_failed) → the right action. Bot not configured: button hidden for users; admin console +
   `atp.sh health` say so with .env key NAMES only. Report: is ATP_TELEGRAM_BOT_TOKEN set on production? (yes / no only)
C5 Chart grid / heatmap on phone (ResultViews.tsx:72-79, 34, 42: labels ~4–8 px): one column on phone, real chart or readable sizes;
   a grid card whose evidence fails to load says so (ResultViews.tsx:123 swallows the error).

PR D — PHONE UI
D1 Run button (ScreenerPage.tsx:690,697; Button.tsx:12,35; tokens.css:463,472): button vs bar 1.61:1 (1.24:1 when disabled). Bright
   primary fill or ≥ 3:1 outline on the bar; disabled state shows why ("Add symbols" / "Fix stage 2"); 44 px (size v2) for Run and Save;
   Save gets a text label. CI contrast check for primary buttons on every surface token.
D2 ScreenQL overflow (ParamInline.tsx:130,134 size={text.length+1}; StageRow.tsx:145-147, 272, 58 min-w-[14rem], whitespace-nowrap;
   AppShellV2.tsx:230 main overflow): w-full min-w-0 max-w-full / wrapping textarea; no min-w on phone; the page never scrolls sideways.
D3 Conditions footer (FunnelCanvas.tsx:124-130, 148-161): flex-wrap; count inside the card.
D4 Rotated group labels (FunnelCanvas.tsx:87, StageRow.tsx:186 -rotate-90) → horizontal "All of these must pass" / "Any of".
D5 Results table (ResultBoard.tsx:59-84): table-fixed, Symbol nowrap + sticky, Result wraps; stacked rows on phone.
D6 Evidence verify table (EvidencePanel.tsx:150-168, 136): plain words, break-words, Result first or cards on phone.
D7 Plain words by default (StageRow.tsx:145, 310; EvidencePanel.tsx:113, 136, 163): no raw ScreenQL unless "Advanced" is open.
D8 Six browser <select>s (ScreenerPage.tsx:534, TimeframeChip.tsx:20, ParamInline.tsx:94, WatchlistPicker.tsx:89, LibraryTools.tsx:165,
   EtfPanel.tsx:43) → the shared Select / combobox. The timeframe list offers only what the data path serves (after A1: all).
D9 LibraryTools.tsx:98-103 confluence table: overflow-x-auto wrapper. Header buttons (ScreenerPage.tsx:473-507, up to 8) → "More" menu
   on phone.
D10 NIFTY 50 fallback list (frontend/src/screener/universe.ts:18) is hardcoded with no date → as-of date + warning when used.

PR E — TEXT (UX-11) + GUARDS
E1 One "not advice" line per page footer (today up to 3 on one phone screen: ScreenerPage.tsx:550, 618; EvidencePanel.tsx:176;
   IdeaPanel.tsx:205; LibraryTools.tsx:127, 192; ScanAlertPanel.tsx:40); other explanations → ⓘ Details; numbers first.
E2 CI: Playwright 390 px, dark + colour v2 and light: Screener with a formula block, the Conditions footer, the evidence dialog, the
   Telegram-unlinked state, the bottom bar; assert document scrollWidth <= clientWidth and no element overflows its card (none of these
   is covered today: e2e/scan-universe-v2.e2e.ts, screener-idea-v2.e2e.ts).

PROOF (after E, once): the same NIFTY 50 / 15 min run + a 5 min, 1 hour and weekly run; a Daily alert firing on today's bar; SBIN
"Show on chart"; "Send to my Telegram" unlinked → connect → sent — phone 390 px dark + light and desktop, before / after, ONE comparison
page. Each PR: deploy line + migration yes / no. Reports ≤ 15 lines per PR; first report = the re-verification list.
Owner decisions (defaults, do not wait): A6 = auto-batch + stored daily bars, one notice; D7 = plain words by default, code under Advanced.
```
