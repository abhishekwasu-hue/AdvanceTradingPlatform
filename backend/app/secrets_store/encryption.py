"""Secret-at-rest encryption (spec section 48; P0.4 / S11).

Three layers:

* **Master key** - `SECRETS_ENCRYPTION_KEY`: a Fernet key, or any passphrase. A passphrase yields two masters
  (`MultiFernet`: first key encrypts, every key decrypts): the SHA-256 derivation Phase N used and a
  scrypt-stretched one (P0.4). Which one *encrypts* follows `SECRETS_WRITE_FORMAT`: while it is `fernet` (the
  rollback-safe default) new wraps stay under SHA-256 so the previous image can still open them; once it is
  `aesgcm` the scrypt key takes over and the SHA-256 one is decrypt-only. Before Phase N every secret was
  encrypted directly under the master; those legacy tokens still decrypt unchanged.
* **Per-tenant data keys** (Phase N1, `app/secrets_store/envelope.py`) - one random key per tenant, stored
  *wrapped* by the master in `tenant_keys`. `t1:<tenant_id>:<fernet token>` is the Phase N format.
* **AES-256-GCM with associated data** (P0.4) - `t2:<tenant_id>:<purpose>:<base64(nonce || ciphertext)>`.
  The key is HKDF-derived from the tenant data key; the associated data binds the ciphertext to the tenant
  *and* the column it belongs to (`purpose`, e.g. `broker_credential`), so a row copied into another tenant
  or another column fails to open instead of decrypting. `SECRETS_WRITE_FORMAT` chooses what new writes use
  (`fernet` by default this release so an image rollback never strands a secret; `aesgcm` once the fleet runs
  P0.4). Every format is always read; `scripts/reencrypt_secrets.py reencrypt` converts existing rows.

`encrypt_text(plaintext, tenant_id, purpose)` picks the tenant key when it is loaded in the in-process key ring
(see `envelope.ensure_tenant_key`, called on login, per worker tenant and at startup) and falls back to the
master key otherwise - a write never fails because a key is not warm; it lands in the legacy format, which the
re-encrypt script upgrades. `decrypt_text(ciphertext, purpose)` needs the same purpose for a `t2` token.
"""
import base64
import hashlib
import logging
import os
from typing import Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core import config
from app.core.config import SECRETS_ENCRYPTION_KEY

logger = logging.getLogger(__name__)

TENANT_PREFIX = "t1:"
AEAD_PREFIX = "t2:"
FORMAT_FERNET = "fernet"
FORMAT_AESGCM = "aesgcm"
_SCRYPT_SALT = b"atp-secrets-master-v2"       # domain separation only: the passphrase itself is the secret
_HKDF_INFO = b"atp-secrets-aesgcm-v2"
_NONCE_BYTES = 12
_DEV_PASSPHRASE = b"dev-only-insecure-encryption-key-change-me"


