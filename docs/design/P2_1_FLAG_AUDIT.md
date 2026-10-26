# P2-1 flag audit: every "unfinished" flag -> finish / keep OFF hidden / remove from view (R6)

Owner order 26 Oct (review/design session, item 3). Source: `backend/app/platform/flag_catalog.py` (status
`unfinished`), defaults in `platform/controls.py` (`DEFAULT_ON_FLAGS`: none of these), the trader pack
(`platform/trader_pack.py` `PACK` / `EXCLUDED_REASONS`, "Enable trader features"), FN0 rows
(`docs/design/FN0_FUNCTIONAL_AUDIT.md`) and the roadmap items. Main 232bf03. Docs only; the owner's answers are applied
in a later PR (catalog status + roadmap), never by deleting a flag (CLAUDE.md §8).

Fates (R6): **Finish** = an item, in roadmap order; **Keep OFF** = hidden, with the reason; **Remove from view** =
the flag stays in code, never shown to a user and dropped from the admin "turn on" list until its item exists.

## Finding first: 7 "unfinished" flags are turned ON by the trader pack

`markets_view`, `data_hub`, `strategy_templates`, `strategy_lab_pine`, `idea_library`, `daily_rhythm` (and
`ai_one_key`, which the pack comment calls unfinished but the catalog marks ready) are in `PACK`. Once the owner presses
"Enable trader features", they are visible to every user while the catalog still says "unfinished" - an R6 breach on
paper. The pack comments say the visible part works (owner decision 23 Oct). Recommendation: keep them in the pack,
and close the gap per flag below - the catalog status moves to `ready` only when its FN item passes the FN1 checklist
on production; until then the admin list shows "in the pack, finishing in <item>".

## The table (26 flags; all OFF by code default)

| # | Flag | What the user sees | Pack | FN0 | Recommendation | Why / item |
|---|---|---|---|---|---|---|
| 1 | `data_hub` | live prices on every page | yes | P | **Finish** - 1st | P1-5 (traders first, data step); API-call numbers before / after |
| 2 | `markets_view` | Sectors & indices, chain summary | yes | H | **Finish** - 2nd | FN7 proof on daily data; sector lists from DATA-H step 1 |
| 3 | `strategy_templates` | templates in the paper bot form | yes | H | **Finish** - 3rd | P2-3 -> FN4 (adopt = PAPER strategy, J3) |
| 4 | `idea_library` | Ideas page | yes | H | **Finish** with #3 | P2-3 -> FN4 |
| 5 | `strategy_lab_pine` | Pine button on the Screener | yes | H | **Finish** | FN3 (never executed; run as screen only) |
| 6 | `daily_rhythm` | Dashboard card; Telegram by choice | yes | H | **Finish** | P2-4 -> FN7 |
| 7 | `chart_contracts` | contract charts with OI | no | H | **Finish** after DATA-H | needs the nightly download; FN7 |
| 8 | `chart_futures_basis` | basis / rollover window | no | H | **Finish** with #7 | same |
| 9 | `help_centre` | Help centre, Report a problem | no | - | **Finish** early | P2-6; owner step: support number / e-mail in admin; day-one testers need "Report a problem" |
| 10 | `chart_analyze` | Analyze a chart page | no | H | **Finish** | P2-5, one family with CP-V (CPV-1); vision key needed |
| 11 | `chart_analyze_strikes` | strikes block on the result | no | H | **Keep OFF** | strikes next to a setup read close to advice (§3); after P2-5 is ON and the owner sees it |
| 12 | `chart_analyze_share` | Share button | no | H | **Keep OFF** | CPV-6 default: download only, no share link; same rule for both readers |
| 13 | `vision_byok_v3` | AI comments on alerts, daily chart review | no (excluded) | H | **Remove from view** | superseded by CP-V (CPV-16 / CP_V_SPEC F7); fold the daily review into CP-V later |
| 14 | `market_thesis` | per-symbol scenarios in the Copilot | no (excluded) | H | **Keep OFF** | needs market memory; scenarios per symbol sit close to advice (§3); revisit after CP-V |
| 15 | `ai_research` | AI drafts auto back-tested + scored | no | H | **Keep OFF** | AI cost per draft, no FN item; after FN5 |
| 16 | `support_bot` | Help bot | no (excluded) | - | **Keep OFF** | answers from help pages that are not complete; after #9 / P2-6 |
| 17 | `screener_fundamentals` | company figures in screens | no | H | **Keep OFF** | no licensed fundamentals source |
| 18 | `screener_legends_lg3` | value / growth Legends cards | no | H | **Keep OFF** | needs #17 |
| 19 | `screener_ownership` | promoter / FII / DII fields | no | H | **Keep OFF** | no ownership data source |
| 20 | `screener_waves` | wave count in screens | no | H | **Keep OFF** | wave reading is method content; after the wave templates / P5-1 |
| 21 | `mobile_push` | phone notifications | no | - | **Keep OFF** | FCM key + the app (P5-4) |
| 22 | `alerts_whatsapp_dlt` | WhatsApp / SMS alerts | no | - | **Keep OFF** | DLT registration (owner / legal); Telegram covers alerts |
| 23 | `growth_v2` | plan & usage meter, discipline share card | no (excluded) | - | **Keep OFF** | plans come with payments (P5-2); the share card is discipline-only (no price / P&L, checked in `growth/routes.py`) |
| 24 | `learn` | Learn page | no | - | **Keep OFF** | education text needs the legal review (P3-5); then P2-6 |
| 25 | `marketing_site` | public pages | no | - | **Keep OFF** | public launch only (P5-3, legal text first) |
| 26 | `onboarding_emails` | first-week e-mails | no | - | **Keep OFF** | only with self-signup (P5-3) and the platform mailer |

Counts: Finish 10 (6 already in the pack), Keep OFF 15, Remove from view 1. The 13 `decision` flags are not in P2-1
(FN0 default: they stay OFF).

## Owner decisions (defaults if no answer)

| # | Question | Default |
|---|---|---|
| FA-1 | Pack flags shown while "unfinished": accept until each FN item passes? | Yes; admin list says "in the pack, finishing in <item>"; catalog -> `ready` only after FN1 on production. |
| FA-2 | `vision_byok_v3` removed from view (superseded by CP-V)? | Yes; code kept, flag stays OFF and leaves the admin "turn on" list. |
| FA-3 | `help_centre` finished early (before the rest of P2-6) for day-one testers? | Yes, once the support contact is set in admin. |
