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


# BRK-1 C: what a guide step asks the user to copy INTO the broker (the panel shows the exact value with a Copy button).
COPY_REDIRECT_URL = "redirect_url"     # ATP's own callback address (GET /api/broker/specs `readonly.redirect_url`)
COPY_STATIC_IP = "static_ip"           # the server's egress IP (GET /api/broker/specs `readonly.server_ip`)

# BRK-1 C: the one identical info line on every broker's connect panel (order A: the SEBI static-IP / market-order rules
# apply to every broker equally).
SEBI_INFO = ("From **1 Apr 2026** (SEBI): API orders must come from a registered **static IP**, and brokers may turn "
             "**market orders** into limit orders.")


@dataclass(frozen=True)
class Step:
    """One action of the connect guide, in the broker's own current menu names; `**word**` marks a key word (bold).
    `url`: the official page the step happens on (a broker-side step without its own address happens on the page of the
    step before it, see `_steps`). `gives`: the field key this step produces. `copy`: a value the user copies INTO the
    broker (COPY_REDIRECT_URL / COPY_STATIC_IP). `atp`: the step happens here in ATP (typed into the panel)."""
    text: str
    url: Optional[str] = None
    gives: Optional[str] = None
    copy: Optional[str] = None
    atp: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "url": self.url, "gives": self.gives, "copy": self.copy,
                "where": "atp" if self.atp else "broker"}


def _steps(*steps: Step) -> Tuple[Step, ...]:
    """A broker-side step without its own address happens on the page the step before it opened, so it carries that
    page's address: every broker-side step links to the official page it happens on."""
    out: List[Step] = []
    page: Optional[str] = None
    for st in steps:
        if not st.atp:
            page = st.url or page
            st = Step(st.text, page, st.gives, st.copy)
        out.append(st)
    return tuple(out)


@dataclass(frozen=True)
class Guide:
    """BRK-1 C: the visible step-by-step guide (docs/brokers/EASIEST_LOGIN.md, checked 2026-11-02): up-front one-line
    warnings, only where true (API charges, a static IP to register, the TOTP secret and not the six-figure code, a secret
    shown only once), the shared SEBI info line, then the numbered steps."""
    warnings: Tuple[str, ...]
    steps: Tuple[Step, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"warnings": list(self.warnings), "info": SEBI_INFO, "steps": [st.as_dict() for st in self.steps]}


def plain(text: str) -> str:
    """A guide line without its bold marks."""
    return text.replace("**", "")


@dataclass(frozen=True)
class BrokerEntry:
    broker: str
    name: str
    method: str
    fields: Tuple[Field, ...]
    guide: Guide
    doc_url: str
    daily_renewal: bool
    # What POST /{broker}/renew takes each day (empty: one click, or no daily renewal at all).
    renew_fields: Tuple[Field, ...] = field(default_factory=tuple)
    # BRK-1 B: the hidden fallback way (see the module notes): the link's words, what connect takes that way (empty: the
    # usual fields) and what renew takes that way.
    fallback_label: str = ""
    fallback_fields: Tuple[Field, ...] = field(default_factory=tuple)
    fallback_renew_fields: Tuple[Field, ...] = field(default_factory=tuple)

    @property
    def how_to(self) -> Tuple[str, ...]:
        """The guide's steps as plain lines (for clients that read `how_to`)."""
        return tuple(plain(st.text) for st in self.guide.steps)

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
                "guide": self.guide.as_dict()}


# Upstox's second option: a pasted daily access token (for LIVE orders later). Accepted by connect / renew in place of
# the analytics token; never asked for in the catalogue's `fields`.
UPSTOX_ALTERNATIVE_FIELDS: Tuple[Field, ...] = (Field(ACCESS_TOKEN, "Daily access token"),)

_LOGIN_ADDRESS = Field("request_token", "Address after login", SECRET, "Paste the whole address from the browser bar")

