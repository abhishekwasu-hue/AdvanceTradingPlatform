"""Plan entitlements, enforced server-side. Plans and limits come from config/settings.yaml `plans` (owner decision Q-002).
A user's plan comes from `users.plan`, which only changes through verified billing events or an admin."""
from __future__ import annotations

from ..config import default_settings

LIMITS = ("symbols_per_scan", "concurrent_jobs", "reports_per_day", "lab_strategies", "alert_rules", "schedules",
          "min_schedule_interval_min", "report_retention_days", "report_storage_mb")


class EntitlementError(PermissionError):
    def __init__(self, limit: str, allowed, plan: str):
        super().__init__(f"Your plan '{plan}' allows {allowed} for {limit.replace('_', ' ')}. Upgrade or reduce the request.")
        self.limit, self.allowed, self.plan = limit, allowed, plan


def plan_limits(plan: str | None, cfg: dict | None = None) -> dict:
    plans = (cfg or default_settings()).get("plans") or {}
    return dict(plans.get(plan or "free") or plans.get("free") or {})


def check(store, user_id: int, limit: str, requested: int | float, cfg: dict | None = None) -> None:
    """Raise EntitlementError if `requested` (the value after the action) exceeds the plan's limit."""
    u = store.get_user(user_id) or {}
    plan = u.get("plan") or "free"
    allowed = plan_limits(plan, cfg).get(limit)
    if limit == "min_schedule_interval_min":
        if allowed is not None and requested < allowed:
            raise EntitlementError(limit, f"a minimum of {allowed} minutes", plan)
        return
    if allowed is not None and requested > allowed:
        raise EntitlementError(limit, allowed, plan)


def usage(store, svc, user_id: int) -> dict:
    return {"lab_strategies": len(store.lab_list(user_id)), "alert_rules": len(store.alert_rules(user_id)),
            "schedules": len(store.schedule_list(user_id)), "reports_today": store.reports_today(user_id),
            "running_jobs": len(svc.jobs.active(user_id))}


def commercial_status(cfg: dict | None = None) -> dict:
    """Q-002: whether plan values are approved commercial terms. Default: provisional, no prices."""
    m = (cfg or default_settings()).get("plan_meta") or {}
    status = m.get("commercial_status", "provisional")
    return {"commercial_status": status if status in ("provisional", "approved") else "provisional",
            "prices": m.get("prices"), "retention_note": m.get("retention_note"),
            "notice": None if status == "approved" else
            "Plan limits and retention periods are provisional configuration values, not approved commercial terms."}

