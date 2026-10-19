"""System notifications derived from stored records (canonical signal, forward test, feed status, scan results).

Kinds and default channels (users opt in to Telegram/e-mail per kind in Watchlist & Alerts):
  signal_invalidated, signal_completed, forward_closed, daily_summary, feed_failure, backtest_done.
Controls: duplicate suppression per (user, kind, dedup_key, channel) in the database, quiet hours and the hourly cap
from the user's delivery settings, bounded retries inside the channel senders. A message is recorded as `sent` only
when the channel reported success; `suppressed` / `failed` carry the reason.
"""
from __future__ import annotations

from datetime import timedelta

from ..storage.db import utcnow
from .alerts import default_senders, in_quiet_hours, notify_settings

KINDS = ("signal_qualified", "signal_invalidated", "signal_completed", "signal_expired", "forward_closed", "daily_summary", "feed_failure", "backtest_done")
DEFAULT_PREFS = {k: ["in_app"] for k in KINDS}


def prefs(store, user_id: int) -> dict:
    p = store.get_pref(user_id, "event_notifications", None) or {}
    return {k: list(p.get(k, DEFAULT_PREFS[k])) for k in KINDS}


def save_prefs(store, user_id: int, p: dict) -> None:
    clean = {}
    for k, chans in p.items():
        if k not in KINDS:
            raise ValueError(f"unknown notification kind {k}")
        bad = [c for c in chans if c not in ("in_app", "telegram", "email")]
        if bad:
            raise ValueError(f"unknown channel(s) {bad}")
        clean[k] = list(dict.fromkeys(chans))
    store.set_pref(user_id, "event_notifications", clean)


def notify(store, user_id: int, kind: str, dedup_key: str, text: str, senders: dict | None = None, now=None) -> list[dict]:
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    now = now or utcnow()
    settings = notify_settings(store, user_id)
    out = []
    for ch in prefs(store, user_id)[kind]:
        if not store.notification_add(user_id=user_id, kind=kind, dedup_key=dedup_key[:80], channel=ch, status="pending",
                                      message=text[:4000], created=now):
            continue                                           # duplicate: already handled once
        if ch == "in_app":
            status, err = "sent", None                         # stored in the in-app inbox = delivered
        elif in_quiet_hours(settings, now):
            status, err = "suppressed", "quiet hours"
        elif store.notifications_sent_since(user_id, ch, now - timedelta(hours=1)) >= settings["max_per_hour"]:
            status, err = "suppressed", f"rate limit ({settings['max_per_hour']}/hour)"
        else:
            s = (senders or default_senders(store)).get(ch)
            status, err = s(user_id, text) if s else ("failed", "unknown channel")
        store.notification_update(user_id, kind, dedup_key[:80], ch, status, err)
        out.append({"kind": kind, "channel": ch, "status": status, "error": err})
    return out


def on_lifecycle(store, user_id: int, changes: list[tuple[str, str]], senders=None) -> list[dict]:
    """Invalidation/completion messages built from the stored canonical record (no recomputation)."""
    out = []
    for key, state in changes:
        if state not in ("qualified", "invalidated", "completed", "expired"):
            continue
        rec = store.get_signal(key, user_id)
        if not rec:
            continue
        # lifecycle EVENT about the ORIGINAL signal (A-005): the original record is quoted, never restated as new
        txt = (f"AMW lifecycle event - signal {state.upper()}: {rec['ticker']} {rec['setup']} {rec['direction']} "
               f"({rec['timeframe']}). Original signal bar {rec['signal_time']}, originally recorded as "
               f"'{rec['status']}' on {str(rec['first_seen'])[:16]} UTC. Reason: {rec.get('state_reason') or '-'}. "
               f"Ref {key[:8]}. Analysis only.")
        out += notify(store, user_id, f"signal_{state}", f"{key}:{state}", txt, senders)
    return out


def daily_summary_text(svc, user_id: int, day: str) -> str | None:
    hist = [h for h in svc.store.signal_history(user_id, limit=500) if str(h["first_seen"])[:10] == day]
    from .forward import stats
    fs = stats(svc.store, user_id)
    if not hist and not fs:
        return None
    q = [h for h in hist if h["status"] == "qualified"]
    lines = [f"AMW daily summary {day} (analysis only)", f"New stored signals: {len(hist)} ({len(q)} qualified)"]
    lines += [f"- {h['ticker']} {h['setup']} {h['direction']} {h['timeframe']} score {h['score']:.0f}" for h in q[:10]]
    for s in fs:
        lines.append(f"Forward tests {s['strategy']} v{s['version']}: closed {s['closed']}, open {s['open']}, "
                     f"avg R {s['avg_r'] if s['avg_r'] is not None else '-'} [{s['evidence']}]")
    return "\n".join(lines)


def backtest_done(store, user_id: int, res: dict, label: str, senders=None) -> list[dict]:
    """'Backtest finished' notification with the engine's own metrics (dedup per strategy/version/tf/run time)."""
    a = res.get("all") or {}
    key = f"bt:{label}:{res.get('version')}:{res.get('timeframe')}:{utcnow().strftime('%Y%m%d%H%M%S')}"
    txt = (f"AMW backtest finished: {label} v{res.get('version')} {res.get('timeframe')} - trades {a.get('trades', 0)}, "
           f"win rate {a.get('win_rate', '-')}, expectancy R {a.get('expectancy_r', a.get('avg_r', '-'))} "
           f"[{a.get('evidence', '')}]. Simulation, in-sample unless stated.")
    return notify(store, user_id, "backtest_done", key, txt, senders)

