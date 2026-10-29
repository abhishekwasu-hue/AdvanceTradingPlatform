# Public API (v1) - developer reference

Phase K3 (master prompt V3.11-3.12, V3.14 rules 9-10). The public surface lives under
`/api/public/v1/*`, apart from the console's `/api/v1/*`, so a leaked key can reach only what
its scopes name and never credentials, team, kill switches or deployments.
`GET /api/public/v1/docs` returns this reference as JSON (scopes, endpoints, limits, error codes).

## Keys

- Created by an **OWNER** on a Pro/Business plan under *Settings → Public API keys*
  (`POST /api/api-keys`), shown **once**; the server stores only a SHA-256 hash and the prefix.
- Shape `atp_<prefix>_<secret>`. Send it as `X-API-Key: atp_...`.
- Each key has **scopes**, a **per-minute rate limit** (1-600), an optional **expiry**, and can
  be **revoked** (`DELETE /api/api-keys/{id}`). Every request is metered as one `api_call`
  against the plan's daily allowance (`max_api_calls_per_day`).
- A suspended organisation, or one downgraded to Free, gets `402` on every call.

| Scope | Grants |
| --- | --- |
| `read:account` | organisation, plan, limits, 30-day metered usage |
| `read:instruments` | instrument master search |
| `read:strategies` | inbuilt + custom strategies |
| `read:signals` | signal history |
| `read:orders` | orders |
| `read:positions` | open positions and trade history |
| `read:backtests` | saved backtest runs |
| `read:risk` | risk limits and risk events |
| `write:signals` | submit a signal for PAPER execution (idempotent) |

## Endpoints

| Method | Path | Scope | Notes |
| --- | --- | --- | --- |
| GET | `/account` | read:account | |
| GET | `/instruments/search?q=&limit=` | read:instruments | |
| GET | `/strategies` | read:strategies | `kind` = inbuilt / custom |
| GET | `/signals?limit=` | read:signals | newest first |
| POST | `/signals` | write:signals | body below; `201` with `order_id`, `status`, `executed`, `reasons` |
| GET | `/orders?limit=` | read:orders | |
| GET | `/positions` | read:positions | open trades |
| GET | `/trades?limit=` | read:positions | |
| GET | `/backtests?limit=` | read:backtests | |
| GET | `/risk/limits` | read:risk | organisation + platform limits |
| GET | `/risk/events?limit=` | read:risk | append-only |
| GET | `/docs` | none | this reference |

`POST /signals` body:

```json
{"strategy_id": "ema_rsi_scalper_1m", "symbol": "RELIANCE", "direction": "LONG",
 "entry": 2500, "stop_loss": 2490, "target1": 2520, "target2": 2540,
 "mode": "PAPER", "idempotency_key": "my-alert-0001"}
```

The signal runs through the same path as a TradingView alert: kill switches, plan gate, risk
hierarchy, position sizing, order state machine, notifications. The same `idempotency_key`
replays the first outcome instead of placing again. `mode: LIVE` answers `409` - LIVE execution
is bound to a broker account through the console's deployments, not to a key.

## Errors

| Code | Meaning |
| --- | --- |
| 401 | missing, invalid, expired or revoked key; key owner deactivated |
| 402 | plan does not include the public API, or the organisation is suspended |
| 403 | key lacks the scope |
| 404 | no such resource in this organisation |
| 409 | conflict (LIVE via key, broker session not usable) |
| 422 | validation error |
| 429 | per-key per-minute limit, or the plan's daily allowance |

## Webhooks (outbound)

Configure a **WEBHOOK** alert channel under *Settings → Alert delivery*: an HTTPS URL, a shared
secret (16+ characters, never shown again) and optionally the event types to send. Every
notification at or above the channel's severity floor is POSTed as JSON:

```
POST <url>
Content-Type: application/json
X-ATP-Event: SYSTEM_FAILURE
X-ATP-Timestamp: 1758900000
X-ATP-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
```

Verify by recomputing the HMAC over `timestamp + "." + body` with the shared secret and
comparing in constant time; reject timestamps older than a few minutes to stop replays. A
non-2xx answer is recorded on the delivery (`GET /api/alert-deliveries`) and retried by the
outbox like Telegram and email.

### Signed webhooks per member (AUD-G2.5, flag `alert_webhooks`)

Each member adds his own receivers under *Settings → Alerts → Webhooks* (up to
`ALERT_WEBHOOKS_MAX_PER_USER`, default 5). The address must be `https://` and must resolve to
public addresses only (private, loopback, link-local, CGNAT and cloud-metadata addresses are
refused when it is added and again before every send; the connection is pinned to the checked
address and redirects are never followed). The **server** makes the secret (`whsec_...`) and
shows it **once**, in the answer to the add; it is stored only encrypted. A receiver gets only
notifications addressed to that member or to the whole organisation.

```
POST <url>
Content-Type: application/json
X-ATP-Schema: atp.webhook/1
X-ATP-Event: EXIT
X-ATP-Event-Id: evt_3f0c...            (also Idempotency-Key; the same on every retry)
X-ATP-Timestamp: 1793300000
X-ATP-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>

{"created_at":"...","event_id":"evt_3f0c...","event_type":"EXIT","notification_id":42,
 "schema":"atp.webhook/1","severity":"INFO","test":false,"text":"...","timestamp":1793300000,
 "title":"...","webhook_id":"a1b2c3d4e5f60718"}
```

The signature covers the raw body, and the body carries its own `timestamp` and `event_id`, so
neither can be changed without breaking the signature. A receiver should:

1. compute `HMAC-SHA256(secret, raw request body bytes)` - the bytes as received, before any
   JSON parsing - and compare `"sha256=" + hex` with `X-ATP-Signature` **in constant time**
   (`hmac.compare_digest` in Python, `crypto.timingSafeEqual` in Node.js; never `==`);
2. check that the body's `timestamp` equals `X-ATP-Timestamp` and is within 5 minutes of its
   own clock (refuses replays);
3. drop an `event_id` it has already processed (a retry after a timeout sends the same id).

```python
import hashlib, hmac, json, time

def verify(secret: str, raw: bytes, signature: str, timestamp: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    sent = int(json.loads(raw)["timestamp"])
    return sent == int(timestamp) and abs(time.time() - sent) <= 300
```

Answer any 2xx within `ALERT_WEBHOOKS_TIMEOUT_SECONDS` (default 10). Anything else (an error,
a timeout, a 3xx) is retried by the outbox with backoff and, after the last attempt, kept as a
dead letter that can be retried from *Settings → Alerts*. *Send test* posts one signed message
with `"test": true` and shows the receiver's answer. The legacy **WEBHOOK** channel above keeps
its own `"<timestamp>.<raw body>"` signature unchanged.

## Versioning

`v1` is stable: fields are added, never renamed or removed, within v1. A breaking change ships
as `/api/public/v2` with both versions served side by side and a deprecation window announced
in `GET /docs` → `changelog`.
