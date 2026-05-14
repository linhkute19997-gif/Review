"""
Application logger.

Centralised logging setup so callers can ``from app.utils.logger import logger``
instead of ``print('[DEBUG] ...')`` everywhere.

Output goes to stderr at ``INFO`` level by default and to a rotating file
``logs/app.log`` next to the executable at ``DEBUG`` level. Two ways to bump
the *console* level to ``DEBUG``:

1. Set the ``RPP_LOG_LEVEL`` env var before launch (e.g. ``DEBUG``,
   ``WARNING``). Useful for CI / power users.
2. Call :func:`enable_debug` at runtime — the Help → Debug menu wires this
   up so end users can flip verbosity from the UI without restarting.

The module also exposes :func:`install_excepthooks` to capture uncaught
exceptions (main thread, worker threads, Qt event-loop) and
:func:`export_debug_bundle` / :func:`collect_system_info` to package
``logs/`` + a system-info dump into a single zip the user can attach to a
bug report.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import platform
import shutil
import sys
import traceback
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from app.utils.config import BASE_DIR

LOG_DIR = BASE_DIR / 'logs'
LOG_FILE = LOG_DIR / 'app.log'
_LOGGER_NAME = 'rpp'
_FMT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'

_configured = False
_console_handler: logging.Handler | None = None
_file_handler: logging.Handler | None = None
_excepthooks_installed = False


def _build_console_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    return handler


def _build_file_handler() -> logging.Handler | None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        return handler
    except Exception:
        return None


def _resolve_console_level() -> int:
    raw = os.environ.get('RPP_LOG_LEVEL', 'INFO').strip().upper()
    return getattr(logging, raw, logging.INFO)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger (or a child of it)."""
    global _configured, _console_handler, _file_handler
    root = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        root.setLevel(logging.DEBUG)
        root.propagate = False
        _console_handler = _build_console_handler(_resolve_console_level())
        root.addHandler(_console_handler)
        _file_handler = _build_file_handler()
        if _file_handler is not None:
            root.addHandler(_file_handler)
        _configured = True
    if name is None or name == _LOGGER_NAME:
        return root
    return root.getChild(name)


# Default importable logger for short, readable usage.
logger = get_logger()


# ═══════════════════════════════════════════════════════════════════
# Debug toggle
# ═══════════════════════════════════════════════════════════════════

def is_debug_enabled() -> bool:
    """Return True iff the console handler is currently at ``DEBUG`` level."""
    if _console_handler is None:
        # Logger not yet configured — assume the env var dictates the level.
        return _resolve_console_level() <= logging.DEBUG
    return _console_handler.level <= logging.DEBUG


def enable_debug() -> None:
    """Bump the console handler to ``DEBUG`` level.

    No-op when the logger hasn't been configured yet — the env var still
    drives the initial level in that case.
    """
    get_logger()  # Ensure handlers exist before touching them.
    if _console_handler is not None:
        _console_handler.setLevel(logging.DEBUG)
    logger.info("Debug logging enabled at runtime")


def disable_debug() -> None:
    """Reset the console handler to the env-var level (default ``INFO``)."""
    get_logger()
    if _console_handler is not None:
        _console_handler.setLevel(_resolve_console_level())
    logger.info("Debug logging disabled at runtime")


def set_debug(enabled: bool) -> None:
    """Convenience setter for the UI menu."""
    if enabled:
        enable_debug()
    else:
        disable_debug()


# ═══════════════════════════════════════════════════════════════════
# Uncaught-exception capture
# ═══════════════════════════════════════════════════════════════════

def _format_exc(exc_type, exc_value, exc_tb) -> str:
    return ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))


def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    # ``KeyboardInterrupt`` should still propagate so the user can Ctrl+C.
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logger.critical(
        "Uncaught exception on main thread:\n%s",
        _format_exc(exc_type, exc_value, exc_tb))


def _thread_excepthook(args) -> None:
    # ``args`` is a ``threading.ExceptHookArgs`` namedtuple.
    if issubclass(args.exc_type, SystemExit):
        return
    name = getattr(args.thread, 'name', '<unknown>')
    logger.critical(
        "Uncaught exception on thread %r:\n%s",
        name, _format_exc(args.exc_type, args.exc_value, args.exc_traceback))


