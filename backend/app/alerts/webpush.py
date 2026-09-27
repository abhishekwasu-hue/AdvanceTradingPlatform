"""Phase O3: Web Push without a third-party service.

Browsers deliver push messages through their vendor's push service (Mozilla, Google, Apple) to a
service worker; the platform only needs the two IETF standards the browsers implement:

* **RFC 8291 message encryption** (`aes128gcm`): an ephemeral P-256 ECDH agreement with the
  subscription's `p256dh` key, HKDF with the subscription's `auth` secret, AES-128-GCM. The push
  service never sees the plaintext.
* **RFC 8292 VAPID**: an ES256 JWT signed with the platform's VAPID private key identifies this
  server to the push service, so a leaked subscription URL cannot be spammed by someone else.

The VAPID key pair is platform configuration (`VAPID_PRIVATE_KEY`, base64url of the 32-byte raw
scalar; `VAPID_SUBJECT`, a mailto: or https: contact). Without one, an ephemeral pair is generated
at start-up with a warning: push works in development, but every restart invalidates the
browsers' subscriptions. `python -m app.alerts.webpush` prints a fresh pair for `.env`.

Implemented with `cryptography` directly so no extra dependency carries a native build.
"""
import base64
import json
import logging
import os
import struct
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core import config

logger = logging.getLogger(__name__)

RECORD_SIZE = 4096
DEFAULT_TTL = 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load_private_key(raw: Optional[str]) -> ec.EllipticCurvePrivateKey:
    if raw:
        raw = raw.strip()
        if raw.startswith("-----"):
            return serialization.load_pem_private_key(raw.encode(), password=None)  # type: ignore[return-value]
        return ec.derive_private_key(int.from_bytes(b64url_decode(raw), "big"), ec.SECP256R1())
    logger.warning("VAPID_PRIVATE_KEY is not set - using an ephemeral Web Push key; browser subscriptions will not survive a restart")
    return ec.generate_private_key(ec.SECP256R1())


_private_key = _load_private_key(getattr(config, "VAPID_PRIVATE_KEY", None))


def public_key_bytes(key: Optional[ec.EllipticCurvePrivateKey] = None) -> bytes:
    """Uncompressed point (65 bytes) - what `applicationServerKey` and the JWT `k` parameter carry."""
    return (key or _private_key).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key_b64() -> str:
    return b64url(public_key_bytes())


def configured() -> bool:
    return bool(getattr(config, "VAPID_PRIVATE_KEY", None))


def generate_keypair() -> Dict[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return {"VAPID_PRIVATE_KEY": b64url(raw), "VAPID_PUBLIC_KEY": b64url(public_key_bytes(key))}


# --- RFC 8291 -------------------------------------------------------------------------------------

def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(plaintext: bytes, client_p256dh: str, client_auth: str, *, salt: Optional[bytes] = None,
                    server_key: Optional[ec.EllipticCurvePrivateKey] = None) -> bytes:
    """`aes128gcm` content-coding body: salt(16) | rs(4) | idlen(1) | server public key(65) | ciphertext."""
    client_pub_raw = b64url_decode(client_p256dh)
    auth_secret = b64url_decode(client_auth)
    if len(client_pub_raw) != 65 or len(auth_secret) != 16:
        raise ValueError("Malformed push subscription keys")
    client_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_pub_raw)
    ephemeral = server_key or ec.generate_private_key(ec.SECP256R1())
    server_pub_raw = public_key_bytes(ephemeral)
    shared = ephemeral.exchange(ec.ECDH(), client_pub)
    salt = salt or os.urandom(16)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + client_pub_raw + server_pub_raw, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(plaintext) + 17 > RECORD_SIZE:
        raise ValueError("Push payload too large for a single record")
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)   # 0x02 = last record delimiter
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(server_pub_raw)]) + server_pub_raw
    return header + ciphertext


def decrypt_payload(body: bytes, client_private: ec.EllipticCurvePrivateKey, client_auth: str) -> bytes:
    """The receiver's side of RFC 8291 - used by the tests to prove the format round-trips."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    server_pub_raw = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    server_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_pub_raw)
    client_pub_raw = public_key_bytes(client_private)
    shared = client_private.exchange(ec.ECDH(), server_pub)
    ikm = _hkdf(b64url_decode(client_auth), shared, b"WebPush: info\x00" + client_pub_raw + server_pub_raw, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert rs == RECORD_SIZE
    return padded.rstrip(b"\x00")[:-1]


# --- RFC 8292 -------------------------------------------------------------------------------------

def vapid_authorization(endpoint: str, *, subject: Optional[str] = None, expires_in: int = 12 * 3600, now: Optional[int] = None) -> str:
    """`vapid t=<ES256 JWT>, k=<public key>` for the push service that owns `endpoint`."""
    parsed = urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": audience, "exp": int(now or time.time()) + expires_in, "sub": subject or getattr(config, "VAPID_SUBJECT", "mailto:ops@example.com")}
    payload = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    der = _private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der)
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{payload}.{signature}, k={public_key_b64()}"


class PushGone(Exception):
    """The push service says this subscription no longer exists (HTTP 404/410): drop it."""


async def send_push(endpoint: str, p256dh: str, auth: str, payload: dict, *, ttl: int = DEFAULT_TTL,
                    client: Optional[httpx.AsyncClient] = None, urgency: str = "high") -> None:
    body = encrypt_payload(json.dumps(payload, separators=(",", ":")).encode(), p256dh, auth)
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
               "Urgency": urgency, "Authorization": vapid_authorization(endpoint)}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(endpoint, content=body, headers=headers)
    finally:
        if owns:
            await client.aclose()
    if response.status_code in (404, 410):
        raise PushGone(f"subscription gone (HTTP {response.status_code})")
    if response.status_code >= 300:
        raise RuntimeError(f"Push service answered HTTP {response.status_code}: {response.text[:120]}")


if __name__ == "__main__":
    for key, value in generate_keypair().items():
        print(f"{key}={value}")
