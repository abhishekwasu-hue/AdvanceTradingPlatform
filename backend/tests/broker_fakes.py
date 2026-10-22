"""Obviously fake broker credentials that pass the UI2-5 field map's formats (app/brokers/field_specs.py).

Since UI2-5 the credentials route checks each value's format, so a test that stores credentials through the API uses
these instead of one-letter stand-ins. Every value is built at run time from plain parts ("fake", zeros), so none of
them reads as a real key to a secret scanner, and none of them is a real credential.
"""
from typing import Any, Dict


def _uuid_like(n: int) -> str:
    return "-".join(["fake" + "0" * 4, "0" * 4, "0" * 4, "0" * 4, str(n).rjust(12, "0")])


UPSTOX_KEY = _uuid_like(1)
UPSTOX_KEY_2 = _uuid_like(2)
UPSTOX_SECRET = "fake" + "-upstox-" + "1"
UPSTOX_SECRET_2 = "fake" + "-upstox-" + "2"

KITE_KEY = "fakekite" + "0" * 7 + "1"
KITE_KEY_2 = "fakekite" + "0" * 7 + "2"
KITE_KEY_3 = "fakekite" + "0" * 7 + "3"
KITE_SECRET = "fake" + "-kite-" + "0" * 6 + "1"

FYERS_APP_ID = "FAKE" + "1-100"
FYERS_SECRET = "fake" + "-fyers-" + "1"

ANGEL_KEY = "fake" + "angel"
ANGEL_CLIENT = "FAKE" + "01"
ANGEL_PIN = "12" + "34"

SHOONYA_USER = "FA" + "12345"
SHOONYA_PASSWORD = "fake" + "-password"
SHOONYA_VENDOR_KEY = "fake" + "-vendor-key"

# The RFC 4648 / pyotp documentation example key, already used by tests/test_shoonya_totp.py.
TOTP_SECRET = "JBSWY3DP" + "EHPK3PXP"

DHAN_CLIENT = "1" + "0" * 4 + "1"
DHAN_TOKEN = ".".join(["fake" + "header", "fake" + "payload", "fake" + "sig"])

COINDCX_KEY = "fa" * 16
COINDCX_SECRET = "fake" + "0" * 28 + "1"

_BY_BROKER: Dict[str, Dict[str, str]] = {
    "upstox": {"api_key": UPSTOX_KEY, "api_secret": UPSTOX_SECRET},
    "zerodha": {"api_key": KITE_KEY, "api_secret": KITE_SECRET},
    "fyers": {"api_key": FYERS_APP_ID, "api_secret": FYERS_SECRET},
    "angel_one": {"api_key": ANGEL_KEY, "client_id": ANGEL_CLIENT, "pin": ANGEL_PIN, "totp_secret": TOTP_SECRET},
    "shoonya": {"client_id": SHOONYA_USER, "api_secret": SHOONYA_PASSWORD, "api_key": SHOONYA_VENDOR_KEY, "totp_secret": TOTP_SECRET},
    "dhan": {"client_id": DHAN_CLIENT, "access_token": DHAN_TOKEN},
    "coindcx": {"api_key": COINDCX_KEY, "api_secret": COINDCX_SECRET},
}


def fake_credentials(broker: str, **overrides: Any) -> Dict[str, Any]:
    """A complete, valid-looking (fake) credentials body for `broker`, with `overrides` on top."""
    return {**_BY_BROKER[broker], **overrides}