def _qt_message_handler(mode, context, message) -> None:
    """Forward Qt's own log stream into our logger.

    Qt uses its own log levels (Debug / Info / Warning / Critical / Fatal);
    we map them to the matching :mod:`logging` levels. Without this hook,
    Qt warnings go to ``stderr`` only — they don't reach ``logs/app.log``
    and a user who only sends back the bundle would have a strictly worse
    diagnostic surface than someone running with the console open.
    """
    try:
        from PyQt6.QtCore import QtMsgType
    except Exception:
        return
    text = str(message)
    file = getattr(context, 'file', None) or ''
    line = getattr(context, 'line', 0) or 0
    suffix = f' [{file}:{line}]' if file else ''
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(mode, logging.INFO)
    logger.log(level, 'Qt: %s%s', text, suffix)


def install_excepthooks() -> None:
    """Route uncaught exceptions (sys, threading, Qt) through the logger.

    Safe to call multiple times — only the first call wires the hooks.
    Idempotent so callers don't need to guard.
    """
    global _excepthooks_installed
    if _excepthooks_installed:
        return
    get_logger()  # Force handler setup before any exception can land.
    sys.excepthook = _sys_excepthook
    try:
        import threading
        threading.excepthook = _thread_excepthook
    except Exception:
        # ``threading.excepthook`` exists on 3.8+; we target 3.10+ so this
        # path is purely defensive. Don't crash the install.
        pass
    try:
        from PyQt6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        # Qt may not be imported yet (unit tests, headless trace). The
        # ``sys`` / ``threading`` hooks are still in place.
        pass
    _excepthooks_installed = True
    logger.info("Excepthooks installed (sys + threading + Qt)")


# ═══════════════════════════════════════════════════════════════════
# Boot banner
# ═══════════════════════════════════════════════════════════════════

def log_boot_banner() -> None:
    """Print a high-signal banner at the top of every session's log.

    Makes it trivial to spot the boundary between two runs when the log
    file is opened in a text editor (or shipped back to us).
    """
    now = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info('=' * 60)
    logger.info("Review Phim Pro — boot @ %s", now)
    logger.info("Python %s on %s", sys.version.split()[0], platform.platform())
    logger.info("Base dir: %s", BASE_DIR)
    logger.info("Debug logging: %s", 'ON' if is_debug_enabled() else 'off')
    logger.info('=' * 60)


# ═══════════════════════════════════════════════════════════════════
# System info & debug bundle
# ═══════════════════════════════════════════════════════════════════

def _iter_log_files() -> Iterable[Path]:
    """Yield every existing log file (current + rotated backups)."""
    if not LOG_DIR.exists():
        return
    # ``app.log`` plus ``app.log.1``, ``app.log.2`` … written by
    # :class:`RotatingFileHandler`.
    yield from sorted(LOG_DIR.glob('app.log*'))


def _safe_env_snapshot() -> dict[str, str]:
    """Return ``os.environ`` minus anything that smells like a secret."""
    secret_markers = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'AUTH', 'COOKIE')
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        upper = k.upper()
        if any(m in upper for m in secret_markers):
            out[k] = '<redacted>'
        else:
            out[k] = v
    return out


def _ffmpeg_summary() -> str:
    """Best-effort one-liner of the active FFmpeg version, never raising."""
    try:
        from app.utils.ffmpeg_check import _run_version
        line = _run_version()
        return line or '<ffmpeg not found>'
    except Exception as exc:  # noqa: BLE001
        return f'<probe failed: {exc}>'


def _encoder_cache_summary() -> str:
    """Read the cached FFmpeg encoder probe so the bundle records what
    GPU acceleration the user has available without re-running the
    ~1.5 s probe.
    """
    cache = BASE_DIR / 'encoder_cache.json'
    if not cache.exists():
        return '<no encoder_cache.json>'
    try:
        return cache.read_text(encoding='utf-8')
    except Exception as exc:  # noqa: BLE001
        return f'<read failed: {exc}>'


def _screen_summary() -> str:
    """Report active screen geometry — without forcing a Qt import if
    we're called from a non-GUI context (unit tests, the audit trace).
    """
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return '<no QApplication yet>'
        lines = []
        for i, screen in enumerate(app.screens()):
            geom = screen.geometry()
            lines.append(
                f"  screen {i}: {geom.width()}x{geom.height()} "
                f"@ ({geom.x()}, {geom.y()}) — {screen.name()}")
        return '\n'.join(lines) if lines else '<no screens>'
    except Exception as exc:  # noqa: BLE001
        return f'<screen probe failed: {exc}>'