# --- BRK-1 C: the connect guides (docs/brokers/EASIEST_LOGIN.md, official pages checked 2026-11-02) -------------------
# One action per step, the broker's own current menu names; every broker-side step links to its official page; `gives`
# names the field each step produces (backend/tests/test_brk1_catalog_guides.py checks that every field is produced).
_TOTP_SECRET_WARNING = "Copy the **TOTP secret** (the long key), not the 6-digit code."
_STATIC_IP_ORDERS = "Orders need a **static IP** registered with {name}; it can be changed only **once a week**."

_UPSTOX = Guide(warnings=(), steps=_steps(
    Step("Sign in to Upstox and open **Developer Apps**.", "https://account.upstox.com/developer/apps"),
    Step("Open the **Analytics** tab.", "https://account.upstox.com/developer/apps#analytics"),
    Step("Click **Generate Token** (a new token replaces your old one)."),
    Step("Click **Confirm**."),
    Step("Click the **copy** icon next to the token.", gives=ANALYTICS_TOKEN),
))

_DHAN = Guide(warnings=(
    "Dhan charges **Rs 499 + taxes a month** for its Data APIs; trading APIs are free.",
    "Orders need a **static IP** registered with Dhan; it cannot be changed for **7 days**.",
    _TOTP_SECRET_WARNING,
), steps=_steps(
    Step("Sign in to **Dhan web**.", "https://web.dhan.co"),
    Step("Open **My Profile** > **DhanHQ Trading APIs**."),
    Step("Click **Setup TOTP**."),
    Step("Enter the **OTP** sent to your mobile or email."),
    Step("Copy the **key** shown under the QR code.", gives="totp_secret"),
    Step("Add the same key to your **authenticator app** and enter its first 6-digit code to confirm."),
    Step("Copy your **Client ID** from **My Profile**.", gives="client_id"),
    Step("For orders: add ATP's **static IP** on the same APIs page.", copy=COPY_STATIC_IP),
    Step("Type your 6-digit **Dhan PIN** here. ATP then logs you in by itself each morning.", gives="pin", atp=True),
))

_ZERODHA = Guide(warnings=(
    "Kite Connect costs **Rs 500 a month** per app; the free Personal plan has no market data.",
    _STATIC_IP_ORDERS.format(name="Zerodha"),
), steps=_steps(
    Step("Log in to the **Kite Connect** developer portal (your Zerodha account needs **TOTP** on).",
         "https://developers.kite.trade/login"),
    Step("Create a **new app**."),
    Step("Paste ATP's address into **Redirect URL**.", copy=COPY_REDIRECT_URL),
    Step("Copy the **API key**.", gives="api_key"),
    Step("Copy the **API secret**.", gives="api_secret"),
    Step("Open **Profile** (top-right) > **IP Whitelist**."),
    Step("Enter ATP's **static IP**.", copy=COPY_STATIC_IP),
    Step("Click **Update**."),
    Step("Each morning press **Log in** here and sign in on Kite.", atp=True),
))

_FYERS = Guide(warnings=(
    "The trading app needs a **static IP**; it cannot be changed for **7 days**.",
), steps=_steps(
    Step("Open the **FYERS API Dashboard**.", "https://fyers.in/web/api-dashboard/user-apps"),
    Step("Under **My apps**, click **Algo trading app** (Type: **Trading**)."),
    Step("Enter ATP's **static IP** as the **Primary IP**.", copy=COPY_STATIC_IP),
    Step("Paste ATP's address into **Redirect URL**.", copy=COPY_REDIRECT_URL),
    Step("Select the **permissions**."),
    Step("Accept the **API Terms and Conditions**."),
    Step("Click **Activate**."),
    Step("Copy the **App ID** (it ends with **-200**).", gives="api_key"),
    Step("Copy the **Secret**.", gives="api_secret"),
    Step("Each morning press **Log in** here and sign in on Fyers.", atp=True),
))

