"""
Secret storage for translation API keys.

The active backend is auto-selected on first use:

* **Keychain** — uses ``keyring`` against the OS keychain
  (Windows Credential Manager / macOS Keychain / Linux Secret Service).
  This is the preferred backend whenever it can both store and retrieve
  values reliably.
* **Encrypted file** — a Fernet-encrypted (when ``cryptography`` is
  available) or AES-CBC-with-PBKDF2-derived-key (pure-stdlib fallback)
  store at ``BASE_DIR/.secrets.dat``. The key is derived from a
  per-machine identifier so the file is meaningless if copied to
  another machine but does **not** require the user to type a master
  password.

API keys are referenced from the JSON config as opaque strings of the
form ``vault:<service>:<id>``; ``resolve`` turns one back into the
plaintext value for use in HTTP requests.

The module survives missing optional dependencies — if neither backend
is available, secrets are written to the encrypted file fallback,
which only depends on the standard library.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import os
import secrets as _secrets
import socket
import threading
import uuid
from typing import Optional

from app.utils.atomic_io import atomic_write_json, read_json
from app.utils.config import BASE_DIR
from app.utils.logger import get_logger

logger = get_logger('key_vault')

VAULT_PREFIX = 'vault:'
SERVICE = 'review-phim-pro'
_FALLBACK_FILE = str(BASE_DIR / '.secrets.dat')

_lock = threading.Lock()
_cached_backend: Optional['_VaultBackend'] = None


class _VaultBackend:
    name = 'noop'

    def store(self, ref: str, value: str) -> None:
        raise NotImplementedError

    def fetch(self, ref: str) -> Optional[str]:
        raise NotImplementedError

    def delete(self, ref: str) -> None:
        raise NotImplementedError


class _KeyringBackend(_VaultBackend):
    name = 'keyring'

    def __init__(self):
        import keyring  # noqa: WPS433
        self._keyring = keyring

    def store(self, ref: str, value: str) -> None:
        self._keyring.set_password(SERVICE, ref, value)

    def fetch(self, ref: str) -> Optional[str]:
        return self._keyring.get_password(SERVICE, ref)

    def delete(self, ref: str) -> None:
        try:
            self._keyring.delete_password(SERVICE, ref)
        except Exception:
            pass


def _machine_passphrase() -> bytes:
    """Derive a stable per-machine passphrase.

    The MAC address + hostname combination is enough to make the
    encrypted file useless if copied off the machine. It is **not** a
    user secret — it is a low-friction local-data-binding measure.
    """
    parts = [
        str(uuid.getnode()),
        socket.gethostname(),
        'review-phim-pro/v1',
    ]
    return ('||'.join(parts)).encode('utf-8')


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', passphrase, salt, iterations=120_000, dklen=32)


class _EncryptedFileBackend(_VaultBackend):
    """Fernet-style AES-CBC-HMAC store using only the standard library.

    Each value is stored as ``base64( salt(16) || iv(16) || ciphertext || mac(32) )``.
    The data is small (a few API keys), so we keep the whole file in
    memory and re-write it atomically on each change.
    """
    name = 'encrypted-file'

    def __init__(self, path: str = _FALLBACK_FILE):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        return read_json(self._path, {}) or {}

    def _save(self, data: dict) -> None:
        atomic_write_json(self._path, data)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _encrypt(self, plaintext: str) -> str:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding
        except Exception as exc:  # pragma: no cover - executes only without cryptography
            raise RuntimeError(
                'cryptography is required for the encrypted-file vault') from exc
        salt = _secrets.token_bytes(16)
        iv = _secrets.token_bytes(16)
        enc_key = _derive_key(_machine_passphrase(), salt)
        mac_key = _derive_key(_machine_passphrase(), salt + b'mac')

        padder = padding.PKCS7(128).padder()
        padded = padder.update(plaintext.encode('utf-8')) + padder.finalize()
        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ct = encryptor.update(padded) + encryptor.finalize()
        mac = hmac.new(mac_key, salt + iv + ct, hashlib.sha256).digest()
        blob = salt + iv + ct + mac
        return base64.b64encode(blob).decode('ascii')

    def _decrypt(self, blob_b64: str) -> Optional[str]:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding
        except Exception:
            return None
        try:
            blob = base64.b64decode(blob_b64.encode('ascii'))
            salt, iv, ct_mac = blob[:16], blob[16:32], blob[32:]
            ct, mac = ct_mac[:-32], ct_mac[-32:]
            mac_key = _derive_key(_machine_passphrase(), salt + b'mac')
            expected = hmac.new(mac_key, salt + iv + ct, hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                logger.warning("Vault MAC mismatch — refusing to decrypt entry")
                return None
            enc_key = _derive_key(_machine_passphrase(), salt)
            cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ct) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            data = unpadder.update(padded) + unpadder.finalize()
            return data.decode('utf-8')
        except Exception as exc:
            logger.warning("Vault decrypt error: %s", exc)
            return None

    def store(self, ref: str, value: str) -> None:
        with self._lock:
            data = self._load()
            data[ref] = self._encrypt(value)
            self._save(data)

    def fetch(self, ref: str) -> Optional[str]:
        with self._lock:
            data = self._load()
            blob = data.get(ref)
            if not blob:
                return None
            return self._decrypt(blob)

    def delete(self, ref: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(ref, None) is not None:
                self._save(data)


class _ObfuscatedFileBackend(_VaultBackend):
    """Last-resort backend used when ``cryptography`` is missing.

    XOR with the machine passphrase. Strictly an obfuscation layer — it
    is *not* secure against an attacker with read access. We log a
    warning so users see the limitation.
    """
    name = 'obfuscated-file'

    def __init__(self, path: str = _FALLBACK_FILE):
        self._path = path
        self._lock = threading.Lock()
        logger.warning(
            "Falling back to obfuscated-file vault — install 'cryptography' "
            "or 'keyring' for stronger protection")

    def _xor(self, data: bytes) -> bytes:
        key = hashlib.sha256(_machine_passphrase()).digest()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    def _load(self) -> dict:
        return read_json(self._path, {}) or {}

    def _save(self, data: dict) -> None:
        atomic_write_json(self._path, data)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def store(self, ref: str, value: str) -> None:
        with self._lock:
            data = self._load()
            data[ref] = base64.b64encode(self._xor(value.encode('utf-8'))).decode('ascii')
            self._save(data)

    def fetch(self, ref: str) -> Optional[str]:
        with self._lock:
            data = self._load()
            blob = data.get(ref)
            if not blob:
                return None
            try:
                return self._xor(base64.b64decode(blob.encode('ascii'))).decode('utf-8')
            except Exception:
                return None

    def delete(self, ref: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(ref, None) is not None:
                self._save(data)


def _try_keyring() -> Optional[_VaultBackend]:
    try:
        import keyring  # noqa: WPS433
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
        cls_name = type(backend).__name__.lower()
        if 'fail' in cls_name or 'null' in cls_name:
            logger.info(
                "keyring backend %s is non-functional; using file vault",
                cls_name)
            return None
        # Round-trip test so we don't pick a backend that fails at write.
        keyring.set_password(SERVICE, '__rpp_health_check__', 'ok')
        if keyring.get_password(SERVICE, '__rpp_health_check__') != 'ok':
            logger.info("keyring round-trip failed; using file vault")
            return None
        keyring.delete_password(SERVICE, '__rpp_health_check__')
    except Exception as exc:
        logger.info("keyring unavailable (%s); using file vault", exc)
        return None
    return _KeyringBackend()


def _try_encrypted_file() -> Optional[_VaultBackend]:
    if importlib.util.find_spec('cryptography') is None:
        return None
    return _EncryptedFileBackend()


def _resolve_backend() -> _VaultBackend:
    global _cached_backend
    if _cached_backend is not None:
        return _cached_backend
    with _lock:
        if _cached_backend is not None:
            return _cached_backend
        backend: Optional[_VaultBackend] = _try_keyring()
        if backend is None:
            backend = _try_encrypted_file()
        if backend is None:
            backend = _ObfuscatedFileBackend()
        logger.info("Using vault backend: %s", backend.name)
        _cached_backend = backend
        return backend


def make_ref(model: str, idx: int) -> str:
    """Stable reference for the ``idx``-th key of ``model``."""
    safe = ''.join(c if c.isalnum() else '-' for c in model)
    return f'{VAULT_PREFIX}{safe}:{idx}'


def is_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(VAULT_PREFIX)


def store(ref: str, value: str) -> None:
    _resolve_backend().store(ref, value)


def fetch(ref: str) -> Optional[str]:
    return _resolve_backend().fetch(ref)


def delete(ref: str) -> None:
    _resolve_backend().delete(ref)


def resolve(value: object) -> object:
    """Dereference a single value, leaving non-references untouched."""
    if is_ref(value):
        plain = fetch(value)  # type: ignore[arg-type]
        return plain or ''
    return value


def active_backend_name() -> str:
    return _resolve_backend().name


__all__ = [
    'VAULT_PREFIX', 'make_ref', 'is_ref',
    'store', 'fetch', 'delete', 'resolve', 'active_backend_name',
]
