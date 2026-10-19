"""Encryption of user-supplied AI provider API keys (envelope-style, AES-256-GCM).

Design
* A 256-bit **master key** encrypts every stored credential. It is never stored in the database.
  - Production: `AMW_SECRET_KEY` (urlsafe-base64, 32 bytes) supplied by the deployment's secret manager.
  - Desktop/single-user: if unset, a key is generated once into `data/.amw_master.key` (file mode 0600; the `data/`
    directory is 0700 and unreadable by the Strategy Lab sandbox user). This protects database copies/backups; it does
    not protect against an attacker who controls the host account.
* Each ciphertext uses a fresh 96-bit nonce and **associated data = "amw-ai|<user_id>|<provider>|<key_id>"**, so a
  ciphertext cannot be decrypted for another user or provider (row swapping fails authentication).
* `key_id` (first 8 hex chars of SHA-256 of the master key) is stored with each row to support master-key rotation.
Plaintext keys exist only in backend memory for the duration of a request.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoError(RuntimeError):
    pass


def _key_file() -> Path:
    from ..config import ROOT
    return ROOT / "data" / ".amw_master.key"


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def load_master_key() -> bytes:
    raw = os.environ.get("AMW_SECRET_KEY")
    if not raw:
        f = _key_file()
        f.parent.mkdir(exist_ok=True)
        if not f.exists():
            fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as h:
                h.write(generate_master_key())
        raw = f.read_text().strip()
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as e:  # noqa: BLE001
        raise CryptoError("AMW_SECRET_KEY is not valid urlsafe base64") from e
    if len(key) != 32:
        raise CryptoError("AMW_SECRET_KEY must decode to exactly 32 bytes")
    return key


def key_id(master: bytes) -> str:
    return hashlib.sha256(master).hexdigest()[:8]


def _aad(user_id: int, provider: str, kid: str) -> bytes:
    return f"amw-ai|{int(user_id)}|{provider}|{kid}".encode()


def encrypt(plaintext: str, user_id: int, provider: str, master: bytes | None = None) -> tuple[bytes, str]:
    m = master or load_master_key()
    kid = key_id(m)
    nonce = os.urandom(12)
    ct = AESGCM(m).encrypt(nonce, plaintext.encode(), _aad(user_id, provider, kid))
    return nonce + ct, kid


def decrypt(blob: bytes, user_id: int, provider: str, kid: str, master: bytes | None = None) -> str:
    m = master or load_master_key()
    if key_id(m) != kid:
        raise CryptoError("credential was encrypted with a different master key (rotate or re-enter the key)")
    try:
        return AESGCM(m).decrypt(blob[:12], blob[12:], _aad(user_id, provider, kid)).decode()
    except Exception as e:  # noqa: BLE001
        raise CryptoError("credential could not be decrypted (tampered or not owned by this user/provider)") from e
