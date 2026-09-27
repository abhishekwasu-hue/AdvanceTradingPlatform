"""Secret-at-rest encryption (spec section 48).

Two layers:

* **Master key** - `SECRETS_ENCRYPTION_KEY` (a Fernet key or any passphrase, see
  `_derive_fernet_key`). Before Phase N every secret was encrypted directly under it, and those
  legacy tokens still decrypt unchanged.
* **Per-tenant data keys** (Phase N1, `app/secrets_store/envelope.py`) - each tenant has its own
  random Fernet key, stored *wrapped* by the master key in `tenant_keys`. New ciphertexts are
  written as `t1:<tenant_id>:<token>` under the tenant's key. Rotating the master key then means
  re-wrapping a few hundred small keys instead of re-encrypting every credential row, and a bug
  that leaks one tenant's key cannot decrypt another tenant's secrets.

`encrypt_text(plaintext, tenant_id)` picks the tenant key when it is loaded in the in-process key
ring (see `envelope.ensure_tenant_key`, called on login, per worker tenant and at startup) and
falls back to the master key otherwise - a write never fails because a key is not warm; it just
lands in the legacy format, which the re-encrypt script (`scripts/reencrypt_secrets.py`) upgrades.
"""
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import SECRETS_ENCRYPTION_KEY

logger = logging.getLogger(__name__)

TENANT_PREFIX = "t1:"


def _derive_fernet_key(raw_key: Optional[str] = None) -> bytes:
    """A Fernet key must be 32 url-safe base64 bytes. `SECRETS_ENCRYPTION_KEY` can be set to
    exactly that, or to any passphrase (hashed down to the right size here) - either way, in
    any real deployment it must come from a secret manager and stay stable across restarts.
    Falling back to a fixed dev-only value keeps local development working without one, but
    anything encrypted under it is not meant to survive past a dev session.
    """
    if raw_key:
        raw = raw_key.encode()
        try:
            Fernet(raw)
            return raw
        except (ValueError, TypeError):
            pass
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    digest = hashlib.sha256(b"dev-only-insecure-encryption-key-change-me").digest()
    return base64.urlsafe_b64encode(digest)


def fernet_for_key(raw_key: Optional[str]) -> Fernet:
    """The master Fernet for an arbitrary configured key (used by the rotation script for the
    *previous* master)."""
    return Fernet(_derive_fernet_key(raw_key))


_fernet = fernet_for_key(SECRETS_ENCRYPTION_KEY)


def master_fernet() -> Fernet:
    return _fernet


def encrypt_text(plaintext: str, tenant_id: Optional[int] = None) -> str:
    """Encrypts under the tenant's data key when one is loaded, else under the master key."""
    if tenant_id is not None:
        from app.secrets_store import envelope
        tenant_fernet = envelope.key_ring.get(tenant_id)
        if tenant_fernet is not None:
            return f"{TENANT_PREFIX}{tenant_id}:{tenant_fernet.encrypt(plaintext.encode()).decode()}"
        logger.warning("Tenant %s data key not loaded - encrypting under the master key", tenant_id)
    return _fernet.encrypt(plaintext.encode()).decode()


def is_tenant_encrypted(ciphertext: str) -> bool:
    return ciphertext.startswith(TENANT_PREFIX)


def parse_tenant_token(ciphertext: str) -> tuple[Optional[int], str]:
    """`t1:<tenant_id>:<token>` -> (tenant_id, token); a legacy token -> (None, token)."""
    if not ciphertext.startswith(TENANT_PREFIX):
        return None, ciphertext
    try:
        _, tenant_part, token = ciphertext.split(":", 2)
        return int(tenant_part), token
    except ValueError as exc:
        raise ValueError("Malformed tenant-encrypted secret") from exc


def decrypt_text(ciphertext: str) -> str:
    tenant_id, token = parse_tenant_token(ciphertext)
    if tenant_id is None:
        try:
            return _fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Could not decrypt stored credentials - wrong or rotated encryption key") from exc
    from app.secrets_store import envelope
    tenant_fernet = envelope.key_ring.get(tenant_id)
    if tenant_fernet is None:
        raise ValueError(f"Data key for tenant {tenant_id} is not loaded - call envelope.ensure_tenant_key first")
    try:
        return tenant_fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored credentials - tenant data key does not match") from exc
