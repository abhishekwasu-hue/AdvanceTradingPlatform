"""P2a: the simple broker-connect catalogue - for each broker, the easiest official way to connect and only the fields
that way needs.

Static data, read by GET /api/broker/catalog (app/brokers/connect.py). The research behind every choice (the method,
its validity and the official source, with the date checked) is in docs/brokers/EASIEST_LOGIN.md; change both together.

Methods:
- `analytics_token`  one long-lived, read-only token (Upstox Analytics Token, one year): data, charts, the scanner, the
                     option chain and PAPER trading; no daily login.
- `access_token`     paste the token the broker's own web console generates (Dhan: 24 hours); renewed by pasting the
                     day's new token.
- `oauth_redirect`   BRK-1 B: store the app's key and secret once; each day press Log in and sign in on the broker's own
                     page, which sends the browser back to ATP's callback - ATP exchanges the one-time code itself, nothing
                     is pasted (Zerodha Kite, Fyers).
- `login_code`       the older way of the same login: paste the address the broker's page lands on. Kept only as the
                     hidden fallback (`fallback`) of `oauth_redirect`, for an app whose Redirect URL cannot be ATP's.
- `totp_auto`        store the login details and the TOTP secret once; ATP logs in by itself each morning before the open
                     (app/brokers/auto_login.py), making the one-time code from the secret; Renew is the manual fallback
                     (Angel One, Shoonya, Kotak Neo, Groww, Dhan).
- `api_key_secret`   a long-lived API key and secret (CoinDCX); no daily login.

`fallback` (optional): another official way the same broker connects, accepted by connect / renew but shown only behind
its own link - Dhan's pasted 24-hour token, the pasted login address of Zerodha / Fyers.

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
OAUTH_REDIRECT = "oauth_redirect"
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
    # Part 1B: the plain-words connect guide (a few points and the broker's official links), for a broker whose
    # connection needs more than the three steps; None for the others.
    guide: Optional[Tuple[Tuple[str, ...], Tuple[Tuple[str, str], ...]]] = None
    # BRK-1 B: the hidden fallback way (see the module notes): the link's words, what connect takes that way (empty: the
    # usual fields) and what renew takes that way.
    fallback_label: str = ""
    fallback_fields: Tuple[Field, ...] = field(default_factory=tuple)
    fallback_renew_fields: Tuple[Field, ...] = field(default_factory=tuple)

    def field_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def renew_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.renew_fields)

    def fallback_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fallback_fields)

    def fallback_renew_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fallback_renew_fields)

    def label_of(self, key: str) -> str:
        for f in self.fields + self.renew_fields + self.fallback_fields + self.fallback_renew_fields + UPSTOX_ALTERNATIVE_FIELDS:
            if f.key == key:
                return f.label
        return key.replace("_", " ")

    def as_dict(self) -> Dict[str, Any]:
        return {"broker": self.broker, "name": self.name, "method": self.method,
                "fields": [f.as_dict() for f in self.fields], "how_to": list(self.how_to), "doc_url": self.doc_url,
                "daily_renewal": self.daily_renewal, "available": self.broker in available_brokers(),
                "renew_fields": [f.as_dict() for f in self.renew_fields],
                "fallback": None if not self.fallback_label else {
                    "label": self.fallback_label, "fields": [f.as_dict() for f in self.fallback_fields],
                    "renew_fields": [f.as_dict() for f in self.fallback_renew_fields]},
                "guide": None if self.guide is None else {"points": list(self.guide[0]),
                                                          "links": [{"label": label, "url": url} for label, url in self.guide[1]]}}


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
        broker="dhan", name="Dhan", method=TOTP_AUTO,
        fields=(Field("client_id", "Client ID", TEXT, "Your Dhan Client ID"),
                Field("pin", "Dhan PIN", SECRET, "The 6-digit PIN you use to log in to Dhan"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown under the QR code when you set up TOTP")),
        how_to=("Sign in to web.dhan.co and open My Profile, then Access DhanHQ APIs.",
                "Generate an access token (it lasts 24 hours) and copy it with your Client ID.",
                "Paste both here; on later days paste only the new token."),
        doc_url="https://dhanhq.co/docs/v2/authentication/",
        daily_renewal=True,
        # BRK-1 B: the TOTP login (EASIEST_LOGIN.md section 2); the token pasted from web.dhan.co is the fallback.
        fallback_label="Paste an access token instead",
        fallback_fields=(Field("client_id", "Client ID", TEXT, "Your Dhan Client ID"),
                         Field(ACCESS_TOKEN, "Access token", SECRET, "Paste the access token (it lasts 24 hours)")),
        fallback_renew_fields=(Field(ACCESS_TOKEN, "Today's access token", SECRET, "Paste today's access token"),),
    ),
    BrokerEntry(
        broker="zerodha", name="Zerodha (Kite)", method=OAUTH_REDIRECT,
        fields=(Field("api_key", "API key", TEXT, "Your Kite Connect API key"),
                Field("api_secret", "API secret", SECRET, "Your Kite Connect API secret")),
        how_to=("Sign in to developers.kite.trade and open your Kite Connect app.",
                "Copy the API key and the API secret and paste them here.",
                "Each morning press Log in, sign in to Kite and paste the address you land on."),
        doc_url="https://kite.trade/docs/connect/v3/user/",
        daily_renewal=True,
        # BRK-1 B: Log in opens Kite, Kite returns to ATP's callback (one button, nothing pasted). Fallback: paste the address.
        fallback_label="Kite did not bring you back to ATP? Paste the address instead",
        fallback_renew_fields=(_LOGIN_ADDRESS,),
    ),
    BrokerEntry(
        broker="fyers", name="Fyers", method=OAUTH_REDIRECT,
        fields=(Field("api_key", "App ID", TEXT, "For example ABCD1234-200"),
                Field("api_secret", "Secret ID", SECRET, "Your app's Secret ID")),
        how_to=("Sign in to myapi.fyers.in and open your app.",
                "Copy the App ID, the Secret ID and the Redirect URL and paste them here.",
                "Each morning press Log in, sign in to Fyers and paste the address you land on."),
        doc_url="https://fyers.in/api-docs/v3/api/authentication/",
        daily_renewal=True,
        # BRK-1 B: as Zerodha. The fallback connect also takes the app's own Redirect URL, which the fallback login link
        # sends to Fyers (it must match the app's).
        fallback_label="Your Fyers app cannot use ATP's address? Use your app's own address instead",
        fallback_fields=(Field("api_key", "App ID", TEXT, "For example ABCD1234-200"),
                         Field("api_secret", "Secret ID", SECRET, "Your app's Secret ID"),
                         Field("redirect_uri", "Redirect URL", TEXT, "Exactly as saved on your Fyers app")),
        fallback_renew_fields=(_LOGIN_ADDRESS,),
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
        doc_url="https://smartapi.angelone.in/docs/User",
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
    # Part 1B (docs/design/BROKER_KOTAK_NEO.md): the official method is a TOTP login with the consumer key, the mobile
    # number, the UCC and the MPIN (kotakneoapi 3.0.9). BRK-1: no per-broker gate.
    BrokerEntry(
        broker="kotak_neo", name="Kotak Neo", method=TOTP_AUTO,
        fields=(Field("api_key", "Consumer key", SECRET, "From the NEO app: More, then Trade API"),
                Field("mobile_number", "Mobile number", TEXT, "The number on your Kotak account, with +91"),
                Field("client_id", "Client code (UCC)", TEXT, "Your Kotak Neo client code"),
                Field("pin", "MPIN", SECRET, "The MPIN you use to log in to Kotak Neo"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown when you registered TOTP")),
        how_to=("In the Kotak Neo app open More, then Trade API, and generate an application; copy its consumer key.",
                "On the same Trade API page register TOTP and copy the secret key it shows (not the six-digit code).",
                "Paste the key, your mobile number, client code, MPIN and TOTP secret here; each morning press Renew."),
        doc_url="https://www.kotakneo.com/platform/kotak-neo-trade-api/",
        daily_renewal=True,
        guide=(("Kotak Neo asks that orders from connected apps come from a registered fixed internet address, and it does not take market orders from them.",
                "ATP never sees your Kotak Neo password; the MPIN and secret key you store here are encrypted and used only for the daily login."),
               (("Kotak Neo Trade API", "https://www.kotakneo.com/platform/kotak-neo-trade-api/"),
                ("Static IP rules", "https://www.kotakneo.com/platform/kotak-neo-trade-api/static-ip-details/"),
                ("Official API guide", "https://github.com/Kotak-Neo/kotak-neo-python"))),
    ),
    # Part 1B (docs/design/BROKER_GROWW.md): the easiest official way is the TOTP key - Groww's API key of the TOTP kind
    # and its TOTP secret; each morning one click makes the day's token. BRK-1: no per-broker gate. The approval way (API key + secret, approved daily on Groww) is supported by the adapter.
    BrokerEntry(
        broker="groww", name="Groww", method=TOTP_AUTO,
        fields=(Field("api_key", "API key (TOTP)", SECRET, "From Groww: Trade API, then API Keys"),
                Field("totp_secret", "TOTP secret", SECRET, "The secret shown with the TOTP key")),
        how_to=("On Groww open Trade API, then API Keys, and generate a TOTP key; copy the key and the secret it shows.",
                "Paste both here; ATP makes the six-digit code from the secret, so you never type it.",
                "Each morning press Renew; Groww ends every session at 6 in the morning."),
        doc_url="https://groww.in/trade-api/docs/python-sdk",
        daily_renewal=True,
        guide=(("Groww's Trade API is a paid Groww plan; Groww asks that orders from connected apps come from a registered fixed internet address.",
                "ATP never sees your Groww password; the key and secret you store here are encrypted and used only for the daily login."),
               (("Groww Trade API", "https://groww.in/trade-api"),
                ("Official API guide", "https://groww.in/trade-api/docs/python-sdk"),
                ("Static IP rules", "https://groww.in/blog/static-ip-api-trading-setup"))),
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
