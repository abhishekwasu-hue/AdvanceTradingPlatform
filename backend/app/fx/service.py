"""Phase P3 / section 57: multi-currency groundwork.

Every instrument has a quote currency (`ContractSpec.quote_currency`, INR for everything traded
today) and every tenant a base currency (`tenants.base_currency`, INR by default). `fx_rates`
holds operator-maintained rates (a managed feed can replace the admin endpoint later) and
`convert()` moves an amount between currencies through a direct rate, its inverse, or via INR.
Portfolio exposure reports in the tenant's base currency and says which rates it used. With only
INR instruments and INR tenants nothing changes; the seams are what section 57 asked for.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FxRateRecord, User
from app.audit.log import write_audit_log

SUPPORTED_CURRENCIES = ("INR", "USD", "USDT", "EUR", "GBP", "AED", "SGD")
PIVOT = "INR"


class FxError(ValueError):
    pass


async def load_rates(session: AsyncSession) -> Dict[Tuple[str, str], float]:
    rows = await session.scalars(select(FxRateRecord))
    return {(r.base, r.quote): float(r.rate) for r in rows}


def convert(amount: float, from_ccy: str, to_ccy: str, rates: Dict[Tuple[str, str], float]) -> float:
    """`amount` in `from_ccy` expressed in `to_ccy`. Direct, inverse, or through the INR pivot."""
    a, b = from_ccy.upper(), to_ccy.upper()
    if a == b:
        return amount
    if (a, b) in rates:
        return amount * rates[(a, b)]
    if (b, a) in rates and rates[(b, a)]:
        return amount / rates[(b, a)]
    if a != PIVOT and b != PIVOT:
        try:
            return convert(convert(amount, a, PIVOT, rates), PIVOT, b, rates)
        except FxError:
            pass
    raise FxError(f"No FX rate for {a}->{b}; a platform admin can set one under /api/admin/fx-rates")


async def set_rate(session: AsyncSession, admin: User, base: str, quote: str, rate: float, source: str = "manual") -> FxRateRecord:
    base, quote = base.upper(), quote.upper()
    if base not in SUPPORTED_CURRENCIES or quote not in SUPPORTED_CURRENCIES or base == quote:
        raise FxError(f"Currencies must be two different codes from {SUPPORTED_CURRENCIES}")
    if rate <= 0:
        raise FxError("Rate must be positive")
    row = await session.scalar(select(FxRateRecord).where(FxRateRecord.base == base, FxRateRecord.quote == quote))
    if row is None:
        row = FxRateRecord(base=base, quote=quote)
        session.add(row)
    row.rate, row.source, row.as_of, row.updated_by = rate, source[:40], datetime.now(timezone.utc), admin.id
    await write_audit_log(session, None, admin.id, "fx_rate_set", f"{base}/{quote}={rate:g} ({source})")
    await session.commit()
    await session.refresh(row)
    return row


def rate_dict(row: FxRateRecord) -> dict:
    return {"base": row.base, "quote": row.quote, "rate": float(row.rate), "source": row.source,
            "as_of": row.as_of.isoformat() if row.as_of else None}


def quote_currency(symbol: str) -> str:
    from app.instruments.registry import get_contract_spec
    spec = get_contract_spec(symbol)
    return (spec.quote_currency if spec is not None else PIVOT).upper()
