"""Shoonya's second factor (`factor2`) is the current TOTP code, not the stored secret.

The stored `totp_secret` is the base32 key from the broker's TOTP enrolment; the login must send the
six-digit code of the moment, generated the same way as Angel One's (one shared helper,
`app.brokers.totp.totp_code`). A value that is already a 6- or 8-digit code is sent as-is, spaces are
dropped, and the secret never shows up in the request, a log line or an error. No network: the
broker's HTTP client is a mock transport.
"""
import asyncio
import datetime as dt
import json
import logging
import types
from urllib.parse import parse_qs

import httpx
import pyotp
import pyotp.totp
import pytest

from app.brokers import angel_one
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.totp import totp_code

run = asyncio.run

SECRET = "JBSWY3DPEHPK3PXP"
FROZEN = dt.datetime(2026, 10, 20, 3, 46, 10, tzinfo=dt.timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch):
    """pyotp reads `datetime.datetime.now()` inside `TOTP.now()`; pin it to FROZEN. Returns the code for FROZEN."""
    expected = pyotp.TOTP(SECRET).at(FROZEN)          # computed before the patch (`at` type-checks against datetime)

    class _Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN if tz is None else FROZEN.astimezone(tz)

    monkeypatch.setattr(pyotp.totp, "datetime", types.SimpleNamespace(datetime=_Frozen))
    return expected


def _shoonya(totp_secret, handler):
    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret=totp_secret)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ShoonyaBroker.BASE_URL)
    return ShoonyaBroker(creds, client=client)


def _capture(sent: list):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess", "actid": "FA12345", "uname": "Test"})
    return handler


def _factor2(raw_body: str) -> str:
    return json.loads(parse_qs(raw_body)["jData"][0])["factor2"]


def test_the_stored_secret_is_sent_as_the_current_code(frozen_clock):
    sent: list = []
    run(_shoonya(SECRET, _capture(sent)).authenticate())
    assert _factor2(sent[0]) == frozen_clock
    assert len(frozen_clock) == 6 and frozen_clock.isdigit()
    assert SECRET not in sent[0] and SECRET.lower() not in sent[0]


def test_the_code_follows_the_clock(monkeypatch):
    later = FROZEN + dt.timedelta(seconds=90)
    expected = pyotp.TOTP(SECRET).at(later)
    assert expected != pyotp.TOTP(SECRET).at(FROZEN)

    class _Later(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr(pyotp.totp, "datetime", types.SimpleNamespace(datetime=_Later))
    sent: list = []
    run(_shoonya(SECRET, _capture(sent)).authenticate())
    assert _factor2(sent[0]) == expected


def test_a_secret_with_spaces_and_lower_case_gives_the_same_code(frozen_clock):
    sent: list = []
    run(_shoonya("  jbsw y3dp ehpk 3pxp ", _capture(sent)).authenticate())
    assert _factor2(sent[0]) == frozen_clock


@pytest.mark.parametrize("value,expected", [("123456", "123456"), ("12345678", "12345678"), (" 123 456 ", "123456"),
                                            ("01-01-1990", "01-01-1990"), ("ABCDE1234F", "ABCDE1234F")])
def test_a_code_or_a_dob_pan_factor_passes_through(value, expected):
    sent: list = []
    run(_shoonya(value, _capture(sent)).authenticate())
    assert _factor2(sent[0]) == expected


def test_no_totp_secret_sends_an_empty_factor2():
    sent: list = []
    run(_shoonya(None, _capture(sent)).authenticate())
    assert _factor2(sent[0]) == ""


def test_shoonya_and_angel_one_share_one_helper(frozen_clock):
    assert angel_one._totp_code is totp_code
    assert totp_code(SECRET) == frozen_clock == angel_one._totp_code(SECRET)


def test_a_rejected_login_never_carries_the_secret_in_the_error_or_the_logs(frozen_clock, caplog):
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stat": "Not_Ok", "emsg": "Invalid Input : Wrong TOTP"})

    caplog.set_level(logging.DEBUG)
    with pytest.raises(BrokerAPIError) as info:
        run(_shoonya(SECRET, reject).authenticate())
    for text in (str(info.value), repr(info.value), caplog.text, *(str(a) for a in info.value.args)):
        assert SECRET not in text and SECRET.lower() not in text


def test_a_malformed_secret_fails_with_a_fixed_message_that_does_not_echo_it(caplog):
    bad = "JBSWY3DPEHPK3PXPA"           # base32 characters, but not a decodable length
    called: list = []
    caplog.set_level(logging.DEBUG)
    with pytest.raises(BrokerAuthenticationError) as info:
        run(_shoonya(bad, lambda request: called.append(request) or httpx.Response(500)).authenticate())
    assert called == []                  # nothing reached the broker
    assert bad not in str(info.value) and bad not in caplog.text
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
