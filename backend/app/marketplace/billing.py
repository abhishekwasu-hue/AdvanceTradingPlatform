"""Phase X (master prompt V3.9-3.10): revenue share for paid marketplace listings.

A creator prices a listing (one-time, INR; 0 keeps it free). Buying it raises a
`MarketplaceChargeRecord` split by the platform fee frozen on the listing at publish time; the
buyer's subscription stays PENDING_PAYMENT and no copy is made until the charge is PAID -
either by the gateway's `payment_link.paid` webhook (Razorpay Payment Links, through the
billing provider seam) or by the operator confirming an out-of-band payment under the manual
provider. Each PAID charge is the creator's earning; the creator requests a payout of what is
available once it reaches the minimum, and the operator settles it by bank/UPI transfer and
records the reference (or rejects, which releases the earnings). The destination the creator
types is stored encrypted under the tenant's key; only a hint stays in clear.

Money never moves inside the platform: charges and payouts are ledgers of what was paid to
whom, with references to the real transfers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.billing.razorpay import payment_entity, payment_link_entity
from app.billing.service import provider as billing_provider
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import MarketplaceChargeRecord, MarketplaceListingRecord, MarketplacePayoutRecord, MarketplaceSubscriptionRecord, Tenant, User
from app.marketplace import service
from app.marketplace.service import MarketplaceError
from app.notifications.service import notify
from app.platform.controls import marketplace_terms
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_PAYOUT_DESTINATION

logger = logging.getLogger(__name__)

CHARGE_REF_PREFIX = "mpc-"     # the gateway reference_id: mpc-<charge id>


def _now() -> datetime:
    return datetime.now(timezone.utc)


def split(amount: float, fee_pct: float) -> tuple[float, float]:
    fee = round(amount * fee_pct / 100.0, 2)
    return fee, round(amount - fee, 2)


def charge_dict(c: MarketplaceChargeRecord, *, listing_title: Optional[str] = None, for_creator: bool = False) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    out = {
        "id": c.id, "listing_id": c.listing_id, "listing_title": listing_title, "amount": c.amount, "currency": c.currency,
        "status": c.status, "provider": c.provider, "checkout_url": c.checkout_url if not for_creator else None,
        "payment_ref": c.payment_ref, "paid_at": iso(c.paid_at), "created_at": iso(c.created_at),
        "platform_fee_pct": c.platform_fee_pct, "platform_fee": c.platform_fee, "creator_net": c.creator_net,
        "payout_id": c.payout_id,
    }
    if for_creator:
        out["buyer_tenant_id"] = c.tenant_id     # a number, never the buyer's name or email
    return out


def payout_dict(p: MarketplacePayoutRecord) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": p.id, "tenant_id": p.tenant_id, "amount": p.amount, "currency": p.currency, "status": p.status,
            "destination_hint": p.destination_hint, "reference": p.reference, "note": p.note,
            "created_at": iso(p.created_at), "settled_at": iso(p.settled_at)}


# --- pricing ----------------------------------------------------------------------------------

async def validate_price(session: AsyncSession, price: float) -> float:
    terms = await marketplace_terms(session)
    if price < 0:
        raise MarketplaceError("Price cannot be negative")
    if price > terms["max_listing_price"]:
        raise MarketplaceError(f"Price exceeds the platform's cap of {terms['max_listing_price']:g}")
    return round(float(price), 2)


async def freeze_fee(session: AsyncSession, listing: MarketplaceListingRecord) -> None:
    """At publish: the fee the listing will carry for every sale, whatever the terms become later."""
    if listing.platform_fee_pct is None:
        listing.platform_fee_pct = (await marketplace_terms(session))["platform_fee_pct"]


# --- purchases --------------------------------------------------------------------------------

async def purchase(session: AsyncSession, user: User, listing: MarketplaceListingRecord) -> tuple[Optional[MarketplaceChargeRecord], MarketplaceSubscriptionRecord]:
    """Free listing: the copy is made now (charge None). Paid: an OPEN charge and a
    PENDING_PAYMENT subscription; repeated calls return the same open charge (same checkout link)."""
    service.check_subscribable(listing, user)
    existing = await service.existing_subscription(session, listing.id, user.tenant_id)
    if existing is not None and existing.status == "ACTIVE":
        return None, existing
    price = float(listing.price or 0.0)
    if price <= 0:
        return None, await service.activate(session, user, listing, existing)
    open_charge = await session.scalar(select(MarketplaceChargeRecord).where(
        MarketplaceChargeRecord.listing_id == listing.id, MarketplaceChargeRecord.tenant_id == user.tenant_id, MarketplaceChargeRecord.status == "OPEN"))
    if open_charge is not None and existing is not None:
        return open_charge, existing
    if existing is None:
        existing = MarketplaceSubscriptionRecord(listing_id=listing.id, tenant_id=user.tenant_id, user_id=user.id, status="PENDING_PAYMENT")
        session.add(existing)
    else:
        existing.status, existing.user_id = "PENDING_PAYMENT", user.id
    await session.flush()
    fee_pct = listing.platform_fee_pct if listing.platform_fee_pct is not None else (await marketplace_terms(session))["platform_fee_pct"]
    fee, net = split(price, fee_pct)
    charge = MarketplaceChargeRecord(listing_id=listing.id, creator_tenant_id=listing.tenant_id, tenant_id=user.tenant_id, user_id=user.id,
                                     subscription_id=existing.id, amount=price, currency=listing.currency or "INR", platform_fee_pct=fee_pct,
                                     platform_fee=fee, creator_net=net, status="OPEN", provider=billing_provider().name)
    session.add(charge)
    await session.flush()
    reference = f"{CHARGE_REF_PREFIX}{charge.id}"
    link = await billing_provider().create_payment_link(
        amount=price, currency=charge.currency, description=f"Strategy '{listing.title}' (marketplace listing #{listing.id})",
        reference_id=reference, notes={"charge_id": str(charge.id), "listing_id": str(listing.id), "tenant_id": str(user.tenant_id)},
        customer_email=getattr(user, "email", None))
    charge.provider_ref, charge.checkout_url = link.ref, link.url
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_charge_opened",
                          f"listing {listing.id} '{listing.title}' {price:g} {charge.currency} (fee {fee_pct:g}%) via {charge.provider}")
    await session.commit()
    await session.refresh(charge)
    await session.refresh(existing)
    return charge, existing


async def settle_charge(session: AsyncSession, charge: MarketplaceChargeRecord, *, payment_ref: Optional[str], actor_user_id: Optional[int],
                        source: str) -> MarketplaceChargeRecord:
    """The charge was paid: book it, activate the subscription (make the copy), tell both sides.
    Idempotent - a redelivered webhook or a second click changes nothing."""
    if charge.status == "PAID":
        return charge
    if charge.status == "VOID":
        raise MarketplaceError("This charge was voided; ask the buyer to purchase again")
    listing = await session.get(MarketplaceListingRecord, charge.listing_id)
    buyer = await session.get(User, charge.user_id) if charge.user_id else None
    if listing is None or buyer is None:
        raise MarketplaceError("The listing or the buyer no longer exists")
    charge.status, charge.payment_ref, charge.paid_at = "PAID", (payment_ref or None), _now()
    subscription = await session.get(MarketplaceSubscriptionRecord, charge.subscription_id) if charge.subscription_id else None
    if subscription is None or subscription.status != "ACTIVE":
        subscription = await service.activate(session, buyer, listing, subscription)   # commits
        charge.subscription_id = subscription.id
    await write_audit_log(session, charge.tenant_id, actor_user_id, "marketplace_charge_paid",
                          f"charge {charge.id} listing {listing.id} {charge.amount:g} {charge.currency} ref={payment_ref or '-'} ({source})")
    await notify(session, charge.creator_tenant_id, NotificationType.MARKETPLACE, title=f"Marketplace sale: {listing.title}",
                 message=f"A subscriber bought '{listing.title}' for {charge.amount:g} {charge.currency}; your share {charge.creator_net:g} "
                         f"(platform fee {charge.platform_fee_pct:g}%) is available for payout.", severity=NotificationSeverity.INFO)
    await notify(session, charge.tenant_id, NotificationType.MARKETPLACE, title=f"Purchase confirmed: {listing.title}",
                 message="The strategy is now under your custom strategies. Backtest and paper-trade it before going LIVE.",
                 severity=NotificationSeverity.INFO, user_id=charge.user_id)
    await session.commit()
    await session.refresh(charge)
    return charge


async def void_charge(session: AsyncSession, charge: MarketplaceChargeRecord, *, actor_user_id: Optional[int], reason: str) -> MarketplaceChargeRecord:
    if charge.status != "OPEN":
        raise MarketplaceError(f"Only an OPEN charge can be voided (this one is {charge.status})")
    charge.status = "VOID"
    subscription = await session.get(MarketplaceSubscriptionRecord, charge.subscription_id) if charge.subscription_id else None
    if subscription is not None and subscription.status == "PENDING_PAYMENT":
        subscription.status = "CANCELLED"
    await write_audit_log(session, charge.tenant_id, actor_user_id, "marketplace_charge_voided", f"charge {charge.id}: {reason}"[:300])
    await session.commit()
    await session.refresh(charge)
    return charge


async def handle_payment_link_event(session: AsyncSession, event_type: str, payload: dict) -> str:
    """`payment_link.paid` from the gateway webhook: match the charge by our reference id (or the
    notes we attached), settle it. Other payment_link events are noted only."""
    link = payment_link_entity(payload)
    reference = str(link.get("reference_id") or "")
    notes = link.get("notes") or {}
    charge_id: Optional[int] = None
    if reference.startswith(CHARGE_REF_PREFIX) and reference[len(CHARGE_REF_PREFIX):].isdigit():
        charge_id = int(reference[len(CHARGE_REF_PREFIX):])
    elif str(notes.get("charge_id") or "").isdigit():
        charge_id = int(notes["charge_id"])
    if charge_id is None:
        return f"ignored ({event_type}): no marketplace charge reference"
    charge = await session.get(MarketplaceChargeRecord, charge_id)
    if charge is None:
        return f"ignored ({event_type}): charge {charge_id} unknown"
    if event_type != "payment_link.paid":
        return f"noted ({event_type}) for charge {charge_id}"
    if link.get("id") and charge.provider_ref and link["id"] != charge.provider_ref:
        return f"ignored: payment link {link.get('id')} is not charge {charge_id}'s ({charge.provider_ref})"
    payment = payment_entity(payload)
    paid = (payment.get("amount") or link.get("amount_paid") or 0) / 100.0
    if paid + 0.005 < charge.amount:
        return f"ignored: paid {paid:g} < charge {charge.amount:g}"
    await settle_charge(session, charge, payment_ref=payment.get("id") or link.get("id"), actor_user_id=None, source="razorpay webhook")
    return f"charge {charge_id} settled ({paid:g} {charge.currency})"


# --- earnings and payouts ---------------------------------------------------------------------

async def earnings(session: AsyncSession, tenant_id: int) -> Dict:
    rows = list(await session.scalars(select(MarketplaceChargeRecord).where(
        MarketplaceChargeRecord.creator_tenant_id == tenant_id, MarketplaceChargeRecord.status == "PAID")
        .order_by(MarketplaceChargeRecord.paid_at.desc(), MarketplaceChargeRecord.id.desc())))
    payouts = list(await session.scalars(select(MarketplacePayoutRecord).where(MarketplacePayoutRecord.tenant_id == tenant_id)
                                         .order_by(MarketplacePayoutRecord.id.desc())))
    requested_ids = {p.id for p in payouts if p.status == "REQUESTED"}
    paid_ids = {p.id for p in payouts if p.status == "PAID"}
    available = sum(c.creator_net for c in rows if c.payout_id is None)
    pending = sum(c.creator_net for c in rows if c.payout_id in requested_ids)
    paid_out = sum(c.creator_net for c in rows if c.payout_id in paid_ids)
    titles = {l.id: l.title for l in await session.scalars(select(MarketplaceListingRecord).where(MarketplaceListingRecord.tenant_id == tenant_id))}
    terms = await marketplace_terms(session)
    return {
        "currency": "INR", "sales": len(rows), "gross": round(sum(c.amount for c in rows), 2), "platform_fees": round(sum(c.platform_fee for c in rows), 2),
        "net": round(sum(c.creator_net for c in rows), 2), "available": round(available, 2), "pending_payout": round(pending, 2),
        "paid_out": round(paid_out, 2), "min_payout": terms["min_payout"], "platform_fee_pct": terms["platform_fee_pct"],
        "can_request_payout": available >= terms["min_payout"] and available > 0 and not requested_ids,
        "sales_rows": [charge_dict(c, listing_title=titles.get(c.listing_id), for_creator=True) for c in rows[:200]],
        "payouts": [payout_dict(p) for p in payouts[:50]],
    }


async def request_payout(session: AsyncSession, user: User, destination: str) -> MarketplacePayoutRecord:
    destination = (destination or "").strip()
    if len(destination) < 6:
        raise MarketplaceError("Give the UPI id or bank account (IFSC + number) the payout should go to")
    terms = await marketplace_terms(session)
    open_request = await session.scalar(select(MarketplacePayoutRecord).where(
        MarketplacePayoutRecord.tenant_id == user.tenant_id, MarketplacePayoutRecord.status == "REQUESTED"))
    if open_request is not None:
        raise MarketplaceError(f"Payout #{open_request.id} is still being processed")
    rows = list(await session.scalars(select(MarketplaceChargeRecord).where(
        MarketplaceChargeRecord.creator_tenant_id == user.tenant_id, MarketplaceChargeRecord.status == "PAID", MarketplaceChargeRecord.payout_id.is_(None))))
    amount = round(sum(c.creator_net for c in rows), 2)
    if amount <= 0 or amount < terms["min_payout"]:
        raise MarketplaceError(f"Available earnings {amount:g} are below the minimum payout of {terms['min_payout']:g}")
    hint = ("…" + destination[-4:]) if len(destination) > 4 else destination
    payout = MarketplacePayoutRecord(tenant_id=user.tenant_id, requested_by=user.id, amount=amount, currency="INR", status="REQUESTED",
                                     destination_encrypted=encrypt_text(destination, user.tenant_id, PURPOSE_PAYOUT_DESTINATION), destination_hint=hint[:40])
    session.add(payout)
    await session.flush()
    for c in rows:
        c.payout_id = payout.id
    await write_audit_log(session, user.tenant_id, user.id, "marketplace_payout_requested", f"payout {payout.id}: {amount:g} INR to {hint} ({len(rows)} sales)")
    await session.commit()
    await session.refresh(payout)
    return payout


async def settle_payout(session: AsyncSession, admin: User, payout: MarketplacePayoutRecord, *, paid: bool, reference: Optional[str],
                        note: Optional[str]) -> MarketplacePayoutRecord:
    if payout.status != "REQUESTED":
        raise MarketplaceError(f"Payout is already {payout.status}")
    if paid and not (reference or "").strip():
        raise MarketplaceError("Record the transfer reference (UTR / UPI reference) when marking a payout paid")
    payout.status = "PAID" if paid else "REJECTED"
    payout.reference = (reference or "").strip()[:200] or None
    payout.note = (note or "").strip()[:500] or None
    payout.settled_by, payout.settled_at = admin.id, _now()
    if not paid:
        for c in await session.scalars(select(MarketplaceChargeRecord).where(MarketplaceChargeRecord.payout_id == payout.id)):
            c.payout_id = None     # the earnings are available again
    await write_audit_log(session, payout.tenant_id, admin.id, "marketplace_payout_settled",
                          f"payout {payout.id} {payout.status} {payout.amount:g} {payout.currency} ref={payout.reference or '-'} {payout.note or ''}".strip())
    await notify(session, payout.tenant_id, NotificationType.MARKETPLACE, title=f"Payout #{payout.id} {payout.status.lower()}",
                 message=(f"{payout.amount:g} {payout.currency} was transferred to {payout.destination_hint} (ref {payout.reference})." if paid
                          else f"The payout request was rejected: {payout.note or 'no reason given'}. The earnings are available again."),
                 severity=NotificationSeverity.INFO if paid else NotificationSeverity.WARNING)
    await session.commit()
    await session.refresh(payout)
    return payout


def payout_destination(payout: MarketplacePayoutRecord) -> str:
    """Operator only: the full destination, decrypted for the transfer."""
    return decrypt_text(payout.destination_encrypted, PURPOSE_PAYOUT_DESTINATION, payout.tenant_id)


# --- lists ------------------------------------------------------------------------------------

async def purchases(session: AsyncSession, tenant_id: int) -> List[dict]:
    rows = list(await session.scalars(select(MarketplaceChargeRecord).where(MarketplaceChargeRecord.tenant_id == tenant_id)
                                      .order_by(MarketplaceChargeRecord.id.desc()).limit(200)))
    titles = await _titles(session, {c.listing_id for c in rows})
    return [charge_dict(c, listing_title=titles.get(c.listing_id)) for c in rows]


async def charges(session: AsyncSession, *, status: Optional[str] = None, limit: int = 200) -> List[dict]:
    q = select(MarketplaceChargeRecord).order_by(MarketplaceChargeRecord.id.desc()).limit(limit)
    if status:
        q = q.where(MarketplaceChargeRecord.status == status.upper())
    rows = list(await session.scalars(q))
    titles = await _titles(session, {c.listing_id for c in rows})
    return [{**charge_dict(c, listing_title=titles.get(c.listing_id), for_creator=True), "checkout_url": c.checkout_url} for c in rows]


async def payouts(session: AsyncSession, *, status: Optional[str] = None, limit: int = 200) -> List[dict]:
    q = select(MarketplacePayoutRecord).order_by(MarketplacePayoutRecord.id.desc()).limit(limit)
    if status:
        q = q.where(MarketplacePayoutRecord.status == status.upper())
    return [payout_dict(p) for p in await session.scalars(q)]


async def platform_revenue(session: AsyncSession) -> dict:
    paid = (await session.execute(select(func.count(MarketplaceChargeRecord.id), func.coalesce(func.sum(MarketplaceChargeRecord.amount), 0.0),
                                         func.coalesce(func.sum(MarketplaceChargeRecord.platform_fee), 0.0), func.coalesce(func.sum(MarketplaceChargeRecord.creator_net), 0.0))
                                  .where(MarketplaceChargeRecord.status == "PAID"))).one()
    open_count = await session.scalar(select(func.count(MarketplaceChargeRecord.id)).where(MarketplaceChargeRecord.status == "OPEN"))
    requested = (await session.execute(select(func.count(MarketplacePayoutRecord.id), func.coalesce(func.sum(MarketplacePayoutRecord.amount), 0.0))
                                       .where(MarketplacePayoutRecord.status == "REQUESTED"))).one()
    paid_out = await session.scalar(select(func.coalesce(func.sum(MarketplacePayoutRecord.amount), 0.0)).where(MarketplacePayoutRecord.status == "PAID"))
    return {"currency": "INR", "sales": int(paid[0]), "gross": round(float(paid[1]), 2), "platform_fees": round(float(paid[2]), 2),
            "creator_net": round(float(paid[3]), 2), "open_charges": int(open_count or 0),
            "payouts_requested": int(requested[0]), "payouts_requested_amount": round(float(requested[1]), 2),
            "paid_out": round(float(paid_out or 0.0), 2), "terms": await marketplace_terms(session)}


async def _titles(session: AsyncSession, ids: set) -> Dict[int, str]:
    if not ids:
        return {}
    return {l.id: l.title for l in await session.scalars(select(MarketplaceListingRecord).where(MarketplaceListingRecord.id.in_(ids)))}
