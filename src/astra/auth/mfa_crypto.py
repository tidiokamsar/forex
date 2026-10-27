"""Authenticated encryption of the TOTP secrets stored in the authentication database.

TOTP verification needs the secret itself, so it is encrypted (reversibly), not hashed. Scheme:
AES-256-GCM from ``cryptography`` (pinned in requirements/web.txt), a fresh random 96-bit nonce
for every encryption, and associated data that binds each ciphertext to its format version, key
id, account id and purpose (active or pending secret). A ciphertext moved to another account or
to the other column therefore fails to decrypt.

Stored value: ``astra-mfa:v1:<key id>:<nonce b64url>:<ciphertext+tag b64url>``.

Keys live in a separate file (``ASTRA_MFA_KEY_FILE``), never in Git, SQLite or the logs:

    astra-mfa-keys v1
    <key id> <32 random bytes, base64url>     <- first key: used to encrypt
    <older key id> <...>                       <- still accepted to decrypt, until retired

Rotation: ``astra auth mfa-key-rotate`` adds a new first key and re-encrypts every secret with
it; ``astra auth mfa-key-retire --kid <old>`` then removes the old key, refusing while any stored
secret still uses it. Every failure (missing or malformed file, unknown key id, corrupted data,
wrong key, plaintext secret) raises ``MfaKeyError``: MFA is refused, never silently disabled.
Random 96-bit nonces stay far below the NIST limit of 2**32 encryptions per key for this volume.
"""

from __future__ import annotations

import base64
import contextlib
import os
import re
import secrets
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FILE_HEADER = "astra-mfa-keys v1"
PREFIX = "astra-mfa:v1:"
KEY_ID = re.compile(r"[a-z0-9][a-z0-9_-]{2,31}")
PURPOSES = ("active", "pending")
TOTP_SECRET = re.compile(r"[A-Z2-7]{16,128}=*")  # the only plaintext values migration may encrypt


