"""Validation errors that never echo what the caller sent.

Pydantic's default error text and FastAPI's default 422 body both include the rejected
`input_value` - for a bot token, an API secret or a TOTP seed that puts the secret straight back on
the screen (and into any proxy or browser log that captures the response). These helpers keep the
field location and the message and drop the input."""
from typing import Any, Dict, List

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError


def safe_validation_message(exc: Exception) -> str:
    """One line per error, `field: message`, without the rejected value. Plain ValueErrors raised by
    our own validators carry only the text we wrote, so they pass through unchanged."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors(include_input=False, include_url=False):
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "__root__")
            msg = str(err.get("msg", "invalid value"))
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "; ".join(parts) or "invalid value"
    return str(exc)


def _strip_inputs(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in err.items() if k not in ("input", "url")} for err in errors]


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Same 422 shape as FastAPI's default handler, minus each error's `input`."""
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(_strip_inputs(list(exc.errors())))})