def collect_system_info() -> str:
    """Return a multi-line text dump describing the environment.

    The output is intentionally plain text — easy to paste into a bug
    report, easy to grep, no JSON structural overhead. Anything that
    could leak credentials is redacted (env vars, vault paths).
    """
    now = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines: list[str] = []
    lines.append(f"Review Phim Pro — system info @ {now}")
    lines.append('=' * 60)
    lines.append(f"Python      : {sys.version}")
    lines.append(f"Executable  : {sys.executable}")
    lines.append(f"Platform    : {platform.platform()}")
    lines.append(f"Machine     : {platform.machine()}")
    lines.append(f"Processor   : {platform.processor() or '<unknown>'}")
    lines.append(f"Base dir    : {BASE_DIR}")
    lines.append(f"CWD         : {os.getcwd()}")
    lines.append(f"Debug log   : {'ON' if is_debug_enabled() else 'off'}")
    lines.append('')
    lines.append('-- FFmpeg --')
    lines.append(_ffmpeg_summary())
    lines.append('')
    lines.append('-- Encoder cache --')
    lines.append(_encoder_cache_summary())
    lines.append('')
    lines.append('-- Screens --')
    lines.append(_screen_summary())
    lines.append('')
    lines.append('-- Environment (secrets redacted) --')
    for k, v in sorted(_safe_env_snapshot().items()):
        lines.append(f"  {k}={v}")
    return '\n'.join(lines) + '\n'


def export_debug_bundle(target_zip: str | Path) -> Path:
    """Bundle ``logs/`` + system info into a single zip file.

    The bundle deliberately excludes anything whose filename hints at
    credential storage (``vault``, ``secret``, ``token``). Returns the
    resolved :class:`Path` of the written archive.
    """
    target = Path(target_zip).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() != '.zip':
        target = target.with_suffix('.zip')

    # Force a flush on every handler so the last few lines make it into
    # the archive before we copy the file off disk.
    for handler in logging.getLogger(_LOGGER_NAME).handlers:
        try:
            handler.flush()
        except Exception:
            pass

    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('system_info.txt', collect_system_info())
        for log in _iter_log_files():
            # Skip anything that smells like it could contain credentials.
            lower = log.name.lower()
            if 'vault' in lower or 'secret' in lower or 'token' in lower:
                continue
            try:
                zf.write(log, arcname=f'logs/{log.name}')
            except OSError as exc:
                # A file rotated out from under us. Note it in the zip
                # so the recipient knows it was attempted.
                zf.writestr(
                    f'logs/{log.name}.MISSING',
                    f'log file disappeared during bundle: {exc}\n')

    logger.info("Wrote debug bundle to %s", target)
    return target


def clear_old_logs(keep_current: bool = True) -> int:
    """Delete rotated log backups (``app.log.1`` ...).

    Returns the number of files removed. The active ``app.log`` is kept
    by default — the rotating handler still holds a file handle to it
    and removing it on Windows would just fail noisily.
    """
    removed = 0
    for log in _iter_log_files():
        if keep_current and log.name == LOG_FILE.name:
            continue
        try:
            log.unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info("Cleared %d rotated log file(s)", removed)
    return removed


def copy_log_to(target_dir: str | Path) -> Path | None:
    """Copy the *current* ``app.log`` into ``target_dir``.

    Handy for "save just the log, not the whole bundle" affordances.
    Returns the destination path on success, ``None`` if the source
    file does not exist yet.
    """
    if not LOG_FILE.exists():
        return None
    dest_dir = Path(target_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / LOG_FILE.name
    shutil.copy2(LOG_FILE, dest)
    return dest


__all__ = [
    'LOG_DIR',
    'LOG_FILE',
    'clear_old_logs',
    'collect_system_info',
    'copy_log_to',
    'disable_debug',
    'enable_debug',
    'export_debug_bundle',
    'get_logger',
    'install_excepthooks',
    'is_debug_enabled',
    'log_boot_banner',
    'logger',
    'set_debug',
]