class MfaKeyError(RuntimeError):
    """The MFA secret cannot be sealed or opened; the caller must refuse (fail closed)."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True, slots=True)
class MfaKey:
    key_id: str
    key: bytes = field(repr=False)  # never shown in a repr, traceback or debugger listing


class MfaCipher:
    """Encrypts with the first key of the key ring and decrypts with any key it holds."""

    def __init__(self, keys: list[MfaKey]) -> None:
        if not keys:
            raise MfaKeyError("the MFA key ring is empty")
        ids = [k.key_id for k in keys]
        if len(set(ids)) != len(ids):
            raise MfaKeyError("duplicate MFA key id")
        for key in keys:
            if not KEY_ID.fullmatch(key.key_id) or len(key.key) != 32:
                raise MfaKeyError("invalid MFA key entry")
        self._keys = {k.key_id: AESGCM(k.key) for k in keys}
        self.active_key_id = keys[0].key_id
        self.key_ids = tuple(ids)

    @staticmethod
    def _aad(key_id: str, user_id: int, purpose: str) -> bytes:
        if purpose not in PURPOSES or not isinstance(user_id, int):
            raise MfaKeyError("invalid MFA secret binding")
        return f"{PREFIX}{key_id}|user={user_id}|{purpose}".encode("ascii")

    def seal(self, user_id: int, purpose: str, secret: str) -> str:
        if not isinstance(secret, str) or not TOTP_SECRET.fullmatch(secret):
            raise MfaKeyError("refusing to encrypt a value that is not a base32 TOTP secret")
        nonce = secrets.token_bytes(12)
        key_id = self.active_key_id
        data = self._keys[key_id].encrypt(nonce, secret.encode("ascii"), self._aad(key_id, user_id, purpose))
        return f"{PREFIX}{key_id}:{_b64(nonce)}:{_b64(data)}"

    def open(self, user_id: int, purpose: str, stored: str) -> str:
        if not is_sealed(stored):
            raise MfaKeyError("MFA secret is not encrypted (run astra auth mfa-reencrypt)")
        try:
            key_id, nonce_text, data_text = stored[len(PREFIX):].split(":")
            nonce, data = _unb64(nonce_text), _unb64(data_text)
        except ValueError as exc:
            raise MfaKeyError("MFA secret is malformed") from exc
        cipher = self._keys.get(key_id)
        if cipher is None:
            raise MfaKeyError(f"MFA key {key_id!r} is not in the key ring")
        if len(nonce) != 12:
            raise MfaKeyError("MFA secret is malformed")
        try:
            return cipher.decrypt(nonce, data, self._aad(key_id, user_id, purpose)).decode("ascii")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise MfaKeyError("MFA secret failed authentication (wrong key, other account or corrupted)") from exc

    def needs_reencryption(self, stored: str) -> bool:
        return not is_sealed(stored) or stored[len(PREFIX):].split(":", 1)[0] != self.active_key_id


def is_sealed(stored: str | None) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)


def sealed_key_id(stored: str) -> str | None:
    return stored[len(PREFIX):].split(":", 1)[0] if is_sealed(stored) else None


def _parse(text: str) -> Iterator[MfaKey]:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines or lines[0] != FILE_HEADER:
        raise MfaKeyError(f"MFA key file must start with {FILE_HEADER!r}")
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 2:
            raise MfaKeyError("malformed MFA key line")
        try:
            yield MfaKey(parts[0], _unb64(parts[1]))
        except ValueError as exc:
            raise MfaKeyError("malformed MFA key line") from exc


def load_cipher(path: Path | str) -> MfaCipher:
    """Read the key ring; refuses a missing file, a malformed one, or one readable by others (POSIX)."""

    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise MfaKeyError(f"{path} must be a regular file (not a link or a directory)")
        if os.name == "posix" and info.st_mode & 0o007:
            raise MfaKeyError(f"{path} must not be accessible to other users (chmod 0600 or 0640)")
        text = path.read_text(encoding="ascii")
    except FileNotFoundError as exc:
        raise MfaKeyError(f"MFA key file {path} does not exist (astra auth mfa-key-init)") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise MfaKeyError(f"MFA key file {path} cannot be read") from exc
    return MfaCipher(list(_parse(text)))


def new_key_id(existing: tuple[str, ...] = ()) -> str:
    stamp = datetime.now(UTC).strftime("k%Y%m%d")
    while True:
        candidate = f"{stamp}-{secrets.token_hex(3)}"
        if candidate not in existing:
            return candidate


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Exclusive lock on the key ring for writers (POSIX flock on a side file), so two rotations cannot lose a key."""

    if os.name != "posix":
        yield
        return
    import fcntl

    try:
        fd = os.open(path.with_name(f".{path.name}.lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise MfaKeyError(f"cannot lock the MFA key ring next to {path}: {exc.strerror}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(path: Path, keys: list[MfaKey], group: str | None = None) -> None:
    """Atomically replace the key ring, keeping the owner, group and mode of the file it replaces.

    A new file is 0600, or 0640 with ``group`` (for example the service group) when given. Any OS error
    becomes MfaKeyError; the temporary file is removed on failure.
    """

    body = FILE_HEADER + "\n" + "".join(f"{k.key_id} {_b64(k.key)}\n" for k in keys)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.stat() if path.exists() else None
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            if os.name == "posix":
                if previous is not None:
                    os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
                    os.fchmod(handle.fileno(), stat.S_IMODE(previous.st_mode) & 0o640)
                elif group is not None:
                    import grp

                    os.fchown(handle.fileno(), -1, grp.getgrnam(group).gr_gid)
                    os.fchmod(handle.fileno(), 0o640)
            handle.write(body.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except (OSError, KeyError) as exc:
        with contextlib.suppress(OSError):
            temporary.unlink()
        detail = exc.strerror if isinstance(exc, OSError) else "unknown group"
        raise MfaKeyError(f"cannot write the MFA key ring {path}: {detail}"
                          " (run the key commands as root, see OPERATIONS.md)") from exc


def init_key_file(path: Path | str, group: str | None = None) -> str:
    """Create the key file with one new key (refused if the file exists). Returns the key id."""

    path = Path(path)
    with _locked(path):
        if path.exists():
            raise MfaKeyError(f"{path} already exists")
        key = MfaKey(new_key_id(), secrets.token_bytes(32))
        _write(path, [key], group=group)
    return key.key_id


def add_active_key(path: Path | str) -> str:
    """Put a new key first in the ring (it encrypts from now on); older keys stay for decryption."""

    path = Path(path)
    with _locked(path):
        current = load_cipher(path)
        keys = list(_parse(path.read_text(encoding="ascii")))
        key = MfaKey(new_key_id(current.key_ids), secrets.token_bytes(32))
        _write(path, [key, *keys])
    return key.key_id


def remove_key(path: Path | str, key_id: str, still_used: set[str]) -> None:
    path = Path(path)
    with _locked(path):
        load_cipher(path)  # same validity and permission checks as at startup
        keys = list(_parse(path.read_text(encoding="ascii")))
        if key_id not in {k.key_id for k in keys}:
            raise MfaKeyError(f"MFA key {key_id!r} is not in the key ring")
        if keys[0].key_id == key_id:
            raise MfaKeyError("the active MFA key cannot be retired; rotate first")
        if key_id in still_used:
            raise MfaKeyError(f"MFA key {key_id!r} still protects stored secrets; run astra auth mfa-reencrypt first")
        _write(path, [k for k in keys if k.key_id != key_id])
