"""Runner S3: the device signature (Ed25519, design section 4) - every runner -> ATP request after pairing is signed by the
device's own key; ATP holds only the public key.

A signed request carries three headers:
- `X-Runner-Device`: the device id ATP gave at pairing (32 hex);
- `X-Runner-Timestamp`: the device clock in milliseconds since the epoch;
- `X-Runner-Signature`: base64url Ed25519 signature of `canonical(...)` below.
ATP accepts a request when the signature verifies, the timestamp is within REPLAY_WINDOW_SECONDS of the server clock, and
it is newer than the device's last accepted request (`runner_devices.last_signed_ms`) - so a captured request can never
be sent again, and an old one never late.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

HEADER_DEVICE = "X-Runner-Device"
HEADER_TIMESTAMP = "X-Runner-Timestamp"
HEADER_SIGNATURE = "X-Runner-Signature"
# Clock drift allowed between the user's machine and ATP (operational; the runner keeps its clock with NTP).
REPLAY_WINDOW_SECONDS = 120
_SCHEME = b"ATP-RUNNER-1"
_PAIR_SCHEME = b"ATP-RUNNER-PAIR-1"


def canonical(method: str, path: str, timestamp_ms: int, device_uid: str, body: bytes) -> bytes:
    """The bytes a device signs for one request: scheme, method, path, timestamp, device id and the body's SHA-256."""
    return b"\n".join((_SCHEME, method.upper().encode(), path.encode(), str(int(timestamp_ms)).encode(), device_uid.encode(),
                       hashlib.sha256(body).hexdigest().encode()))


def pair_message(code: str, public_key_hex: str) -> bytes:
    """What a new device signs at pairing (proof that it holds the private key of the public key it presents)."""
    return b"\n".join((_PAIR_SCHEME, code.encode(), public_key_hex.lower().encode()))


def load_public_key(public_key_hex: str) -> Ed25519PublicKey | None:
    """The 32-byte raw Ed25519 public key from 64 hex characters, or None when it is not one."""
    try:
        raw = bytes.fromhex(public_key_hex)
    except ValueError:
        return None
    if len(raw) != 32:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        return None


def _b64(signature: str) -> bytes | None:
    text = (signature or "").strip()
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def verify(public_key_hex: str, signature_b64: str, message: bytes) -> bool:
    key = load_public_key(public_key_hex)
    signature = _b64(signature_b64)
    if key is None or signature is None or len(signature) != 64:
        return False
    try:
        key.verify(signature, message)
    except InvalidSignature:
        return False
    return True


def within_window(timestamp_ms: int, now_ms: int) -> bool:
    return abs(int(now_ms) - int(timestamp_ms)) <= REPLAY_WINDOW_SECONDS * 1000
