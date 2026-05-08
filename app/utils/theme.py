"""
Theme management — Phase 3 P3-15
================================
Loads / applies QSS themes (Dark / Light / System) and persists the
choice in ``user_preferences.json``. Apps call :func:`apply_theme`
once at boot and every time the user changes the dropdown in the
"Cài Đặt" tab.

The "system" theme is a thin probe that asks Qt for the current
palette luminance; if Qt can't tell us, we fall back to dark, which
matches the legacy app default.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from app.utils.config import load_user_preferences, save_user_preferences
from app.utils.logger import get_logger

logger = get_logger('theme')

THEMES = ('dark', 'light', 'system')
DEFAULT_THEME = 'dark'

_STYLES_DIR = Path(__file__).parent.parent / 'styles'


def available_themes() -> Iterable[str]:
    """Return the list of theme keys exposed in the UI dropdown."""
    return THEMES


def _detect_system_theme() -> str:
    """Probe Qt's palette to decide whether the OS prefers dark mode."""
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPalette
    except ImportError:
        return DEFAULT_THEME
    app = QApplication.instance()
    if app is None:
        return DEFAULT_THEME
    try:
        palette = app.palette()
        bg = palette.color(QPalette.ColorRole.Window)
        # Qt returns 0–255 luminance; <128 means a dark window background.
        return 'dark' if bg.lightness() < 128 else 'light'
    except Exception as exc:  # noqa: BLE001
        logger.debug('System theme probe failed: %s', exc)
        return DEFAULT_THEME


def _resolve_theme(theme: str) -> str:
    """Map ``system`` → ``dark`` / ``light`` based on Qt palette."""
    if theme == 'system':
        return _detect_system_theme()
    if theme not in THEMES:
        return DEFAULT_THEME
    return theme


def load_qss(theme: str) -> str:
    """Read the QSS source for a resolved theme.

    Returns an empty string when the file is missing — callers fall
    back to Qt's built-in palette so the app still renders.
    """
    resolved = _resolve_theme(theme)
    path = _STYLES_DIR / f'{resolved}_theme.qss'
    if not path.exists():
        logger.warning('Theme file missing: %s', path)
        return ''
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        logger.warning('Could not read theme %s: %s', path, exc)
        return ''


def apply_theme(app, theme: Optional[str] = None) -> str:
    """Apply ``theme`` to the running ``QApplication``.

    ``theme`` defaults to whatever is stored in user preferences. The
    resolved theme name (always ``dark`` / ``light``) is returned so
    callers can update widgets that key off it (e.g. preview cards
    that need a different border colour).
    """
    if theme is None:
        prefs = load_user_preferences()
        theme = prefs.get('theme', DEFAULT_THEME)
    qss = load_qss(theme)
    try:
        app.setStyleSheet(qss)
    except Exception as exc:  # noqa: BLE001
        logger.warning('setStyleSheet failed: %s', exc)
    return _resolve_theme(theme)


def save_theme_preference(theme: str) -> None:
    """Persist the user's theme choice to ``user_preferences.json``."""
    if theme not in THEMES:
        theme = DEFAULT_THEME
    prefs = load_user_preferences()
    prefs['theme'] = theme
    save_user_preferences(prefs)


__all__ = [
    'THEMES', 'DEFAULT_THEME',
    'available_themes', 'apply_theme', 'load_qss', 'save_theme_preference',
]
