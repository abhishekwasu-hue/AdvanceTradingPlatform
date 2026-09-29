"""Phase Y: the AI scanner endpoints (V4.2).

* `POST /api/scanner/ai/plan` - plain language -> a validated `ScanPlan` the page loads into
  its filters; nothing runs until the user presses Run on the ordinary `/api/scanner/run`.
* `POST /api/scanner/ai/read` - the request and result of a scan -> a ranked, explained read
  with the regime per symbol and a fixed disclaimer. Analysis only; no order path exists here.

Both need a logged-in trader (the tenant's provider and metering), sit behind the `ai_copilot`
operator flag, and fall back to the deterministic parser/read when no external provider is
configured or the plan lacks AI features (`ai_settings.provider_for`).
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_trader
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.scanner import ai as scanner_ai
from app.scanner.models import ScannerRequest, ScannerResult

router = APIRouter(prefix="/api/scanner/ai", tags=["scanner"])


class PlanBody(BaseModel):
    text: str = Field(min_length=5, max_length=2000)
    language: str = Field(default="en", min_length=2, max_length=5, pattern=r"^[A-Za-z-]+$")


class ReadBody(BaseModel):
    request: ScannerRequest
    result: ScannerResult
    language: str = Field(default="en", min_length=2, max_length=5, pattern=r"^[A-Za-z-]+$")


@router.post("/plan", response_model=scanner_ai.ScanPlan)
async def plan(body: PlanBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> scanner_ai.ScanPlan:
    await require_flag(session, "ai_copilot", user.tenant_id)
    tenant: Optional[Tenant] = await session.get(Tenant, user.tenant_id)
    return await scanner_ai.plan_scan(session, tenant, user, body.text, language=body.language)


@router.post("/read", response_model=scanner_ai.ScanRead)
async def read(body: ReadBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> scanner_ai.ScanRead:
    await require_flag(session, "ai_copilot", user.tenant_id)
    tenant: Optional[Tenant] = await session.get(Tenant, user.tenant_id)
    return await scanner_ai.read_scan(session, tenant, user, body.request, body.result, language=body.language)
