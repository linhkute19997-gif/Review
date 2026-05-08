"""
FFmpeg version check
====================
Validates that the bundled or system FFmpeg satisfies the minimum
version (4.4) required by the render pipeline. Older builds miss
``-progress pipe`` semantics, several encoder presets, and ``atempo``
chaining used by the voice-over pipeline.

Usage::

    from app.utils.ffmpeg_check import check_ffmpeg
    ok, message = check_ffmpeg()

The boot path can show *message* in a QMessageBox when *ok* is False.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Tuple

from app.utils.config import FFMPEG_PATH
from app.utils.logger import get_logger

logger = get_logger('ffmpeg_check')

MIN_VERSION: Tuple[int, int] = (4, 4)
_VERSION_RE = re.compile(r'ffmpeg version (\d+)\.(\d+)')


def _resolve_ffmpeg_path() -> str:
    """Pick the first available ffmpeg binary.

    Prefers the bundled ``ffmpeg.exe`` next to the app, then falls back
    to whichever ``ffmpeg`` is on PATH (common on Linux/macOS dev runs).
    """
    if os.path.isfile(FFMPEG_PATH):
        return FFMPEG_PATH
    return 'ffmpeg'


def _run_version() -> str:
    """Return the first line of ``ffmpeg -version`` or '' on failure."""
    creation_flags = 0
    if sys.platform == 'win32':
        creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.run(
            [_resolve_ffmpeg_path(), '-version'],
            capture_output=True, text=True, timeout=8,
            creationflags=creation_flags,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("ffmpeg -version failed: %s", exc)
        return ''
    return (proc.stdout or '').splitlines()[0] if proc.stdout else ''


def parse_version(line: str) -> Tuple[int, int] | None:
    """Extract (major, minor) from ``ffmpeg version X.Y.Z …`` output."""
    match = _VERSION_RE.search(line or '')
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_ffmpeg() -> Tuple[bool, str]:
    """Verify the active FFmpeg satisfies ``MIN_VERSION``.

    Returns ``(ok, message)``. ``message`` is empty when ``ok`` is True;
    otherwise it contains a Vietnamese-friendly description that the
    caller can surface in a dialog.
    """
    line = _run_version()
    if not line:
        return False, (
            "❌ Không tìm thấy ffmpeg. "
            "Vui lòng đặt ffmpeg.exe cạnh ứng dụng hoặc cài ffmpeg "
            "vào PATH trước khi chạy render."
        )

    version = parse_version(line)
    if not version:
        logger.warning("Could not parse ffmpeg version from: %s", line)
        # Don't block the user just because parsing failed.
        return True, ''

    if version < MIN_VERSION:
        major, minor = version
        need_major, need_minor = MIN_VERSION
        return False, (
            f"❌ Phiên bản FFmpeg {major}.{minor} quá cũ.\n"
            f"Pipeline cần FFmpeg ≥ {need_major}.{need_minor} "
            f"để dùng -progress pipe, atempo và các encoder mới.\n"
            f"Hãy cập nhật ffmpeg.exe rồi mở lại ứng dụng."
        )

    logger.info("FFmpeg %s.%s detected — OK", *version)
    return True, ''
