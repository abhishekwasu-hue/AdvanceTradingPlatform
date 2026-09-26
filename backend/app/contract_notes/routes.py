"""Phase D4: contract-note upload API.

POST /api/contract-notes            multipart `file` (CSV), optional `broker_name`, `apply` (default true)
GET  /api/contract-notes            uploads for this tenant, newest first
GET  /api/contract-notes/{id}       one upload with its parsed legs and matches

Traders and owners can upload (the same people who see the trades); viewers cannot.
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.contract_notes.parser import ContractNoteParseError
from app.contract_notes.service import DuplicateContractNote, ingest_contract_note
from app.db.models import ContractNoteLineRecord, ContractNoteRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/contract-notes", tags=["contract-notes"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _note(n: ContractNoteRecord) -> Dict:
    return {
        "id": n.id, "broker_name": n.broker_name, "filename": n.filename, "sha256": n.sha256,
        "note_date": n.note_date.isoformat() if n.note_date else None, "line_count": n.line_count,
        "matched_lines": n.matched_lines, "trades_updated": n.trades_updated, "total_charges": n.total_charges,
        "uploaded_at": n.uploaded_at.isoformat(), "uploaded_by": n.uploaded_by,
    }


@router.post("")
async def upload_contract_note(
    file: UploadFile = File(...), broker_name: str = Form(""), apply: bool = Form(True),
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> Dict:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 5 MB")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        result = await ingest_contract_note(
            session, tenant_id=user.tenant_id, user_id=user.id, content=content, filename=file.filename or "contract-note.csv",
            broker_name=broker_name, apply=apply,
        )
    except ContractNoteParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DuplicateContractNote as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="File is not UTF-8 text - export the CSV again") from exc
    return result.as_dict()


@router.get("")
async def list_contract_notes(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict]:
    rows = await session.scalars(
        select(ContractNoteRecord).where(ContractNoteRecord.tenant_id == user.tenant_id).order_by(ContractNoteRecord.id.desc()).limit(100)
    )
    return [_note(n) for n in rows]


@router.get("/{note_id}")
async def contract_note_detail(note_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict:
    note = await session.get(ContractNoteRecord, note_id)
    if note is None or note.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown contract note")
    lines = await session.scalars(
        select(ContractNoteLineRecord).where(ContractNoteLineRecord.contract_note_id == note.id).order_by(ContractNoteLineRecord.id)
    )
    return {**_note(note), "lines": [{
        "id": l.id, "trade_id": l.trade_id, "trade_date": l.trade_date.isoformat() if l.trade_date else None, "symbol": l.symbol,
        "side": l.side, "quantity": l.quantity, "price": l.price, "order_id": l.order_id, "charges": l.charges,
        "breakdown": json.loads(l.breakdown_json or "{}"), "match_method": l.match_method,
    } for l in lines]}
