"""P0.2 / S1: the CAPTCHA hook for the login endpoint.

Off unless `CAPTCHA_PROVIDER` (turnstile | hcaptcha) and `CAPTCHA_SECRET` are set *and*
`LOGIN_CAPTCHA_AFTER_FAILURES` > 0. Then, once an email has that many recent failures, the login body must
carry `captcha_token`, which is verified with the provider's siteverify endpoint. The frontend learns that a
token is needed from the 403 `captcha_required` answer (header `X-Captcha: required`).
"""
from typing import Optional

import httpx

from app.core import config

VERIFY_URLS = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://hcaptcha.com/siteverify",
}


def configured() -> bool:
    return bool(config.CAPTCHA_PROVIDER in VERIFY_URLS and config.CAPTCHA_SECRET and config.LOGIN_CAPTCHA_AFTER_FAILURES > 0)


def required(recent_failures: int) -> bool:
    return configured() and recent_failures >= config.LOGIN_CAPTCHA_AFTER_FAILURES


async def verify(token: Optional[str], remote_ip: Optional[str], client: Optional[httpx.AsyncClient] = None) -> bool:
    """True when the provider accepts the token. Any failure to reach the provider is a refusal (the user can
    try again); the secret never appears in a log or an error message."""
    if not token or not configured():
        return False
    owns = client is None
    client = client or httpx.AsyncClient(timeout=5.0)
    try:
        data = {"secret": config.CAPTCHA_SECRET, "response": token[:4096]}
        if remote_ip:
            data["remoteip"] = remote_ip
        response = await client.post(VERIFY_URLS[config.CAPTCHA_PROVIDER], data=data)
        return bool(response.status_code == 200 and response.json().get("success"))
    except Exception:  # noqa: BLE001
        return False
    finally:
        if owns:
            await client.aclose()
