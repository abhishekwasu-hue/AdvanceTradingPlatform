"""Phase K2: the strategy marketplace (master prompt V3.9-3.10, V3.14 rule 8).

Creator flow: create (from one of the tenant's own strategy versions, with an optional saved
backtest run as the documented performance) -> validate -> submit for review -> operator
publishes or rejects -> published listings are discoverable by every tenant on a plan with
marketplace access. Subscriber flow: discover -> read description, methodology, documented
performance (methodology and period always shown, never a promise) -> subscribe, which copies
the frozen strategy config into the subscriber's own custom strategies so it runs through the
same backtest -> paper -> live pipeline as anything they wrote themselves.

The creator's later edits never reach subscribers: a listing freezes one version's config.
"""
import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.custom_strategies import versioning
from app.strategy_engine.declarative import CustomStrategyConfig
from app.db.models import (
    BacktestRunRecord, CustomStrategyRecord, MarketplaceListingRecord, MarketplaceSubscriptionRecord, StrategyVersionRecord, User,
)

DISCLAIMER = ("Documented performance is a backtest over the stated period with modelled costs; it is not a promise of future "
              "results and not investment advice. Backtest and paper-trade the strategy on your own account before going live.")

STATUSES = ("DRAFT", "PENDING_REVIEW", "PUBLISHED", "REJECTED", "UNLISTED")


class MarketplaceError(ValueError):
    pass


def _performance_from_run(run: Optional[BacktestRunRecord]) -> Optional[dict]:
    if run is None:
        return None
    metrics = json.loads(run.metrics_json or "{}")
    return {
        "backtest_run_id": run.id, "symbol": run.symbol, "base_timeframe": run.base_timeframe, "bars": run.bars,
        "data_from": run.data_from.isoformat() if run.data_from else None, "data_to": run.data_to.isoformat() if run.data_to else None,
        "data_source": run.data_source, "engine_version": run.engine_version,
        "total_trades": metrics.get("total_trades"), "win_rate": metrics.get("win_rate"), "net_pnl": metrics.get("net_pnl"),
        "profit_factor": metrics.get("profit_factor"), "max_drawdown": metrics.get("max_drawdown"), "expectancy": metrics.get("expectancy"),
        "exit_rules": json.loads(run.exit_rules) if run.exit_rules else None,
    }


async def create_listing(session: AsyncSession, user: User, *, custom_strategy_id: int, title: str, description: str,
                         methodology: Optional[str], backtest_run_id: Optional[int], version_number: Optional[int] = None) -> MarketplaceListingRecord:
    strategy = await session.get(CustomStrategyRecord, custom_strategy_id)
    if strategy is None or strategy.tenant_id != user.tenant_id:
        raise MarketplaceError("No such strategy in your organisation")
    await _ai_origin_allowed(session, strategy)
    version = None
    if version_number is not None:
        version = await session.scalar(select(StrategyVersionRecord).where(
            StrategyVersionRecord.custom_strategy_id == strategy.id, StrategyVersionRecord.version_number == version_number))
        if version is None:
            raise MarketplaceError(f"Strategy has no version {version_number}")
    elif strategy.live_version_id is not None:
        version = await session.get(StrategyVersionRecord, strategy.live_version_id)
    config_json = version.config_json if version is not None else strategy.config_json
    run = None
    if backtest_run_id is not None:
        run = await session.get(BacktestRunRecord, backtest_run_id)
        if run is None or run.tenant_id != user.tenant_id:
            raise MarketplaceError("No such backtest run in your organisation")
    listing = MarketplaceListingRecord(
        tenant_id=user.tenant_id, created_by=user.id, custom_strategy_id=strategy.id,
        version_number=version.version_number if version is not None else 1, config_json=config_json,
        title=title.strip(), description=description.strip(), methodology=(methodology or "").strip() or None,
        backtest_run_id=run.id if run else None, performance_json=json.dumps(_performance_from_run(run)) if run else None, status="DRAFT",
    )
    session.add(listing)
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_listing_created", f"{listing.title} (strategy {strategy.id} v{listing.version_number})")
    await session.commit()
    await session.refresh(listing)
    return listing


async def _ai_origin_allowed(session: AsyncSession, strategy: Optional[CustomStrategyRecord]) -> None:
    """P0.8-D: a strategy an AI drafted or the strategist proposed is not sold on the marketplace until the operator turns
    `marketplace_ai_listings` on (SEBI Research Analyst gating); the trader can still run it in their own organisation."""
    from app.platform.controls import flag_enabled
    if strategy is not None and str(strategy.origin or "user").lower().startswith("ai") and not await flag_enabled(session, "marketplace_ai_listings", strategy.tenant_id):
        raise MarketplaceError("AI-originated strategies cannot be listed on the marketplace yet (platform flag marketplace_ai_listings is off pending SEBI research-analyst gating)")


async def submit(session: AsyncSession, user: User, listing: MarketplaceListingRecord) -> MarketplaceListingRecord:
    if listing.status not in ("DRAFT", "REJECTED", "UNLISTED"):
        raise MarketplaceError(f"A {listing.status} listing cannot be submitted")
    await _ai_origin_allowed(session, await session.get(CustomStrategyRecord, listing.custom_strategy_id))
    if not listing.performance_json:
        raise MarketplaceError("Attach a saved backtest run as documented performance before submitting (V3.14 rule 8)")
    if len(listing.description) < 40:
        raise MarketplaceError("Describe the strategy in at least 40 characters - subscribers decide from this text")
    listing.status = "PENDING_REVIEW"
    listing.review_note = None
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_listing_submitted", listing.title)
    await session.commit()
    await session.refresh(listing)
    return listing


