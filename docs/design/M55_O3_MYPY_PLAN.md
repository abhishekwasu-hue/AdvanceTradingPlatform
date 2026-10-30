# M55 O3 (D1): mypy plan for workers / trading / brokers / ai / backtest

Measured 30 Oct 2026 on `origin/main` (eba6bd0a) with the CI's pinned `mypy==2.4.0` and `backend/mypy.ini`
(`python_version = 3.11`, `follow_imports = silent`, `ignore_missing_imports = True`), from `backend/`:

    mypy app/workers app/trading app/brokers app/ai app/backtest

Result: **173 findings in 42 files** (126 files checked). None of these packages is in the gate yet; this PR changes
none of their code. `app/backtest` has 37 findings (over the 5-finding threshold), so it is not fixed or gated here.
`app/trading/trigger_rules.py` is already gated and clean.

| Package | Findings | Files with findings |
|---|---:|---:|
| app/ai | 83 | 21 |
| app/backtest | 37 | 7 |
| app/brokers | 21 | 7 |
| app/workers | 20 | 2 |
| app/trading | 12 | 5 |
| **Total** | **173** | **42** |

By error code: arg-type 66, operator 30, assignment 25, misc 16, union-attr 12, attr-defined 11, var-annotated 3,
return-value 3, index 3, type-var 2, call-overload 1, call-arg 1. Most are `Optional` values used without a guard
(`x < None`, `float(None)`) and attributes initialised to `None` without an annotation.

## Fix items (one per file group; each its own small PR, the package added to `mypy.ini` `files` when it reaches 0)

Order: smallest blast radius first; the worker, broker and order-path items (M1, M2, M5-M7) are done on the main
model and only when no other open PR is editing those files. Types only - no behaviour change; where a guard is
added, a test pins the existing behaviour for the `None` case first.

| ID | Files | Findings | Fix plan (one line) |
|---|---|---:|---|
| O3-M1 | workers/trading_worker.py | 16 | Annotate the `_last_*_day` attributes in `__init__` as `date \| None` (13 findings); narrow the account / chain values at 1247, 1408, 2347 with an explicit guard or `cast`. |
| O3-M2 | workers/routes.py | 4 | Build `WorkerStatusResponse` from a `TypedDict` (or explicit keyword arguments) instead of `**dict[str, str \| int \| None]`. |
| O3-M3 | trading/analytics.py, trading/routes.py | 8 | Filter `None` P&L values before the `<` / `>=` comparisons and sums; narrow the optional broker before `get_ltp_for_symbol`. |
| O3-M4 | trading/position_monitor.py, trading/persistence.py, trading/cost_meter.py | 4 | Guard the optional `account_id` / exit time / price before use; `CursorResult` (not `Result`) for the `rowcount` read. |
| O3-M5 | brokers/dhan.py, brokers/upstox.py | 8 | Give the mixed str / int payload lists and dicts their real element types; guard the optional dates and prices. |
| O3-M6 | brokers/angel_one.py, brokers/coindcx.py | 6 | Drop `None` expiries before `sorted` / `<=`; annotate `_recent_order_ids: list[str]`; guard the optional price. |
| O3-M7 | brokers/smoke.py, brokers/registry.py, brokers/login_reminder.py | 7 | Type the smoke-step parameters as `str` (not `object`); construct the concrete broker class in the registry; guard the optional broker name. |
| O3-M8 | ai/tools/*.py (copilot, copilot_trades, market, proposals, read, screener) | 26 | One change in `ai/tools/__init__.py`: make `Tool.fn` accept the tool's own input model (`Callable[[ToolContext, Any], Awaitable[ToolResult]]` or a generic `Tool[M]`) - clears all 26. |
| O3-M9 | ai/thesis.py, ai/briefing.py | 21 | Separate the str-valued labels from the float dict at thesis 159-163; filter `None` values (times, P&L, thresholds) before comparisons, `sorted` and `max`. |
| O3-M10 | ai/strategist.py, ai/generator.py | 11 | `Literal` types for the indicator / operator strings; annotate `per_day` / `out`; guard the optional range, JSON text and suggestion. |
| O3-M11 | ai/monitor.py, ai/chat_history.py | 6 | `CursorResult` for the `rowcount` reads (`await session.execute(update(...))` typed via `cast`); guard the optional run record. |
| O3-M12 | ai/market_memory.py, ai/sentiment.py, ai/global_cues.py, ai/interview.py | 12 | Replace the `object`-typed dict values with a `TypedDict` / `dict[str, Any]` so `.append`, `+`, indexing and `float()` type-check. |
| O3-M13 | ai/key_formats.py, ai/settings.py, ai/routes.py, ai/knowledge.py, ai/copilot_chat.py | 7 | Rename the reused `Detection` variable; return `LLMProvider` from a `MeteredProvider` that implements it (Protocol); guard the optional tenant / text / price. |
| O3-M14 | backtest/options_engine.py | 15 | Guard the optional strike / premium / exit rules (raise if a leg arrives incomplete - the planner never builds one); type `entry_time` as `datetime`. |
| O3-M15 | backtest/optimizer.py, backtest/robustness.py | 13 | A `TypedDict` for the optimiser's parameter grid values; type the robustness result as `BacktestResult`, not `object`. |
| O3-M16 | backtest/engine.py, backtest/analytics.py, backtest/options.py, backtest/routes.py | 9 | Filter `None` P&L values in the sums / counts; guard the optional weekday; pass the fingerprint's options by name instead of `**dict`. |

When a package's last item lands, the same PR adds the package to `mypy.ini` `files` and updates the plan comment
above it (AUD-G1.6). Re-measure before starting an item: other PRs touch these files.
