"""AUD-G1.4 (owner audit 28 Oct; DPDP): JSON log lines carry no email address or phone number."""
import json
import logging

from app.core.logging_config import JsonFormatter, redact_personal


def test_emails_and_phones_are_masked_ids_and_times_are_not():
    assert redact_personal("login failed for ravi.kumar@example.com") == "login failed for r***@example.com"
    assert redact_personal("sms to +91 9876543210 and 09876543210 and 9876543210.") == "sms to [phone] and [phone] and [phone]."
    kept = "order 250128000123456 at 1730123456 qty 75 price 24512.5"
    assert redact_personal(kept) == kept


def test_the_formatter_masks_message_and_traceback():
    try:
        raise ValueError("bad user a.b@example.org")
    except ValueError:
        import sys
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "user %s phone %s", ("x.y@example.com", "9876543210"), sys.exc_info())
    out = json.loads(JsonFormatter().format(record))
    assert out["message"] == "user x***@example.com phone [phone]"
    assert "a.b@example.org" not in out["exc_info"] and "a***@example.org" in out["exc_info"]