_ANGEL_ONE = Guide(warnings=(
    _STATIC_IP_ORDERS.format(name="Angel One"),
    _TOTP_SECRET_WARNING,
), steps=_steps(
    Step("Sign up or sign in on **SmartAPI**.", "https://smartapi.angelone.in/signin"),
    Step("Click **+ ADD APP**."),
    Step("Enter an **App Name**."),
    Step("Enter a **Redirect URL** (any address you own; ATP does not use it)."),
    Step("Enter ATP's **static IP** as the **Primary Static IP**.", copy=COPY_STATIC_IP),
    Step("Click **Add**."),
    Step("Copy the app's **API Key**.", gives="api_key"),
    Step("Open **Enable TOTP**.", "https://smartapi.angelone.in/enable-totp"),
    Step("Enter your Angel One **Client ID**.", gives="client_id"),
    Step("Enter your **PIN**.", gives="pin"),
    Step("Click **Login**."),
    Step("Enter the **OTP** sent to your email and mobile."),
    Step("Click **Proceed**."),
    Step("Click **Can't Scan ? Copy the key** and copy the long key.", gives="totp_secret"),
))

# Shoonya's docs name no web-terminal address: the steps link to Shoonya's own pages that describe them.
_SHOONYA = Guide(warnings=(
    "Shoonya needs ATP's **static IP** registered even to **log in**.",
    _TOTP_SECRET_WARNING,
    "The TOTP key is **shown only once**; save it.",
), steps=_steps(
    Step("Sign in to the **Shoonya web terminal**.", "https://www.shoonya.com/api-documentation/ip-whitelisting"),
    Step("Click your **profile icon**."),
    Step("Open **API Key Generation**."),
    Step("Enter ATP's **static IP** as the **Primary IP Address**.", copy=COPY_STATIC_IP),
    Step("Click **Update**."),
    Step("Copy the **Secret Code** (ATP calls it API key).", gives="api_key"),
    Step("Open **Profile** > **Security** > **TOTP Setup**.", "https://www.shoonya.com/api-documentation/totp-setup-guide"),
    Step("Copy the long key under the **QR code**.", gives="totp_secret"),
    Step("Type your Shoonya **User ID** here.", gives="client_id", atp=True),
    Step("Type your Shoonya login **password** here.", gives="api_secret", atp=True),
))

_KOTAK_NEO = Guide(warnings=(
    _STATIC_IP_ORDERS.format(name="Kotak Neo"),
    _TOTP_SECRET_WARNING,
), steps=_steps(
    Step("Open the **Kotak Neo** app or web.", "https://www.kotakneo.com/platform/kotak-neo-trade-api/"),
    Step("Go to **More** > **Trade API** > **API Dashboard**."),
    Step("Click **Create Application**."),
    Step("Copy the **token** shown.", gives="api_key"),
    Step("Click **Add IP**."),
    Step("Enter ATP's **static IP**.", copy=COPY_STATIC_IP),
    Step("Click **TOTP Registration** (top-right).", "https://www.kotakneo.com/platform/kotak-neo-trade-api/totp-registration/"),
    Step("Enter your registered **mobile number** and the **OTP**."),
    Step("Click **Proceed**."),
    Step("Copy the **setup key** shown with the QR code.", gives="totp_secret"),
    Step("Enter the **6-digit code** from your authenticator app to finish."),
    Step("Copy your **Client Code** from **Profile**.", "https://www.kotakneo.com/platform/kotak-neo-trade-api/", gives="client_id"),
    Step("Type your registered mobile number with **+91** here.", gives="mobile_number", atp=True),
    Step("Type your **MPIN** here.", gives="pin", atp=True),
))