async def review(session: AsyncSession, admin: User, listing: MarketplaceListingRecord, *, publish: bool, note: Optional[str]) -> MarketplaceListingRecord:
    if listing.status != "PENDING_REVIEW" and publish:
        raise MarketplaceError("Only a listing pending review can be published")
    listing.status = "PUBLISHED" if publish else "REJECTED"
    listing.review_note = (note or "")[:500] or None
    listing.published_at = datetime.now(timezone.utc) if publish else listing.published_at
    await write_audit_log(session, listing.tenant_id, admin.id, "marketplace_listing_reviewed", f"{listing.title}: {listing.status} {note or ''}".strip())
    await session.commit()
    await session.refresh(listing)
    return listing


async def unlist(session: AsyncSession, user: User, listing: MarketplaceListingRecord) -> MarketplaceListingRecord:
    listing.status = "UNLISTED"
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_listing_unlisted", listing.title)
    await session.commit()
    await session.refresh(listing)
    return listing


def check_subscribable(listing: MarketplaceListingRecord, user: User) -> None:
    if listing.status != "PUBLISHED":
        raise MarketplaceError("This listing is not published")
    if listing.tenant_id == user.tenant_id:
        raise MarketplaceError("This is your own listing")


async def existing_subscription(session: AsyncSession, listing_id: int, tenant_id: int) -> Optional[MarketplaceSubscriptionRecord]:
    return await session.scalar(select(MarketplaceSubscriptionRecord).where(
        MarketplaceSubscriptionRecord.listing_id == listing_id, MarketplaceSubscriptionRecord.tenant_id == tenant_id))


async def subscribe(session: AsyncSession, user: User, listing: MarketplaceListingRecord) -> MarketplaceSubscriptionRecord:
    """A free listing: copy now. A paid one goes through `app/marketplace/billing.py::purchase`
    (Phase X), which calls `activate` once the charge is paid."""
    check_subscribable(listing, user)
    if (listing.price or 0) > 0:
        raise MarketplaceError("This listing is paid - purchase it first")
    existing = await existing_subscription(session, listing.id, user.tenant_id)
    if existing is not None and existing.status == "ACTIVE":
        return existing
    return await activate(session, user, listing, existing)


async def activate(session: AsyncSession, user: User, listing: MarketplaceListingRecord,
                   existing: Optional[MarketplaceSubscriptionRecord]) -> MarketplaceSubscriptionRecord:
    """Copies the frozen config into the subscriber's strategies and marks the subscription ACTIVE."""
    config = CustomStrategyConfig.model_validate_json(listing.config_json)
    config = config.model_copy(update={"name": f"{config.name} (marketplace #{listing.id})"[:200]})
    copy = CustomStrategyRecord(tenant_id=user.tenant_id, user_id=user.id, name=config.name, config_json=config.model_dump_json())
    session.add(copy)
    await session.flush()
    await versioning.create_version(session, copy, config, user, source=f"marketplace:{listing.id}")
    if existing is None:
        existing = MarketplaceSubscriptionRecord(listing_id=listing.id, tenant_id=user.tenant_id, user_id=user.id, custom_strategy_id=copy.id, status="ACTIVE")
        session.add(existing)
    else:
        existing.status, existing.custom_strategy_id = "ACTIVE", copy.id
    listing.subscriber_count = (listing.subscriber_count or 0) + 1
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_subscribed", f"listing {listing.id} -> strategy {copy.id}")
    await session.commit()
    await session.refresh(existing)
    return existing


async def unsubscribe(session: AsyncSession, user: User, listing: MarketplaceListingRecord) -> None:
    existing = await session.scalar(select(MarketplaceSubscriptionRecord).where(
        MarketplaceSubscriptionRecord.listing_id == listing.id, MarketplaceSubscriptionRecord.tenant_id == user.tenant_id))
    if existing is None or existing.status != "ACTIVE":
        return
    existing.status = "CANCELLED"
    listing.subscriber_count = max(0, (listing.subscriber_count or 0) - 1)
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_unsubscribed", f"listing {listing.id} (your copy is kept)")
    await session.commit()


async def published(session: AsyncSession) -> List[MarketplaceListingRecord]:
    return list(await session.scalars(select(MarketplaceListingRecord).where(MarketplaceListingRecord.status == "PUBLISHED")
                                      .order_by(MarketplaceListingRecord.subscriber_count.desc(), MarketplaceListingRecord.published_at.desc())))


def as_dict(listing: MarketplaceListingRecord, *, include_config: bool = False, mine: bool = False) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    out = {
        "id": listing.id, "title": listing.title, "description": listing.description, "methodology": listing.methodology,
        "status": listing.status, "review_note": listing.review_note if mine else None, "version_number": listing.version_number,
        "subscriber_count": listing.subscriber_count, "published_at": iso(listing.published_at), "created_at": iso(listing.created_at),
        "performance": json.loads(listing.performance_json) if listing.performance_json else None, "disclaimer": DISCLAIMER,
        "creator_tenant_id": listing.tenant_id if mine else None, "custom_strategy_id": listing.custom_strategy_id if mine else None,
        # Phase X: the one-time price; the fee split is the creator's business.
        "price": float(listing.price or 0.0), "currency": listing.currency or "INR",
        "platform_fee_pct": listing.platform_fee_pct if mine else None,
        "creator_net_per_sale": round(float(listing.price or 0.0) * (1 - (listing.platform_fee_pct or 0.0) / 100.0), 2)
        if mine and listing.price else None,
    }
    if include_config:
        out["config"] = json.loads(listing.config_json)
    return out
