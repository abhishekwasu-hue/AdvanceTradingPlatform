import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import SECRETS_ENCRYPTION_KEY


def _derive_fernet_key() -> bytes:
    """A Fernet key must be 32 url-safe base64 bytes. `SECRETS_ENCRYPTION_KEY` can be set to
    exactly that, or to any passphrase (hashed down to the right size here) - either way, in
    any real deployment it must come from a secret manager and stay stable across restarts.
    Falling back to a fixed dev-only value keeps local development working without one, but
    anything encrypted under it is not meant to survive past a dev session.
    """
    if SECRETS_ENCRYPTION_KEY:
        raw = SECRETS_ENCRYPTION_KEY.encode()
        try:
            Fernet(raw)
            return raw
        except (ValueError, TypeError):
            pass
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    digest = hashlib.sha256(b"dev-only-insecure-encryption-key-change-me").digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_fernet_key())


def encrypt_text(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored credentials - wrong or rotated encryption key") from exc
