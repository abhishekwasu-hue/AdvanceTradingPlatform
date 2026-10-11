"""Z10-f: `/api/legal/*`.

* `GET /api/legal/documents` and `GET /api/legal/documents/{slug}` - public (no sign-in): the Privacy notice, Terms of
  use and Risk disclosure, rendered with the platform's live retention and breach-notice settings. The sign-up form
  links to them, so they must open before an account exists.
* `GET /api/legal/acceptance` - the signed-in user's acceptance of each current version (`pending` lists what to ask).
* `POST /api/legal/acceptance` - records the user's acceptance of the versions they read.

Nothing else depends on these: an unaccepted document never blocks a route (exits, positions and every other screen
keep working while the prompt is open). Behind the flag `legal_pages` (OFF by default): while it is off every route
here answers 404, exactly as before this feature existed.
"""
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import rate_limit
from app.db.models import User
from app.db.session import get_session
from app import legal
from app.legal import acceptance, documents

router = APIRouter(prefix="/api/legal", tags=["legal"])

# Module-level so tests can target it through `app.dependency_overrides`. Public pages: per IP, generous.
legal_read_rate_limit = rate_limit("legal_read", limit=120, window_seconds=60)
_CACHE = "public, max-age=300"


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: Dict[str, str] = Field(description="slug -> the version the user read, e.g. {\"terms\": \"2026-10-11\"}",
                                      min_length=1, max_length=len(documents.SLUGS))


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": legal.FLAG})


async def pages_on(session: AsyncSession = Depends(get_session)) -> None:
    """The public pages: 404 while `legal_pages` is off (for everyone and every organisation)."""
    if not await legal.enabled(session):
        raise _not_found()


async def legal_user(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    """The caller, when `legal_pages` is on for their organisation; 404 otherwise (the banner then never shows)."""
    if not await legal.enabled(session, user.tenant_id):
        raise _not_found()
    return user


def version_conflict(exc: acceptance.LegalVersionError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT,
                         detail={"code": "legal_version_changed", "slug": exc.slug, "current": exc.current, "message": str(exc)})


@router.get("/documents", dependencies=[Depends(legal_read_rate_limit), Depends(pages_on)])
async def list_documents(response: Response) -> dict:
    response.headers["Cache-Control"] = _CACHE
    return {"documents": [documents.get(s).summary() for s in documents.SLUGS]}


@router.get("/documents/{slug}", dependencies=[Depends(legal_read_rate_limit), Depends(pages_on)])
async def get_document(response: Response, slug: str = Path(pattern=r"^[a-z]{2,20}$")) -> dict:
    if slug not in documents.SLUGS:
        raise HTTPException(status_code=404, detail="No such legal document")
    doc = documents.get(slug)
    response.headers["Cache-Control"] = _CACHE
    return {**doc.summary(), "body": documents.render(doc), "open_placeholders": doc.open_placeholders()}


@router.get("/acceptance")
async def get_acceptance(user: User = Depends(legal_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await acceptance.status(session, user)


@router.post("/acceptance")
async def post_acceptance(body: AcceptBody, request: Request, user: User = Depends(legal_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await acceptance.accept(session, user, body.documents, source="prompt", request=request)
    except acceptance.LegalVersionError as exc:
        raise version_conflict(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await session.commit()
    return await acceptance.status(session, user)
