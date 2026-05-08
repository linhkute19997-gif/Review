"""
Review Phim Pro — Entry Point
"""
import sys
import os

# Ensure the project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont

from app.main_window import MainWindow
from app.domain.prewarm import PrewarmService
from app.utils.ffmpeg_check import check_ffmpeg
from app.utils.logger import get_logger

logger = get_logger('main')


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")

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

    window = MainWindow(prewarm=prewarm)
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
