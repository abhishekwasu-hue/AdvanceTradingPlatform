"""Runner S5: the local encrypted keystore (docs/design/USER_RUNNER.md section 5, step S5).

The user's broker details - API key, client code, PIN, TOTP secret - and the day's broker session tokens live ONLY on his
runner, in ONE encrypted file of the runner's folder. Nothing here is ever sent to ATP, printed or logged: the CLI
(`keys.py`) prompts for secrets without echo and lists only which fields are set; errors are fixed sentences.

File format (`<home>/keystore.enc`, mode 600, written atomically): one JSON object
    {"format": "atp-runner-keystore", "v": 1, "kdf": {...}, "nonce": b64, "ct": b64}
`ct` is AES-256-GCM (the `cryptography` package the backend already uses) over the JSON of the store; the header (format,
version, kdf) is the associated data, so a changed header fails like a changed ciphertext. A fresh 96-bit nonce per write.

Where the 256-bit key comes from (`kdf.name`), chosen at `keys init`:
- "keyfile" (default): 32 random bytes in `<home>/keystore.key`, mode 600. Why the default: the pilot runner is a small
  VPS started by `restart: always` and must log in before the open with nobody at the keyboard, so nothing may need to be
  typed at a restart. It protects a copied / backed-up keystore file; whoever can read the runner's folder as the runner's
  user can read both files (the same as any unattended secret on that machine: keep that user's login to yourself).
- "scrypt": the key derived from a passphrase (scrypt, n=2**15, r=8, p=1, 16-byte random salt). Nothing on the disk opens
  the file; the passphrase is asked at each start, so it suits a machine where the user starts the runner himself.
  scrypt rather than Argon2id: both are in `cryptography`, scrypt is in every version the backend allows and is a
  standard memory-hard KDF; no new dependency.

Both files must be readable by their owner only: a file with any group / other permission is refused (as ssh does),
and every write creates the file with mode 600 and the folder with mode 700. A read-modify-write holds an exclusive
lock on `<home>/keystore.lock` (POSIX), so the CLI and the running runner never lose each other's change.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "atp-runner-keystore"
VERSION = 1
STORE_FILE = "keystore.enc"
KEY_FILE = "keystore.key"
LOCK_FILE = "keystore.lock"
KEYFILE, SCRYPT = "keyfile", "scrypt"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
MIN_PASSPHRASE = 10

# The broker details the keystore accepts, per broker (RUN-Q3: Angel One first). `SECRET_FIELDS` are prompted without
# echo; the others (client code, the static IP sent in Angel One's identification header) are not secret.
BROKER_FIELDS: dict[str, tuple[str, ...]] = {
    "angel_one": ("api_key", "client_id", "pin", "totp_secret", "public_ip"),
}
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "angel_one": ("api_key", "client_id", "pin", "totp_secret"),
}
SECRET_FIELDS: frozenset[str] = frozenset({"api_key", "api_secret", "pin", "totp_secret"})
SESSION_FIELDS: tuple[str, ...] = ("access_token", "refresh_token", "feed_token")


class KeystoreError(Exception):
    """A keystore problem, as one plain sentence that never carries a value."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _private(path: Path) -> None:
    """Refuses a file that its group or others may read or write (POSIX; Windows has no such bits)."""
    if os.name == "nt":
        return
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise KeystoreError(f"{path} must be readable by you only (chmod 600 {path})")


