"""S10 seam gate (flag `alerts_whatsapp_dlt`): the WhatsApp channel and SMS channels that use the India DLT fields
work only while the flag is on for the organisation, and WhatsApp only on a paid plan. Checked when a channel is saved,
when it is tested, and again on every delivery - so turning the flag off, or a plan downgrade after the channel was
created, stops the messages (the delivery row is SUPPRESSED with the reason; nothing is sent)."""
from typing import Any, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertChannelType

FLAG = "alerts_whatsapp_dlt"
REASON_CODE = "feature_off"


def gated(kind: str, config: Any) -> bool:
    """Does this channel need the gate (WhatsApp, or SMS with any DLT field set)?"""
    if kind == AlertChannelType.WHATSAPP.value:
        return True
    return kind == AlertChannelType.SMS.value and bool(getattr(config, "uses_dlt", lambda: False)())


async def blocked(session: AsyncSession, tenant_id: int, kind: str, config: Any) -> Optional[Tuple[int, str]]:
    """(HTTP status, reason) when this channel may not be used now; None when it may (or is not gated)."""
    if not gated(kind, config):
        return None
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, tenant_id):
        return 403, "WhatsApp and SMS DLT settings are not turned on for this organisation"
    if kind == AlertChannelType.WHATSAPP.value:
        from app.plans.limits import load_tenant
        from app.plans.registry import get_plan
        plan = get_plan((await load_tenant(session, tenant_id)).plan)
        if not plan.price_monthly:
            return 402, f"WhatsApp alerts need a paid plan; the {plan.name} plan does not include them"
    return None


__all__ = ["FLAG", "REASON_CODE", "gated", "blocked"]
