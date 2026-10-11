"""Z10-f (Phase 0 launch hardening): the legal pages and their acceptance.

* `documents`  - the Privacy notice (DPDP Act 2023), Terms of use and Risk disclosure as versioned Markdown content
                 (`content/*.md`), each headed "Draft - to be reviewed by a lawyer before launch".
* `acceptance` - who accepted which version and when (at sign-up, or when asked on the next sign-in).
* `routes`     - `/api/legal/*`: the documents are public; acceptance needs a signed-in user.

Nothing here gates trading: an unaccepted document never blocks an exit, a position or any other route.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

# Off by default: the documents are drafts with open placeholders until the company is registered and a lawyer has
# reviewed them. Off = the public routes answer 404, sign-up ignores `legal_acceptance`, nobody is asked to accept and
# the support bot leaves the legal pages out. `platform.controls.set_flag` refuses to turn it on while a document still
# has an open placeholder, unless the operator overrides explicitly.
FLAG = "legal_pages"


async def enabled(session: AsyncSession, tenant_id: Optional[int] = None) -> bool:
    """For an organisation: the flag is on, or on for that organisation. Without one (the public pages, a sign-up that
    creates a new organisation): on for everyone, or for at least one organisation (a staged rollout's invitees must be
    able to open the pages their banner links to)."""
    from app.platform.controls import feature_flags, flag_enabled
    if tenant_id is not None:
        return await flag_enabled(session, FLAG, tenant_id)
    entry = (await feature_flags(session)).get(FLAG) or {}
    return bool(entry.get("on") or entry.get("tenants"))
