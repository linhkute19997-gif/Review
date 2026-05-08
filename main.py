"""
Review Phim Pro — Entry Point
"""
import sys
import os
import threading

# Ensure the project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont

from app.main_window import MainWindow
from app.domain.prewarm import PrewarmService
from app.utils.ffmpeg_check import check_ffmpeg
from app.utils.logger import get_logger
from app.utils.theme import apply_theme

logger = get_logger('main')


def _prewarm_encoders():
    """Run the encoder probe in a daemon thread.

    P2-6: pay the ~1.5 s probe cost once at boot instead of on the
    first render. The detector writes to ``encoder_cache.json`` so
    every subsequent process boot is essentially free until the user
    swaps GPU drivers.
    """
    try:
        from app.utils.encoder_detector import EncoderDetector
        EncoderDetector().detect_available_encoders()
    except Exception as exc:  # noqa: BLE001 — best-effort warmup
        logger.debug("Encoder pre-warm failed (will retry on render): %s", exc)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")

    # P3-15: load the user's saved theme (Dark / Light / System) and
    # apply the matching QSS before MainWindow constructs widgets so
    # nothing flashes the wrong palette on first paint.
    apply_theme(app)

    # Boot-time FFmpeg version check. We need ≥ 4.4 for atempo chaining,
    # ``-progress pipe:1``, and the modern ASS subtitle pipeline.
    ok, message = check_ffmpeg()
    if not ok:
        logger.error("FFmpeg precheck failed: %s", message)
        QMessageBox.critical(None, "Review Phim Pro — FFmpeg", message)
        sys.exit(1)

    # Background pre-warm of Whisper / PaddleOCR caches. Errors are
    # swallowed inside the service — manual triggers in the dialog
    # still work even if the warm-up fails on boot.
    prewarm = PrewarmService()
    prewarm.start()

    # Pre-warm the FFmpeg encoder probe so the first render doesn't
    # pay the ~1.5 s detection cost. Runs in parallel with the main
    # window construction, no UI dependency.
    threading.Thread(
        target=_prewarm_encoders, name='encoder-probe',
        daemon=True).start()

    window = MainWindow(prewarm=prewarm)
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
