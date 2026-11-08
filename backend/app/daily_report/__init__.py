"""M88-R (owner order M88, research R39): the shared daily AI market report.

* Universe: the admin setting "Shared report symbols" (`settings.py`); an entry "<index name> constituents" is read
  from NSE's official constituent file of that index (`universe.py`, refreshed on each run, the last good list cached).
  An empty setting makes no report.
* Input: end-of-day data only - the licensed NSE daily files in the shared store and ATP's own EOD reading (`inputs.py`).
  Never a live or intraday bar, never a row any user's broker session fetched (M86).
* One AI request per run on the admin's own AI key (Settings > AI), the output checked for recommendation words and
  asked again once (`check.py`, `build.py`); stored once per trading day; users read the stored report (no per-user AI
  call). Descriptive only: no buy / sell, entry, target, stop-loss or "should".
* Flag `shared_daily_report` (OFF by default). Nothing here places, blocks or changes an order.
"""
FLAG = "shared_daily_report"
PAGE_FLAG = "ui_v2"


def day_text(day: str) -> str:
    """'2026-11-06' -> '6 Nov 2026' (the app's date form)."""
    from datetime import date
    d = date.fromisoformat(day)
    return f"{d.day} {d:%b %Y}"


def info_line(day: str) -> str:
    """The line every part of the report carries."""
    return f"Last close {day_text(day)} · AI-generated · information only, not advice"
