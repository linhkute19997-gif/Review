"""
Application logger.

Centralised logging setup so callers can ``from app.utils.logger import logger``
instead of ``print('[DEBUG] ...')`` everywhere.

Output goes to stderr at ``INFO`` level by default and to a rotating file
``logs/app.log`` next to the executable at ``DEBUG`` level. Set the
``RPP_LOG_LEVEL`` env var to override the console level (e.g. ``DEBUG``,
``WARNING``).
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.utils.config import BASE_DIR

_LOG_DIR = BASE_DIR / 'logs'
_LOG_FILE = _LOG_DIR / 'app.log'
_LOGGER_NAME = 'rpp'
_FMT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'

_configured = False


def _build_console_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    return handler


def _build_file_handler() -> logging.Handler | None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
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
    global _configured
    root = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        root.setLevel(logging.DEBUG)
        root.propagate = False
        root.addHandler(_build_console_handler(_resolve_console_level()))
        file_handler = _build_file_handler()
        if file_handler is not None:
            root.addHandler(file_handler)
        _configured = True
    if name is None or name == _LOGGER_NAME:
        return root
    return root.getChild(name)


# Default importable logger for short, readable usage.
logger = get_logger()


__all__ = ['get_logger', 'logger']
