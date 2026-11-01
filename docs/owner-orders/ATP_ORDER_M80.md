# ATP ORDER M80 - Admin › Feature flags: the status badge must tell the truth (owner order, 1 Nov, verbatim)

M80 (ATP) - Admin › Feature flags: the status badge must tell the truth (owner screenshot review).

Finding (code + screenshot): backend/app/platform/flag_catalog.py `status` is a static audit label from 22 Oct, never updated. 33 of 124 flags show "Unfinished / needs setup"; 11 of them have an empty `needs` (vision_byok_v3, strategy_lab_pine, multi_account, screener_legends_lg3, screener_waves, term_roll, term_whatif, strategy_templates, chart_analyze_share, growth_v2, marketing_site), so the owner cannot tell what is unfinished or what to set up. Several are ON (multi_account, term_whatif, strategy_templates) while labelled unfinished, and some were merged after 22 Oct.

F1 Re-audit every flag against main today (built? tests? what is missing?) and split the one badge into exact states: "Ready" / "Ready (behind the scenes)" / "Needs your setup: <the exact item>" / "Not built yet: <what is missing, which order>" / "Owner decision". No row may say "needs setup" without naming the item.
F2 "Needs your setup" names the concrete thing and where to do it in place (e.g. "AI key - Settings › AI", "Telegram bot - Settings › Alerts", "private package on the server - owner host block"); the row shows it beside the switch, one line, no prose.
F3 Live check where possible: when the need can be detected (AI key present, broker connected, private package importable, Telegram set), show ✓ / ✗ from the real state, not the static text. trade_engine keeps its current "not installed on this server" line.
F4 A flag that is "Not built yet" shows the switch disabled with that reason (turning it on gives a user nothing). Do not change any flag's current ON/OFF value and do not change defaults; report any flag that is ON but not built, for the owner to decide.
F5 Keep catalog and FEATURE_FLAGS in sync (tests/test_ux1_flag_catalog.py); add a test that every non-ready status has a non-empty reason.
Proof: one before/after page of the Flags list (desktop + phone 390, dark + light), plus a table of all 124 flags: old status -> new status, reason. Report in Marathi <= 25 lines.