def _write_private(path: Path, data: bytes) -> None:
    """Atomic write of a mode-600 file: a temporary file created 600 in the same folder, fsynced, then renamed."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _header_aad(header: dict[str, Any]) -> bytes:
    return json.dumps({k: header[k] for k in ("format", "v", "kdf")}, sort_keys=True, separators=(",", ":")).encode()


def _scrypt_key(passphrase: str, kdf: dict[str, Any]) -> bytes:
    return Scrypt(salt=_unb64(kdf["salt"]), length=32, n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"])).derive(
        passphrase.encode("utf-8"))


class Keystore:
    """The runner's encrypted store of broker details and sessions. `passphrase` is only for an "scrypt" keystore: a
    string, or a callable asked once when the key is first needed (the CLI's hidden prompt)."""

    def __init__(self, home: str | os.PathLike[str], passphrase: str | Callable[[], str] | None = None) -> None:
        self.home = Path(home).expanduser()
        self._passphrase = passphrase
        self._key: Optional[bytes] = None

    def __repr__(self) -> str:                      # never a key or a value
        return f"Keystore({str(self.home)!r})"

    # --- files -------------------------------------------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.home / STORE_FILE

    @property
    def key_path(self) -> Path:
        return self.home / KEY_FILE

    def exists(self) -> bool:
        return self.path.exists()

    def _ensure_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.home, stat.S_IMODE(self.home.stat().st_mode) & ~0o077)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_home()
        fd = os.open(self.home / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                import fcntl
            except ImportError:                     # Windows: single writer assumed
                yield
                return
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _header(self) -> dict[str, Any]:
        if not self.path.exists():
            raise KeystoreError("No keystore on this runner yet: run `python -m app.runner.keys init` first")
        _private(self.path)
        try:
            header = json.loads(self.path.read_text())
            assert header["format"] == FORMAT and int(header["v"]) == VERSION and header["kdf"]["name"] in (KEYFILE, SCRYPT)
        except Exception:  # noqa: BLE001 - the file is not a keystore of this version
            raise KeystoreError(f"{self.path} is not a runner keystore this version can read") from None
        return header

    def mode(self) -> str:
        return str(self._header()["kdf"]["name"])

    def _ask_passphrase(self) -> str:
        value = self._passphrase() if callable(self._passphrase) else self._passphrase
        if not value:
            raise KeystoreError("This keystore is locked with a passphrase: give it to unlock (it is asked at each start)")
        return value

    def _key_for(self, header: dict[str, Any]) -> bytes:
        if self._key is not None:
            return self._key
        kdf = header["kdf"]
        if kdf["name"] == KEYFILE:
            if not self.key_path.exists():
                raise KeystoreError(f"The key file {self.key_path} is missing: the keystore cannot be opened without it")
            _private(self.key_path)
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise KeystoreError(f"The key file {self.key_path} is damaged")
        else:
            key = _scrypt_key(self._ask_passphrase(), kdf)
        self._key = key
        return key

    # --- create / read / write ---------------------------------------------------------------------------------------
    def init(self, mode: str = KEYFILE, passphrase: Optional[str] = None) -> None:
        """Creates an empty keystore (refuses to overwrite one: `rekey` changes the key of an existing one)."""
        with self._locked():
            if self.path.exists():
                raise KeystoreError("A keystore already exists on this runner (use `rekey` to change its key)")
            self._new_key(mode, passphrase)
            self._write({"brokers": {}, "sessions": {}}, self._pending_header)
            self._commit_key()

    def _new_key(self, mode: str, passphrase: Optional[str]) -> None:
        self._pending_key_file: Optional[Path] = None
        if mode == KEYFILE:
            key = secrets.token_bytes(32)
            # written beside the old one and moved in only after the store is written under it (`_commit_key`)
            self._pending_key_file = self.key_path.with_name(KEY_FILE + ".new")
            _write_private(self._pending_key_file, key)
            kdf: dict[str, Any] = {"name": KEYFILE}
        elif mode == SCRYPT:
            if not passphrase or len(passphrase) < MIN_PASSPHRASE:
                raise KeystoreError(f"Choose a passphrase of at least {MIN_PASSPHRASE} characters")
            kdf = {"name": SCRYPT, "salt": _b64(secrets.token_bytes(16)), "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
            key = _scrypt_key(passphrase, kdf)
            self._passphrase = passphrase
        else:
            raise KeystoreError("The keystore mode is `keyfile` or `scrypt`")
        self._key = key
        self._pending_header = {"format": FORMAT, "v": VERSION, "kdf": kdf}

    def _commit_key(self) -> None:
        if self._pending_key_file is not None:
            os.replace(self._pending_key_file, self.key_path)
            self._pending_key_file = None

    def _write(self, data: dict[str, Any], header: dict[str, Any]) -> None:
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self._key_for(header)).encrypt(nonce, json.dumps(data).encode("utf-8"), _header_aad(header))
        _write_private(self.path, json.dumps({**header, "nonce": _b64(nonce), "ct": _b64(ct)}).encode("utf-8"))

    def _read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        header = self._header()
        try:
            nonce, ct = _unb64(header["nonce"]), _unb64(header["ct"])
        except Exception:  # noqa: BLE001
            raise KeystoreError(f"{self.path} is damaged") from None
        try:
            plain = AESGCM(self._key_for(header)).decrypt(nonce, ct, _header_aad(header))
        except InvalidTag:
            self._key = None
            if header["kdf"]["name"] == SCRYPT:
                raise KeystoreError("Wrong passphrase, or the keystore file was changed") from None
            raise KeystoreError("The key file does not open this keystore, or the keystore file was changed") from None
        data = json.loads(plain)
        data.setdefault("brokers", {})
        data.setdefault("sessions", {})
        return data, {k: header[k] for k in ("format", "v", "kdf")}

    @contextlib.contextmanager
    def _edit(self) -> Iterator[dict[str, Any]]:
        with self._locked():
            data, header = self._read()
            yield data
            self._write(data, header)

    def rekey(self, mode: str = KEYFILE, passphrase: Optional[str] = None) -> None:
        """Re-encrypts the store under a new key (a new key file, or a new passphrase); the old key no longer opens it."""
        with self._locked():
            data, _ = self._read()
            old_key_file = self.key_path.exists()
            self._key = None
            self._new_key(mode, passphrase)
            self._write(data, self._pending_header)
            self._commit_key()
            if mode == SCRYPT and old_key_file:
                self.key_path.unlink()

    # --- broker details ----------------------------------------------------------------------------------------------
    def brokers(self) -> dict[str, list[str]]:
        """{broker: [field names that are set]} - names only, never a value."""
        data, _ = self._read()
        return {b: sorted(k for k, v in fields.items() if v) for b, fields in data["brokers"].items()}

    def put_broker(self, broker: str, fields: Mapping[str, Optional[str]], *, replace: bool) -> None:
        """`replace` True: the broker's details become exactly `fields` (add). False: only the given fields change
        (rotate). Any change of details ends the stored session (the next login uses the new details)."""
        allowed = BROKER_FIELDS.get(broker)
        if allowed is None:
            raise KeystoreError(f"The runner keeps details for: {', '.join(sorted(BROKER_FIELDS))}")
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeystoreError(f"Unknown field(s) for {broker}: {', '.join(sorted(unknown))}")
        with self._edit() as data:
            current = {} if replace else dict(data["brokers"].get(broker) or {})
            for name, value in fields.items():
                value = (value or "").strip()
                if value:
                    current[name] = value
                elif replace:
                    current.pop(name, None)
            missing = [f for f in REQUIRED_FIELDS.get(broker, ()) if not current.get(f)]
            if missing:
                raise KeystoreError(f"{broker} needs: {', '.join(missing)}")
            data["brokers"][broker] = current
            data["sessions"].pop(broker, None)

    def remove_broker(self, broker: str) -> bool:
        with self._edit() as data:
            had = broker in data["brokers"] or broker in data["sessions"]
            data["brokers"].pop(broker, None)
            data["sessions"].pop(broker, None)
        return had

    def broker_fields(self, broker: str) -> dict[str, str]:
        """The stored details of one broker (in memory only: for building the adapter's credentials)."""
        data, _ = self._read()
        fields = data["brokers"].get(broker)
        if not fields:
            raise KeystoreError(f"No {broker} details in the keystore yet: run `python -m app.runner.keys add {broker}`")
        return dict(fields)

    # --- the day's broker session ------------------------------------------------------------------------------------
    def put_session(self, broker: str, day: str, tokens: Mapping[str, Optional[str]], at: str) -> None:
        with self._edit() as data:
            if broker not in data["brokers"]:
                raise KeystoreError(f"No {broker} details in the keystore")
            data["sessions"][broker] = {"day": day, "at": at,
                                        **{k: tokens.get(k) for k in SESSION_FIELDS if tokens.get(k)}}

    def session(self, broker: str, day: str) -> Optional[dict[str, str]]:
        """The stored session of `day`, or None (none, or an older day's - a broker session ends at its daily expiry)."""
        data, _ = self._read()
        stored = data["sessions"].get(broker)
        return dict(stored) if stored and stored.get("day") == day and stored.get("access_token") else None

    def clear_session(self, broker: str) -> None:
        with self._edit() as data:
            data["sessions"].pop(broker, None)


__all__ = ["Keystore", "KeystoreError", "BROKER_FIELDS", "REQUIRED_FIELDS", "SECRET_FIELDS", "KEYFILE", "SCRYPT"]
