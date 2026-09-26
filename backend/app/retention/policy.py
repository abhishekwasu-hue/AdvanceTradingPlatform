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
    "users", "tenants", "market_holidays",
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

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "login_events_days": self.login_events_days,
            "alert_deliveries_days": self.alert_deliveries_days, "notifications_days": self.notifications_days,
            "sessions_days": self.sessions_days, "password_resets_days": self.password_resets_days,
            "invites_days": self.invites_days, "batch_size": self.batch_size, "never_deleted": list(NEVER_DELETED),
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
    )