def _sha256_key(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _scrypt_key(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(hashlib.scrypt(raw, salt=_SCRYPT_SALT, n=2 ** 14, r=8, p=1, dklen=32))


def write_format() -> str:
    value = (getattr(config, "SECRETS_WRITE_FORMAT", FORMAT_FERNET) or FORMAT_FERNET).lower()
    return FORMAT_AESGCM if value == FORMAT_AESGCM else FORMAT_FERNET


def _derive_fernet_keys(raw_key: Optional[str] = None, *, scrypt_first: Optional[bool] = None) -> Tuple[bytes, ...]:
    """The master key material, encrypting key first. A proper Fernet key is used as is. A passphrase yields
    Phase N's SHA-256 derivation and the scrypt-stretched key; the scrypt key leads only once the operator has
    moved to `SECRETS_WRITE_FORMAT=aesgcm` (so a rollback inside the default window still opens every new wrap).
    Without a key the fixed dev-only value is used (refused by the production/staging configuration checks)."""
    if raw_key:
        raw = raw_key.encode()
        try:
            Fernet(raw)
            return (raw,)
        except (ValueError, TypeError):
            pass
        lead_scrypt = (write_format() == FORMAT_AESGCM) if scrypt_first is None else scrypt_first
        return (_scrypt_key(raw), _sha256_key(raw)) if lead_scrypt else (_sha256_key(raw), _scrypt_key(raw))
    return (_sha256_key(_DEV_PASSPHRASE),)


def _derive_fernet_key(raw_key: Optional[str] = None) -> bytes:
    """Kept for callers that want the single *encrypting* master key."""
    return _derive_fernet_keys(raw_key)[0]


def fernet_for_key(raw_key: Optional[str]) -> MultiFernet:
    """The master for an arbitrary configured key (the rotation script uses it for the *previous* master)."""
    return MultiFernet([Fernet(k) for k in _derive_fernet_keys(raw_key)])


_masters: dict = {}


def master_fernet() -> MultiFernet:
    """The configured master. Built per write format so a passphrase master's encrypting key follows the
    operator's rollout (SHA-256 while `fernet`, scrypt once `aesgcm`); every derivation always decrypts."""
    key = write_format()
    cached = _masters.get(key)
    if cached is None:
        cached = _masters[key] = fernet_for_key(SECRETS_ENCRYPTION_KEY)
    return cached


def aead_key_from_data_key(data_key: bytes, tenant_id: int) -> AESGCM:
    """AES-256-GCM key for one tenant, derived (HKDF-SHA256) from its Fernet data key so no new key material
    has to be stored or wrapped."""
    raw = base64.urlsafe_b64decode(data_key)
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO + b"|" + str(tenant_id).encode()).derive(raw)
    return AESGCM(derived)


def _aad(tenant_id: int, purpose: str) -> bytes:
    return f"{tenant_id}|{purpose}".encode()


def _clean_purpose(purpose: Optional[str]) -> str:
    value = (purpose or "").strip()
    if not value or ":" in value or "|" in value:
        raise ValueError("A purpose (column label without ':' or '|') is required for AES-GCM secrets")
    return value


def encrypt_text(plaintext: str, tenant_id: Optional[int] = None, purpose: Optional[str] = None) -> str:
    """Encrypts under the tenant's data key when one is loaded (format per SECRETS_WRITE_FORMAT), else under
    the master key (legacy format)."""
    if tenant_id is not None:
        from app.secrets_store import envelope
        tenant_fernet = envelope.key_ring.get(tenant_id)
        if tenant_fernet is not None:
            if write_format() == FORMAT_AESGCM and purpose:
                label = _clean_purpose(purpose)
                aead = envelope.aead_for(tenant_id)
                nonce = os.urandom(_NONCE_BYTES)
                sealed = aead.encrypt(nonce, plaintext.encode(), _aad(tenant_id, label))
                return f"{AEAD_PREFIX}{tenant_id}:{label}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"
            return f"{TENANT_PREFIX}{tenant_id}:{tenant_fernet.encrypt(plaintext.encode()).decode()}"
        logger.warning("Tenant %s data key not loaded - encrypting under the master key", tenant_id)
    return master_fernet().encrypt(plaintext.encode()).decode()


def is_tenant_encrypted(ciphertext: str) -> bool:
    return ciphertext.startswith(TENANT_PREFIX) or ciphertext.startswith(AEAD_PREFIX)


def is_aead_encrypted(ciphertext: str) -> bool:
    return ciphertext.startswith(AEAD_PREFIX)


def format_of(ciphertext: Optional[str]) -> str:
    """'aesgcm', 'fernet' (tenant key) or 'legacy' (master key)."""
    if not ciphertext:
        return "empty"
    if ciphertext.startswith(AEAD_PREFIX):
        return FORMAT_AESGCM
    if ciphertext.startswith(TENANT_PREFIX):
        return FORMAT_FERNET
    return "legacy"


def needs_rewrite(ciphertext: Optional[str]) -> bool:
    """True when a stored secret is not in the configured write format (the re-encrypt script's test)."""
    if not ciphertext:
        return False
    current = format_of(ciphertext)
    return current == "legacy" or (write_format() == FORMAT_AESGCM and current == FORMAT_FERNET)


def parse_tenant_token(ciphertext: str) -> tuple[Optional[int], str]:
    """`t1:<tenant_id>:<token>` -> (tenant_id, token); a legacy token -> (None, token)."""
    if not ciphertext.startswith(TENANT_PREFIX):
        return None, ciphertext
    try:
        _, tenant_part, token = ciphertext.split(":", 2)
        return int(tenant_part), token
    except ValueError as exc:
        raise ValueError("Malformed tenant-encrypted secret") from exc


def _parse_aead_token(ciphertext: str) -> tuple[int, str, bytes]:
    try:
        _, tenant_part, label, blob = ciphertext.split(":", 3)
        return int(tenant_part), label, base64.urlsafe_b64decode(blob)
    except (ValueError, TypeError) as exc:
        raise ValueError("Malformed AES-GCM secret") from exc


def _check_owner(embedded: int, owner: Optional[int]) -> None:
    if owner is not None and int(owner) != int(embedded):
        raise ValueError(f"Stored secret belongs to organisation {embedded}, not {owner} - refusing to decrypt")


def decrypt_text(ciphertext: str, purpose: Optional[str] = None, tenant_id: Optional[int] = None) -> str:
    """`tenant_id` is the organisation that *owns the row* being read; when given, a token minted for another
    organisation (even copied verbatim) is refused, for the `t1` and `t2` formats alike."""
    if ciphertext.startswith(AEAD_PREFIX):
        embedded, label, blob = _parse_aead_token(ciphertext)
        _check_owner(embedded, tenant_id)
        tenant_id = embedded
        expected = _clean_purpose(purpose)
        if label != expected:
            raise ValueError(f"Stored secret belongs to '{label}', not '{expected}' - refusing to decrypt")
        from app.secrets_store import envelope
        if envelope.key_ring.get(tenant_id) is None:
            raise ValueError(f"Data key for tenant {tenant_id} is not loaded - call envelope.ensure_tenant_key first")
        if len(blob) <= _NONCE_BYTES:
            raise ValueError("Malformed AES-GCM secret")
        try:
            return envelope.aead_for(tenant_id).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], _aad(tenant_id, expected)).decode()
        except InvalidTag as exc:
            raise ValueError("Could not decrypt stored credentials - tenant data key or binding does not match") from exc
    embedded, token = parse_tenant_token(ciphertext)
    if embedded is None:
        try:
            return master_fernet().decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Could not decrypt stored credentials - wrong or rotated encryption key") from exc
    _check_owner(embedded, tenant_id)
    tenant_id = embedded
    from app.secrets_store import envelope
    tenant_fernet = envelope.key_ring.get(tenant_id)
    if tenant_fernet is None:
        raise ValueError(f"Data key for tenant {tenant_id} is not loaded - call envelope.ensure_tenant_key first")
    try:
        return tenant_fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored credentials - tenant data key does not match") from exc
