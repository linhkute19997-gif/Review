"""
Atomic JSON I/O helpers.

Writes go to a sibling temp file then ``os.replace`` swaps them in, so a
crash mid-write can never corrupt the destination. Reads are best-effort
and return a fallback if the file is missing or invalid.

A ``filelock`` (when installed) coordinates concurrent writers across
processes; if the dependency is unavailable we fall back to an
in-process ``threading.Lock`` so single-process use stays correct.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.utils.logger import get_logger

logger = get_logger('atomic_io')

try:
    from filelock import FileLock, Timeout as _FileLockTimeout  # type: ignore
    _HAS_FILELOCK = True
except Exception:  # pragma: no cover - optional dep
    FileLock = None  # type: ignore[assignment]
    _FileLockTimeout = Exception  # type: ignore[assignment]
    _HAS_FILELOCK = False

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(path: str) -> threading.Lock:
    with _thread_locks_guard:
        lock = _thread_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[path] = lock
        return lock


@contextmanager
def _file_lock(path: str, timeout: float = 5.0) -> Iterator[None]:
    """Best-effort cross-process + cross-thread lock for ``path``."""
    thread_lock = _get_thread_lock(path)
    if _HAS_FILELOCK:
        lock_path = path + '.lock'
        flock = FileLock(lock_path, timeout=timeout)
        try:
            with flock:
                with thread_lock:
                    yield
        except _FileLockTimeout:
            logger.warning("File lock timeout on %s, proceeding without it", path)
            with thread_lock:
                yield
    else:
        with thread_lock:
            yield


def atomic_write_text(path: str | os.PathLike, text: str,
                      encoding: str = 'utf-8') -> None:
    """Write ``text`` to ``path`` atomically (tempfile + os.replace)."""
    path_str = os.fspath(path)
    directory = os.path.dirname(path_str) or '.'
    os.makedirs(directory, exist_ok=True)

    with _file_lock(path_str):
        fd, tmp = tempfile.mkstemp(
            prefix='.tmp-', suffix='.write',
            dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding=encoding) as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path_str)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def atomic_write_json(path: str | os.PathLike, data: Any,
                      indent: int = 2, ensure_ascii: bool = False) -> None:
    """JSON-encode ``data`` and write to ``path`` atomically."""
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, payload)


def read_json(path: str | os.PathLike, default: Any = None) -> Any:
    """Read a JSON document, returning ``default`` on any failure.

    The lock is held briefly so we don't read a partially-replaced file
    on platforms where ``os.replace`` isn't atomic from the reader's
    perspective.
    """
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        return default
    with _file_lock(path_str):
        try:
            with open(path_str, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to read JSON %s: %s", path_str, exc)
            return default


__all__ = ['atomic_write_text', 'atomic_write_json', 'read_json']