_GROWW = Guide(warnings=(
    "Groww charges **Rs 499 + taxes a month** for the Trading API.",
    "Orders need a **static IP** registered with Groww.",
    _TOTP_SECRET_WARNING,
), steps=_steps(
    Step("Buy the **Trading API** subscription.", "https://groww.in/user/profile/trading-apis"),
    Step("Open the **API Keys** page and log in.", "https://groww.in/trade-api/api-keys"),
    Step("Open the dropdown on **Generate API key**."),
    Step("Click **Generate TOTP token**."),
    Step("Enter a **name**."),
    Step("Click **Continue**."),
    Step("Copy the **TOTP token**.", gives="api_key"),
    Step("Copy the **TOTP secret**.", gives="totp_secret"),
    Step("Click **Add static IP**."),
    Step("Enter ATP's **static IP** as the **Primary Static IP**.", copy=COPY_STATIC_IP),
    Step("Click **Update**."),
))

_COINDCX = Guide(warnings=(
    "The **secret** is hidden once you refresh the page; save it right away.",
), steps=_steps(
    Step("Open the **CoinDCX API Dashboard**.", "https://coindcx.com/api-dashboard"),
    Step("Click **Create API key**."),
    Step("Enter a **label**."),
    Step("Optional: tick **Bind IP Address to API key** with ATP's static IP.", copy=COPY_STATIC_IP),
    Step("Enter the **email OTP** and the **SMS OTP**."),
    Step("Copy the **Key**.", gives="api_key"),
    Step("Copy the **Secret** now.", gives="api_secret"),
))

CATALOG: Tuple[BrokerEntry, ...] = (
    BrokerEntry(
        broker="upstox", name="Upstox", method=ANALYTICS_TOKEN,
        fields=(Field(ANALYTICS_TOKEN, "Analytics Token", SECRET, "Paste your Upstox Analytics Token"),),
        guide=_UPSTOX,
        doc_url="https://upstox.com/developer/api-documentation/analytics-token/",
        daily_renewal=False,
    ),
    BrokerEntry(
        broker="dhan", name="Dhan", method=TOTP_AUTO,
        fields=(Field("client_id", "Client ID", TEXT, "Your Dhan Client ID"),
                Field("pin", "Dhan PIN", SECRET, "The 6-digit PIN you use to log in to Dhan"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown under the QR code when you set up TOTP")),
        guide=_DHAN,
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
        guide=_ZERODHA,
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
        guide=_FYERS,
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
        guide=_ANGEL_ONE,
        doc_url="https://smartapi.angelone.in/docs/User",
        daily_renewal=True,
    ),
    BrokerEntry(
        broker="shoonya", name="Shoonya (Finvasia)", method=TOTP_AUTO,
        fields=(Field("client_id", "User ID", TEXT, "Your Shoonya user ID"),
                Field("api_secret", "Password", SECRET, "Your Shoonya login password"),
                Field("api_key", "API key", SECRET, "From prism.shoonya.com, API key section"),
                Field("totp_secret", "TOTP secret", SECRET, "The key shown when you enabled TOTP")),
        guide=_SHOONYA,
        doc_url="https://www.shoonya.com/api-documentation",
        daily_renewal=True,
    ),
    BrokerEntry(
        broker="coindcx", name="CoinDCX", method=API_KEY_SECRET,
        fields=(Field("api_key", "API key", TEXT, "Your CoinDCX API key"),
                Field("api_secret", "API secret", SECRET, "Your CoinDCX API secret")),
        guide=_COINDCX,
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
        guide=_KOTAK_NEO,
        doc_url="https://www.kotakneo.com/platform/kotak-neo-trade-api/",
        daily_renewal=True,
    ),
    # Part 1B (docs/design/BROKER_GROWW.md): the easiest official way is the TOTP key - Groww's API key of the TOTP kind
    # and its TOTP secret; each morning one click makes the day's token. BRK-1: no per-broker gate. The approval way (API key + secret, approved daily on Groww) is supported by the adapter.
    BrokerEntry(
        broker="groww", name="Groww", method=TOTP_AUTO,
        fields=(Field("api_key", "API key (TOTP)", SECRET, "From Groww: Trade API, then API Keys"),
                Field("totp_secret", "TOTP secret", SECRET, "The secret shown with the TOTP key")),
        guide=_GROWW,
        doc_url="https://groww.in/trade-api/docs/python-sdk",
        daily_renewal=True,
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
