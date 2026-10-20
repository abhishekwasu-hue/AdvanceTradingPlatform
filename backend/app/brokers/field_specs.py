"""UI2-5: the broker field map - one source of truth for what each broker's connection needs.

`BROKER_FIELD_SPECS` says, per broker in the registry (`app.brokers.registry.BROKER_ADAPTERS`), which credential fields
exist, their plain-English labels, their kind, whether a person types them or ATP fills them (an OAuth callback, a login,
the server itself), the format each must have and what is required. It is derived from the adapters (every field an
adapter reads is here, and nothing else - tests/test_ui2_5_broker_field_map.py checks both ways) and follows the table in
docs/design/UI_V2_PROPOSAL.md section 10.

Two readers use the same map, so they cannot drift:
- `GET /api/broker/specs` serves it to the Connect flow (UI2-6) - labels, kinds, patterns, never a stored value;
- `POST /api/broker/{name}/credentials` validates with it (`validate_credentials`): an unknown field, a token that ATP
  fills in itself, a bad format or a missing required field is refused with an English sentence that never repeats the
  value.

Patterns are written so the browser's RegExp and Python's `re` read them the same way (anchored, no Python-only syntax).

`legacy_paste` marks a field ATP normally fills in itself (an access token, a one-time login code, a redirect URL) that
the old Settings form can still send; the server keeps accepting it so the old layout's flows keep working until UI2-6
replaces that form. The new form never shows it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Field kinds (how the form shows the input).
KIND_TEXT = "text"
KIND_SECRET = "secret"
KIND_TOTP_SECRET = "totp-secret"
KIND_PIN = "pin"
KIND_CODE = "code"

# Who supplies the value.
BY_USER = "user"                       # typed by the person in the Connect form
BY_OAUTH_CALLBACK = "oauth_callback"   # the broker's redirect to ATP's callback brings it (or its exchange produces it)
BY_LOGIN = "login"                     # ATP's own login with the stored keys produces it
BY_SERVER = "server"                   # ATP fills it in (the redirect URL it serves)

# How the daily login works (v2 target, section 10).
LOGIN_OAUTH = "oauth"        # the broker's page opens and returns to ATP's callback; nothing is pasted
LOGIN_TOTP = "totp"          # ATP logs in with the stored PIN or password and the TOTP secret
LOGIN_TOKEN = "token"        # the person pastes a token from the broker's console (Dhan, UI-7)
LOGIN_API_KEY = "api_key"    # a long-lived key; no daily login

# Server-written payload entries that are never a form field (dropped if a client sends them).
SERVER_WRITTEN_FIELDS = frozenset({"analytics_meta", "oauth_pending"})

_ANY_TOKEN = r"^[\x21-\x7E]{1,4096}$"
_REDIRECT_URL = r"^https?://[\x21-\x7E]{1,500}$"
_BASE32_SECRET = r"^[A-Z2-7]{16,128}=*$"


@dataclass(frozen=True)
class FieldSpec:
    key: str                                  # the stored field (BrokerCredentials attribute or payload key)
    label: str                                # plain-English label
    kind: str                                 # text | secret | totp-secret | pin | code
    entered_by: str = BY_USER                 # user | oauth_callback | login | server
    required: bool = False
    # Required unless one of these fields is present too (e.g. an API secret is needed only to exchange a login code,
    # so a stored access token makes it optional). Checked on the stored-plus-sent result.
    required_unless: Tuple[str, ...] = ()
    pattern: Optional[str] = None             # anchored regex, same in the browser and on the server
    format_hint: str = ""                     # the pattern in words, used in the error message
    max_length: Optional[int] = None
    help: str = ""
    example: str = ""
    advanced: bool = False                    # shown under "Advanced"
    legacy_paste: bool = False                # filled by ATP, but the old Settings form may still send it (see above)

    @property
    def accepted_from_client(self) -> bool:
        return self.entered_by == BY_USER or self.legacy_paste


@dataclass(frozen=True)
class BrokerSpec:
    name: str
    display_name: str
    tile_line: str
    login_method: str
    console_url: Optional[str]
    fields: Tuple[FieldSpec, ...]
    oauth_callback_path: Optional[str] = None   # the broker's redirect lands here (login_method oauth)
    oauth_start_path: Optional[str] = None
    login_note: str = ""

    def get(self, key: str) -> Optional[FieldSpec]:
        return next((f for f in self.fields if f.key == key), None)

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields)


def _access_token(label: str = "Access token", *, by: str = BY_OAUTH_CALLBACK, legacy: bool = True) -> FieldSpec:
    return FieldSpec("access_token", label, KIND_SECRET, entered_by=by, pattern=_ANY_TOKEN, format_hint="letters, digits or symbols with no spaces",
                     max_length=4096, help="ATP gets this when you log in to the broker; you never type it.", legacy_paste=legacy)


def _login_code(label: str = "Login code") -> FieldSpec:
    return FieldSpec("request_token", label, KIND_CODE, entered_by=BY_OAUTH_CALLBACK, pattern=_ANY_TOKEN,
                     format_hint="letters, digits or symbols with no spaces", max_length=4096,
                     help="The one-time code the broker sends back after you log in; ATP's callback receives it.",
                     legacy_paste=True)


def _redirect_url(help_text: str) -> FieldSpec:
    return FieldSpec("redirect_uri", "Redirect URL", KIND_TEXT, entered_by=BY_SERVER, pattern=_REDIRECT_URL,
                     format_hint="a web address starting with https:// (or http:// on a local setup)", max_length=512,
                     help=help_text, advanced=True, legacy_paste=True)


def _totp_secret(broker: str, required_unless: Tuple[str, ...] = ()) -> FieldSpec:
    return FieldSpec("totp_secret", "TOTP secret", KIND_TOTP_SECRET, required=True, required_unless=required_unless,
                     pattern=_BASE32_SECRET, format_hint="the 16 to 128 character key (letters A-Z and digits 2-7) shown when you "
                                                          "set up TOTP, not today's 6-digit code",
                     max_length=160,
                     help=f"The key {broker} shows when you turn on TOTP (often under 'Show key' next to the QR code). Spaces "
                          "are ignored. A 6-digit code is refused because it stops working within a minute.")


BROKER_FIELD_SPECS: Dict[str, BrokerSpec] = {
    "upstox": BrokerSpec(
        name="upstox", display_name="Upstox", tile_line="Log in once each morning", login_method=LOGIN_OAUTH,
        console_url="https://account.upstox.com/developer/apps",
        oauth_start_path="/api/broker/upstox/oauth/start", oauth_callback_path="/api/broker/upstox/oauth/callback",
        login_note="Brokers end the session every trading day. Log in once each morning; we never see or store your broker password.",
        fields=(
            FieldSpec("api_key", "API key", KIND_TEXT, required=True, required_unless=("analytics_token",),
                      pattern=r"^[\x21-\x7E]{8,128}$", format_hint="8 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="From your app in the Upstox developer console."),
            # Not needed for a data-only connection (an analytics token alone) or with a session already on file.
            FieldSpec("api_secret", "API secret", KIND_SECRET, required=True, required_unless=("access_token", "analytics_token"),
                      pattern=r"^[\x21-\x7E]{4,128}$", format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="Shown next to the API key in the Upstox developer console."),
            _redirect_url("Copy this into your Upstox app's Redirect URL. ATP fills it in when you log in."),
            _access_token(),
            _login_code(),
            FieldSpec("analytics_token", "Analytics token (read-only, valid one year)", KIND_SECRET, pattern=r"^[\x21-\x7E]{1,4096}$",
                      format_hint="letters, digits or symbols with no spaces", max_length=4096, advanced=True,
                      help="Optional. Generate it in Upstox Developer Apps → Analytics. Used for market data only, never for orders."),
        ),
    ),
    "zerodha": BrokerSpec(
        name="zerodha", display_name="Zerodha (Kite)", tile_line="Log in once each morning", login_method=LOGIN_OAUTH,
        console_url="https://developers.kite.trade/apps",
        oauth_start_path="/api/broker/zerodha/oauth/start", oauth_callback_path="/api/broker/zerodha/oauth/callback",
        login_note="Brokers end the session every trading day. Log in once each morning; we never see or store your broker password.",
        fields=(
            FieldSpec("api_key", "API key", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{4,128}$",
                      format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="From your app in the Kite developer console."),
            FieldSpec("api_secret", "API secret", KIND_SECRET, required=True, required_unless=("access_token",),
                      pattern=r"^[\x21-\x7E]{4,128}$", format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="Shown next to the API key in the Kite developer console."),
            _access_token(),
            _login_code("Request token"),
        ),
    ),
    "fyers": BrokerSpec(
        name="fyers", display_name="Fyers", tile_line="Log in once each morning", login_method=LOGIN_OAUTH,
        console_url="https://myapi.fyers.in/dashboard",
        oauth_start_path="/api/broker/fyers/oauth/start", oauth_callback_path="/api/broker/fyers/oauth/callback",
        login_note="Brokers end the session every trading day. Log in once each morning; we never see or store your broker password.",
        fields=(
            FieldSpec("api_key", "App ID", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{4,128}$",
                      format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="From your app on the Fyers API dashboard.", example="like XB12345-100"),
            FieldSpec("api_secret", "Secret ID", KIND_SECRET, required=True, required_unless=("access_token",),
                      pattern=r"^[\x21-\x7E]{4,128}$", format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="Shown next to the App ID on the Fyers API dashboard."),
            _redirect_url("Copy this into your Fyers app's Redirect URL. ATP fills it in when you log in."),
            _access_token(),
            _login_code("Auth code"),
        ),
    ),
    "angel_one": BrokerSpec(
        name="angel_one", display_name="Angel One", tile_line="One-click login with your stored PIN and TOTP secret",
        login_method=LOGIN_TOTP, console_url="https://smartapi.angelone.in/",
        login_note="ATP logs in to Angel One for you with the PIN and TOTP secret you store here, encrypted.",
        fields=(
            FieldSpec("api_key", "SmartAPI key", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{4,128}$",
                      format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="From your app on the SmartAPI portal."),
            FieldSpec("client_id", "Client ID", KIND_TEXT, required=True, required_unless=("access_token",),
                      pattern=r"^[\x21-\x7E]{2,64}$", format_hint="2 to 64 letters, digits or symbols with no spaces", max_length=64,
                      help="Your Angel One client code."),
            FieldSpec("pin", "PIN", KIND_PIN, required=True, required_unless=("access_token",), pattern=r"^[0-9]{4,8}$",
                      format_hint="4 to 8 digits", max_length=8, help="The PIN you use to log in to Angel One."),
            _totp_secret("Angel One", required_unless=("access_token",)),
            _access_token("Session token", by=BY_LOGIN),
            # SmartAPI asks every call to name the client's addresses; the adapter sends placeholders when these are empty.
            FieldSpec("public_ip", "Public IP", KIND_TEXT, pattern=r"^[0-9A-Fa-f.:]{2,45}$", format_hint="an IPv4 or IPv6 address",
                      max_length=45, advanced=True, help="Optional. The IP address you registered on the SmartAPI app."),
            FieldSpec("local_ip", "Local IP", KIND_TEXT, pattern=r"^[0-9A-Fa-f.:]{2,45}$", format_hint="an IPv4 or IPv6 address",
                      max_length=45, advanced=True, help="Optional. Leave empty to use ATP's default."),
            FieldSpec("mac", "MAC address", KIND_TEXT, pattern=r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$",
                      format_hint="six pairs of hexadecimal digits separated by : or -", max_length=17, advanced=True,
                      help="Optional. Leave empty to use ATP's default."),
        ),
    ),
    "shoonya": BrokerSpec(
        name="shoonya", display_name="Shoonya (Finvasia)", tile_line="One-click login with your stored password and TOTP secret",
        login_method=LOGIN_TOTP, console_url=None,
        login_note="ATP logs in to Shoonya for you with the password and TOTP secret you store here, encrypted.",
        fields=(
            FieldSpec("client_id", "User ID", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{2,64}$",
                      format_hint="2 to 64 letters, digits or symbols with no spaces", max_length=64, help="Your Shoonya user ID.",
                      example="like FA12345"),
            FieldSpec("api_secret", "Login password", KIND_SECRET, required=True, pattern=r"^[^\x00-\x1F\x7F]{1,128}$",
                      format_hint="1 to 128 characters on one line, no control characters", max_length=128,
                      help="The password you use to log in to Shoonya. ATP sends only its hash, as Shoonya's API asks."),
            FieldSpec("api_key", "API key (vendor)", KIND_SECRET, required=True, pattern=r"^[\x21-\x7E]{4,128}$",
                      format_hint="4 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="The API key Shoonya gave you for API access."),
            _totp_secret("Shoonya"),
            FieldSpec("vc", "Vendor code", KIND_TEXT, pattern=r"^[\x21-\x7E]{1,64}$", format_hint="1 to 64 letters, digits or symbols with no spaces",
                      max_length=64, advanced=True, help="Leave empty to use your user ID followed by _API."),
            FieldSpec("imei", "Device ID", KIND_TEXT, pattern=r"^[\x21-\x7E]{1,128}$",
                      format_hint="1 to 128 letters, digits or symbols with no spaces", max_length=128, advanced=True,
                      help="Leave empty to use ATP's default."),
        ),
    ),
    "dhan": BrokerSpec(
        name="dhan", display_name="Dhan", tile_line="Paste a new access token when it ends", login_method=LOGIN_TOKEN,
        console_url="https://web.dhan.co/",
        login_note="Dhan's access token lasts 24 hours from when you generate it. Paste a new one when it ends.",
        fields=(
            FieldSpec("client_id", "Client ID", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{2,64}$",
                      format_hint="2 to 64 letters, digits or symbols with no spaces", max_length=64, help="Your Dhan client ID."),
            FieldSpec("access_token", "Paste your Dhan access token", KIND_SECRET, required=True,
                      pattern=r"^[\x21-\x7E]{16,4096}$",
                      format_hint="at least 16 letters, digits or symbols with no spaces", max_length=4096,
                      help="Generate it on web.dhan.co under My Profile → Access DhanHQ APIs. It lasts 24 hours."),
        ),
    ),
    "coindcx": BrokerSpec(
        name="coindcx", display_name="CoinDCX", tile_line="No daily login", login_method=LOGIN_API_KEY,
        console_url="https://coindcx.com/api-dashboard",
        login_note="No daily login: the API key keeps working until you delete it on CoinDCX.",
        fields=(
            FieldSpec("api_key", "API key", KIND_TEXT, required=True, pattern=r"^[\x21-\x7E]{16,128}$",
                      format_hint="16 to 128 letters, digits or symbols with no spaces", max_length=128, help="From the CoinDCX API dashboard."),
            FieldSpec("api_secret", "API secret", KIND_SECRET, required=True, pattern=r"^[\x21-\x7E]{16,128}$",
                      format_hint="16 to 128 letters, digits or symbols with no spaces", max_length=128,
                      help="Shown once when you create the key on CoinDCX."),
            _access_token("Session key", by=BY_LOGIN, legacy=False),
        ),
    ),
}


def broker_spec(name: str) -> Optional[BrokerSpec]:
    return BROKER_FIELD_SPECS.get(name)


def normalise_value(spec: FieldSpec, value: str) -> str:
    """What is stored for a sent value: trimmed; a TOTP secret without spaces and in capitals (as broker apps show it)."""
    text = value.strip()
    if spec.kind == KIND_TOTP_SECRET:
        text = text.replace(" ", "").upper()
    return text


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


def validate_credentials(name: str, sent: Mapping[str, Any], merged: Mapping[str, Any], *,
                         check_required: bool = True) -> List[FieldError]:
    """The problems with one credentials save, in plain English (never repeating a value).

    `sent` is what the client sent (non-empty values only, already normalised); `merged` is the stored payload with the
    sent values on top - what the save would store. Unknown fields and values ATP fills in itself are refused, each sent
    value must match its format, and every required field must be present after the merge (`check_required` False when
    only the analytics token is saved, which leaves the daily-session fields as they are)."""
    spec = BROKER_FIELD_SPECS.get(name)
    if spec is None:
        return [FieldError("", f"Unknown broker '{name}'.")]
    errors: List[FieldError] = []
    for key, value in sent.items():
        if key in SERVER_WRITTEN_FIELDS:
            continue
        fs = spec.get(key)
        if fs is None:
            errors.append(FieldError(key, f"{spec.display_name} does not use '{key}'. Remove it and try again."))
            continue
        if not fs.accepted_from_client:
            errors.append(FieldError(key, f"{spec.display_name} {fs.label.lower()} is filled in by ATP; it is never typed."))
            continue
        text = value if isinstance(value, str) else str(value)
        if fs.max_length is not None and len(text) > fs.max_length:
            errors.append(FieldError(key, f"{spec.display_name} {fs.label}: too long - {fs.format_hint}."))
        elif fs.pattern and not re.fullmatch(fs.pattern, text):
            errors.append(FieldError(key, f"{spec.display_name} {fs.label} does not look right: expected {fs.format_hint}."))
    if check_required:
        present = {k for k, v in merged.items() if v not in (None, "")}
        for fs in spec.fields:
            if fs.required and fs.key not in present and not any(alt in present for alt in fs.required_unless):
                errors.append(FieldError(fs.key, f"{spec.display_name} needs your {fs.label}."))
    return errors


def public_view(spec: BrokerSpec) -> Dict[str, Any]:
    """The spec as served (labels, kinds, patterns; there is no stored value in a spec to leak)."""
    view = asdict(spec)
    for item, fs in zip(view["fields"], spec.fields):
        item["required_unless"] = list(fs.required_unless)
        item["accepted_from_client"] = fs.accepted_from_client
    return view
