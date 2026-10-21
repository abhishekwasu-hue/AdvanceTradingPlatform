"""P2a: the simple broker-connect catalogue - for each broker, the easiest official way to connect and only the fields
that way needs.

Static data, read by GET /api/broker/catalog (app/brokers/connect.py). The research behind every choice (the method,
its validity and the official source, with the date checked) is in docs/brokers/EASIEST_LOGIN.md; change both together.

Methods:
- `analytics_token`  one long-lived, read-only token (Upstox Analytics Token, one year): data, charts, the scanner, the
                     option chain and PAPER trading; no daily login.
- `access_token`     paste the token the broker's own web console generates (Dhan: 24 hours); renewed by pasting the
                     day's new token.
- `login_code`       store the app's key and secret once; each day log in on the broker's own page and paste the address
                     it lands on (Zerodha Kite, Fyers) - the platform exchanges the one-time code.
- `totp_auto`        store the login details and the TOTP secret once; each day one click logs in, the platform makes
                     the one-time code from the secret (Angel One, Shoonya).
- `api_key_secret`   a long-lived API key and secret (CoinDCX); no daily login.

Field keys are `BrokerCredentials` fields (app/brokers/models.py), so the connect route stores them through the
existing encrypted path unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.brokers.registry import available_brokers

ANALYTICS_TOKEN = "analytics_token"
ACCESS_TOKEN = "access_token"
LOGIN_CODE = "login_code"
TOTP_AUTO = "totp_auto"
API_KEY_SECRET = "api_key_secret"

SECRET = "secret"
TEXT = "text"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = SECRET
    placeholder: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind}
        if self.placeholder:
            out["placeholder"] = self.placeholder
        return out


@dataclass(frozen=True)
class BrokerEntry:
    broker: str
    name: str
    method: str
    fields: Tuple[Field, ...]
    how_to: Tuple[str, ...]
    doc_url: str
    daily_renewal: bool
    # What POST /{broker}/renew takes each day (empty: one click, or no daily renewal at all).
    renew_fields: Tuple[Field, ...] = field(default_factory=tuple)

    def field_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def renew_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.renew_fields)

    def label_of(self, key: str) -> str:
        for f in self.fields + self.renew_fields + UPSTOX_ALTERNATIVE_FIELDS:
            if f.key == key:
                return f.label
        return key.replace("_", " ")

    def as_dict(self) -> Dict[str, Any]:
        return {"broker": self.broker, "name": self.name, "method": self.method,
                "fields": [f.as_dict() for f in self.fields], "how_to": list(self.how_to), "doc_url": self.doc_url,
                "daily_renewal": self.daily_renewal, "available": self.broker in available_brokers(),
                "renew_fields": [f.as_dict() for f in self.renew_fields]}


# Upstox's second option: a pasted daily access token (for LIVE orders later). Accepted by connect / renew in place of
# the analytics token; never asked for in the catalogue's `fields`.
UPSTOX_ALTERNATIVE_FIELDS: Tuple[Field, ...] = (Field(ACCESS_TOKEN, "Daily access token"),)

_LOGIN_ADDRESS = Field("request_token", "Address after login", SECRET, "Paste the whole address from the browser bar")

CATALOG: Tuple[BrokerEntry, ...] = (
    BrokerEntry(
        broker="upstox", name="Upstox", method=ANALYTICS_TOKEN,
        fields=(Field(ANALYTICS_TOKEN, "Analytics Token", SECRET, "Paste your Upstox Analytics Token"),),
        how_to=("Sign in to Upstox and open Developer Apps.",
                "Open the Analytics tab and generate a token; it is valid for one year.",
                "Copy the token and paste it here."),
        doc_url="https://upstox.com/developer/api-documentation/analytics-token/",
        daily_renewal=False,
    ),
    BrokerEntry(
        broker="dhan", name="Dhan", method=ACCESS_TOKEN,
        fields=(Field("client_id", "Client ID", TEXT, "Your Dhan Client ID"),
                Field(ACCESS_TOKEN, "Access token", SECRET, "Paste the access token")),
        how_to=("Sign in to web.dhan.co and open My Profile, then Access DhanHQ APIs.",
                "Generate an access token (it lasts 24 hours) and copy it with your Client ID.",
                "Paste both here; on later days paste only the new token."),
        doc_url="https://dhanhq.co/docs/v2/authentication/",
        daily_renewal=True,
        renew_fields=(Field(ACCESS_TOKEN, "Today's access token", SECRET, "Paste today's access token"),),
    ),
    BrokerEntry(
        broker="zerodha", name="Zerodha (Kite)", method=LOGIN_CODE,
        fields=(Field("api_key", "API key", TEXT, "Your Kite Connect API key"),
                Field("api_secret", "API secret", SECRET, "Your Kite Connect API secret")),
        how_to=("Sign in to developers.kite.trade and open your Kite Connect app.",
                "Copy the API key and the API secret and paste them here.",
                "Each morning press Log in, sign in to Kite and paste the address you land on."),
        doc_url="https://kite.trade/docs/connect/v3/user/",
        daily_renewal=True,
        renew_fields=(_LOGIN_ADDRESS,),
    ),
    BrokerEntry(
        broker="fyers", name="Fyers", method=LOGIN_CODE,
        fields=(Field("api_key", "App ID", TEXT, "For example ABCD1234-100"),
                Field("api_secret", "Secret ID", SECRET, "Your app's Secret ID"),
                Field("redirect_uri", "Redirect URL", TEXT, "Exactly as saved on your Fyers app")),
        how_to=("Sign in to myapi.fyers.in and open your app.",
                "Copy the App ID, the Secret ID and the Redirect URL and paste them here.",
                "Each morning press Log in, sign in to Fyers and paste the address you land on."),
        doc_url="https://myapi.fyers.in/docsv3",
        daily_renewal=True,
        renew_fields=(_LOGIN_ADDRESS,),
    ),
    BrokerEntry(
        broker="angel_one", name="Angel One", method=TOTP_AUTO,
        fields=(Field("api_key", "SmartAPI key", TEXT, "From your SmartAPI app"),
                Field("client_id", "Client code", TEXT, "Your Angel One client code"),
                Field("pin", "PIN", SECRET, "Your Angel One login PIN"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown when you enabled TOTP")),
        how_to=("Sign in to smartapi.angelone.in, create an app and copy its API key.",
                "Enable TOTP for SmartAPI and copy the secret key it shows (not the six-digit code).",
                "Paste the key, your client code, your PIN and the TOTP secret here; each morning press Log in."),
        doc_url="https://smartapi.angelbroking.com/docs",
        daily_renewal=True,
    ),
    BrokerEntry(
        broker="shoonya", name="Shoonya (Finvasia)", method=TOTP_AUTO,
        fields=(Field("client_id", "User ID", TEXT, "Your Shoonya user ID"),
                Field("api_secret", "Password", SECRET, "Your Shoonya login password"),
                Field("api_key", "API key", SECRET, "From prism.shoonya.com, API key section"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown when you enabled TOTP")),
        how_to=("Sign in to prism.shoonya.com and generate an API key in your profile.",
                "Enable TOTP and copy the secret key it shows (not the six-digit code).",
                "Paste your user ID, password, API key and TOTP secret here; each morning press Log in."),
        doc_url="https://www.shoonya.com/api-documentation",
        daily_renewal=True,
    ),
    BrokerEntry(
        broker="coindcx", name="CoinDCX", method=API_KEY_SECRET,
        fields=(Field("api_key", "API key", TEXT, "Your CoinDCX API key"),
                Field("api_secret", "API secret", SECRET, "Your CoinDCX API secret")),
        how_to=("Sign in to CoinDCX and open API Dashboard.",
                "Create an API key; copy the key and the secret (the secret is shown only once).",
                "Paste both here; no daily login is needed."),
        doc_url="https://docs.coindcx.com/",
        daily_renewal=False,
    ),
)

_BY_BROKER: Dict[str, BrokerEntry] = {e.broker: e for e in CATALOG}


def entry(broker: str) -> Optional[BrokerEntry]:
    return _BY_BROKER.get((broker or "").lower())


def display_name(broker: str) -> str:
    found = entry(broker)
    return found.name if found else (broker or "").replace("_", " ").title()


def catalog() -> List[Dict[str, Any]]:
    return [e.as_dict() for e in CATALOG]
