"""Phase D3: the written retention policy, as code.

Two regimes apply to this platform (docs/OPERATIONS.md section 2.3):

* **Regulatory trading records** - orders, order events, trades, signals, strategy versions and
  the hash-chained audit trail - must be kept for at least five years (SEBI). Nothing in this
  module ever touches those tables; they are listed in `NEVER_DELETED` so a reviewer can check
  the claim, and `tests/test_retention.py` asserts it.
* **Operational and personal data** - login attempts (IP, user agent), delivered alert rows,
  in-app notifications, expired/revoked sessions, spent password-reset and invite tokens - has
  no regulatory reason to live longer than it is useful and, under the DPDP Act, should not.
  Each has a bounded retention here, configurable per deployment through environment variables,
  with a floor of `MIN_DAYS` so a typo cannot wipe recent history.
"""
import os
from dataclasses import dataclass

MIN_DAYS = 7

NEVER_DELETED = (
    "audit_logs", "orders", "order_events", "trades", "signal_history", "custom_strategies", "strategy_versions",
    "risk_events",
    "users", "tenants", "market_holidays",
    # P0.8-D: every LLM input/output and every accepted acknowledgement/consent (5+ years, like the audit trail).
    "llm_calls", "ai_acknowledgements",
    # H-C2: the Copilot agent's audit trail (runs and tool calls), kept like llm_calls.
    "agent_runs", "agent_steps",
)


def _days(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(MIN_DAYS, value)


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool
    login_events_days: int
    alert_deliveries_days: int
    notifications_days: int
    sessions_days: int          # after expiry or revocation
    password_resets_days: int   # after expiry or use
    invites_days: int           # after expiry or acceptance
    batch_size: int
    chain_snapshots_days: int = 400   # Phase W: recorded option-chain quotes (reference data, not personal)
    market_snapshots_days: int = 90   # Phase AR: the Copilot's market memory (reference data, not personal)
    news_feed_days: int = 365         # Phase BB: unverified FEED rows in news_events (MANUAL entries are never deleted here)
    ai_candidates_days: int = 30      # P0.8: expired, never adopted strategist/interview candidates (adopted/deployed rows are kept)
    # H-C1 e: days after which an llm_calls row's text is scrubbed (the row, its hashes, model, cost stay). 0 = keep the
    # text (the default: removing it is the operator's data-retention decision, OPEN_QUESTIONS H-6).
    llm_text_days: int = 0

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "login_events_days": self.login_events_days,
            "alert_deliveries_days": self.alert_deliveries_days, "notifications_days": self.notifications_days,
            "sessions_days": self.sessions_days, "password_resets_days": self.password_resets_days,
            "invites_days": self.invites_days, "batch_size": self.batch_size, "chain_snapshots_days": self.chain_snapshots_days,
            "market_snapshots_days": self.market_snapshots_days, "news_feed_days": self.news_feed_days, "ai_candidates_days": self.ai_candidates_days,
            "llm_text_days": self.llm_text_days,
            "never_deleted": list(NEVER_DELETED),
        }


def load_policy() -> RetentionPolicy:
    return RetentionPolicy(
        enabled=os.environ.get("RETENTION_ENABLED", "true").lower() in ("1", "true", "yes"),
        login_events_days=_days("RETENTION_LOGIN_EVENTS_DAYS", 365),
        alert_deliveries_days=_days("RETENTION_ALERT_DELIVERIES_DAYS", 90),
        notifications_days=_days("RETENTION_NOTIFICATIONS_DAYS", 180),
        sessions_days=_days("RETENTION_SESSIONS_DAYS", 30),
        password_resets_days=_days("RETENTION_PASSWORD_RESETS_DAYS", 7),
        invites_days=_days("RETENTION_INVITES_DAYS", 30),
        batch_size=max(100, int(os.environ.get("RETENTION_BATCH_SIZE", "5000"))),
        chain_snapshots_days=_days("RETENTION_CHAIN_SNAPSHOTS_DAYS", 400),
        market_snapshots_days=_days("RETENTION_MARKET_SNAPSHOTS_DAYS", 90),
        news_feed_days=_days("RETENTION_NEWS_FEED_DAYS", 365),
        ai_candidates_days=_days("RETENTION_AI_CANDIDATES_DAYS", 30),
        llm_text_days=_llm_text_days(),
    )


def _llm_text_days() -> int:
    """0 (keep) unless set; a set value is floored at MIN_DAYS like the others."""
    try:
        value = int(os.environ.get("RETENTION_LLM_TEXT_DAYS", "0") or 0)
    except ValueError:
        return 0
    return 0 if value <= 0 else max(MIN_DAYS, value)
