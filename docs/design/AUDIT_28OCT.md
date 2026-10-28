# AUDIT 28 Oct - verification of the owner's complete audit (ATP_ORDER_AUDIT_28OCT.md)

Checked on origin/main b86710c (read-only, 28 Oct). Verdict per item: CONFIRMED / PARTLY / NOT REPRODUCED, with the file:line
found today (line numbers in the order drifted in places). Web sources the order cites (SEBI study, complaints, competitor
pricing) were not attached to the order message; they are added here when the owner sends them - none is invented.

## V - verify-fixes
| Item | Verdict | Evidence (origin/main) | Note |
|---|---|---|---|
| V1 forming bar | CONFIRMED | workers/trading_worker.py:1676 `_evaluate_deployment`, :1685 `get_frames`, :1732 `strategy.analyze`; market_data/service.py:407-413 (`get_candles` -> `build_frames`, bars as they are); strategy_templates/adapter.py:131-150 reads `df.index[-1]` | no trim of bars with start + TF > now; only `candle_staleness` (:1702-1705). Screener trims: screener/runtime.py:115 `closed_only` (routes.py:694, :749) |
| V1 parity test | PARTLY | strategy_templates/parity.py:117-123; tests/test_tpl1_parity.py:39 | parity exists but calls `analyze()` on fixed closed bars - never through `get_frames`, so it cannot catch V1 |
| V2 after-hours P&L | CONFIRMED | portfolio/routes.py:44 / :65 / :68 (live LTP only, else "entry"); position_monitor.py:1343-1346 `_mark`, :1250-1253 open families only; frontend dashboard/model.ts:177-181 `fullyPriced` needs `price_source === "ltp"`, :186 | `mark_price` never read by the portfolio route |
| V3 VIX bands | PARTLY | dashboard/model.ts:141-148 `vixNote` words; ui/levels.ts:21-26 `VIX_BANDS` [12,16,20] + `vixLevel`, :38-44; LevelChip (rhythm/SectionRowsV2.tsx:166); ai/market_memory.py:228 | same thresholds, different words (duplicate table) |
| V4 discipline score | CONFIRMED | journal/service.py:148-151 (open rows included), :202, :214; journal/score.py:47-49, :56; rhythm/build.py:470-476 | only-open untagged trades score 100 with 0 closed |
| V5 OI hysteresis | CONFIRMED | option_chain/oi_regime.py:216, :244-255, :189-203 (:197 both sides >= 2 %, :199 calls up AND puts down) | flip needs 3 negative diffs AND puts falling >= 2 % twice; else "BULLISH (Weakening)". Doc: docs/design/OI_BANNER.md |
| V6 text budget | CONFIRMED | frontend/e2e/textBudget.ts:17-20 (`p`), :24-28 (`li`), :13 skips td / dd / th / dt | div / span text not counted |
| V7 repo hygiene | CONFIRMED | `ls-remote --heads 'claude/*'` = 620; tags = 0; no CHANGELOG; no workflow deletes branches; repo `delete_branch_on_merge` = false | |

## G1 - before public launch
| Item | Verdict | Evidence | Note |
|---|---|---|---|
| G1.1 DPDP erase / export | CONFIRMED | team/routes.py:256, :273 (OWNER erases a member); retention/service.py:180 `erase_user`; exports/routes.py:59, :69 (organisation datasets) | no /api/me/export or /api/me/erase; help.md:153 mentions "delete account" with no route |
| G1.2 legal placeholders | CONFIRMED | legal/content/privacy.md:14, :86-89; terms.md:10, :56, :60, :68; legal/documents.py:13 | no docs/runbooks/BREACH.md (no runbooks folder) |
| G1.3 /docs exposed | CONFIRMED | main.py:213-219 (no docs_url / redoc_url / openapi_url); deploy/Caddyfile:25-26 blocks /metrics only | Caddyfile.ip to check in the fix |
| G1.4 error tracking / redaction | CONFIRMED / PARTLY | no @sentry in frontend; core/logging_config.py:40-45, :85, :103 redact OAuth callback query + token params | the order's "Telegram only" is not what the code does: it redacts OAuth / token query strings; no email / phone redaction |
| G1.5 Web Vitals | CONFIRMED | no lighthouse / web-vitals anywhere | |
| G1.6 tooling | CONFIRMED | no dependabot / renovate; no ESLint; backend/ruff.toml select E4 E7 E9 F B006 S102 S307 T10 PLE; backend/mypy.ini `files =` allow-list (ci.yml:68-70) | |
| G1.7 SEBI readiness | PARTLY | compliance/static_ip.py, compliance/algo_id.py exist (tests d4 / d1); no Compliance page in routes.ts; RiskManagementPage has no disclaimer (shared `Disclaimer kind="signals"` at components/ui.tsx:121-126 unused there) | pieces exist; page + text missing |

## G2 / T - not verified line by line (proposals; each verified when its row starts)
Already present (do not rebuild): T8 public status page (platform/routes.py:64 `/status`, frontend routes.ts:74, flag
`public_status_page` flag_catalog.py:107; .github/workflows/uptime.yml) and docs/SLO.md exist - T8 extends them (per-session
uptime, degradation banner). T9: plans/registry.py:76 / :86 already give Pro and Business a 14-day trial.

## Summary
CONFIRMED 13, PARTLY 4 (V1 parity, V3, G1.4, G1.7), NOT REPRODUCED 0 of the V / G1 claims; T8 / T9 partly already built.
