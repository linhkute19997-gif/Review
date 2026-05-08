"""
Auto-shutdown countdown dialog (P3-11)
======================================
Replaces the silent ``shutdown -t 60`` dispatch with a visible
countdown that the user can cancel. The OS-level shutdown is only
triggered after the dialog is *accepted*; pressing Cancel (or
closing the dialog) aborts the schedule.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)


class ShutdownCountdownDialog(QDialog):
    """Modal countdown that defers the actual shutdown call.

    The dialog returns ``Accepted`` once the timer hits zero or the
    user clicks "Tắt ngay". It returns ``Rejected`` if the user
    clicks "Huỷ" or closes the window — caller MUST treat that as
    "do not shut down".
    """

    def __init__(self, total_seconds: int = 60, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tự động tắt máy")
        self.setModal(True)
        self.setFixedSize(360, 170)
        self._total = max(5, int(total_seconds))
        self._remaining = self._total
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Render đã hoàn tất.\nMáy sẽ tự tắt sau:")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._countdown_label = QLabel(f"{self._total}s")
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._countdown_label.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: #ff5252;")
        layout.addWidget(self._countdown_label)

        self._bar = QProgressBar()
        self._bar.setRange(0, self._total)
        self._bar.setValue(self._total)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        layout.addWidget(self._bar)

        buttons = QHBoxLayout()
        self.btn_cancel = QPushButton("✖ Huỷ — không tắt máy")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)

        self.btn_now = QPushButton("⏻ Tắt ngay")
        self.btn_now.setObjectName("btnTienHanh")
        self.btn_now.clicked.connect(self.accept)
        buttons.addWidget(self.btn_now)
        layout.addLayout(buttons)

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.accept()
            return
        self._countdown_label.setText(f"{self._remaining}s")
        self._bar.setValue(self._remaining)

    def reject(self) -> None:  # noqa: D401 — Qt API
        self._timer.stop()
        super().reject()

    def accept(self) -> None:  # noqa: D401 — Qt API
        self._timer.stop()
        super().accept()


__all__ = ['ShutdownCountdownDialog']
