"""Phase N3: the platform's own outbound mail (account emails), distinct from tenant alert
channels. Configured by PLATFORM_SMTP_*; when unconfigured, `send` returns False and the caller
logs the link instead - the product keeps working in development without a mail server.

`_deliver` is the single seam tests replace to capture messages."""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core import config

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.PLATFORM_SMTP_HOST and config.PLATFORM_SMTP_FROM)


def _deliver(message: EmailMessage) -> None:
    with smtplib.SMTP(config.PLATFORM_SMTP_HOST, config.PLATFORM_SMTP_PORT, timeout=15) as smtp:
        if config.PLATFORM_SMTP_STARTTLS:
            smtp.starttls()
        if config.PLATFORM_SMTP_USERNAME:
            smtp.login(config.PLATFORM_SMTP_USERNAME, config.PLATFORM_SMTP_PASSWORD)
        smtp.send_message(message)


async def send(to_address: str, subject: str, body: str) -> bool:
    """Best effort: True when handed to the SMTP server, False when unconfigured or failed."""
    if not configured():
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.PLATFORM_SMTP_FROM
    message["To"] = to_address
    message.set_content(body)
    try:
        await asyncio.to_thread(_deliver, message)
        return True
    except Exception as exc:  # noqa: BLE001 - mail must never break the request
        logger.warning("Platform mail to %s failed: %s", to_address, exc)
        return False
