"""Front-door image "Daily report on Telegram": the Telegram message the app itself sends (M89 TG1,
backend/app/daily_report/telegram.py `message_html` and `buttons`) for a MADE-UP report on a placeholder index.

Public image rule (CLAUDE.md §3): no real name ("Index A"), no price (the index close is left out, so the message has
no price line), no performance claim. Prints {"html": ..., "buttons": [...]} for front-images.e2e.ts.
  python3 scripts/front-images/telegram_message.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "backend"))
from app.daily_report.telegram import buttons, message_html  # noqa: E402

REPORT = {
    "info_line": "Last close 31 Jul 2026 · AI-generated · information only, not advice",
    # close None: the message has no price line (the symbol is shown only next to its close, so no name either).
    "index": [{"symbol": "Index A", "close": None, "change_pct": 0.42, "missing": False}],
    "breadth": {"advances": 32, "declines": 18},
    "sections": {
        "index_read": "Index A closed higher; the daily structure is up, the weekly is in a range.",
        "breadth_read": "More stocks rose than fell; most sectors closed higher.",
        "conclusion": "A firm close with broad participation.",
    },
}

if __name__ == "__main__":
    keyboard = buttons("https://app.invalid", 1, 1) or {"inline_keyboard": []}
    print(json.dumps({"html": message_html(REPORT), "buttons": [b["text"] for row in keyboard["inline_keyboard"] for b in row]}))
