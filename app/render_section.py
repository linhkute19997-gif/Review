"""
Render Section
==============
Render status display with progress, GPU info, timer, and batch table.
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer


class RenderSection(QWidget):
    """Render status and batch processing table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exporting = False
        self._elapsed_seconds = 0
        self._build_ui()
        self._detect_and_show_gpu_info()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()

        self.status_label = QLabel("⏳ Chưa sẵn sàng")
        self.status_label.setStyleSheet("color: #ff9800; font-size: 12px; font-weight: bold;")
        header.addWidget(self.status_label)

        header.addStretch()

        self.gpu_label = QLabel("")
        self.gpu_label.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self.gpu_label)

        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color: #00c853; font-size: 11px; font-weight: bold;")
        header.addWidget(self.time_label)

        layout.addLayout(header)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        # Batch table
        # Columns: ID | Video name | Output | Status text | Progress bar.
        # P3-13 splits the old single-column "Tiến Trình" into a status
        # text column and a real :class:`QProgressBar` so users can see
        # per-item progress at a glance during batch render.
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ['ID', 'Tên Video', 'Đầu Ra', 'Trạng Thái', 'Tiến Độ'])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 140)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(180)
        layout.addWidget(self.table)

        # Timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time_counter)

    def set_exporting_status(self, is_exporting: bool, encoder_name: str = ''):
        """Set export status and start/stop timer."""
        self._exporting = is_exporting
        if is_exporting:
            self._elapsed_seconds = 0
            self.status_label.setText(f"🎬 Đang render... ({encoder_name})")
            self.status_label.setStyleSheet("color: #00c853; font-size: 12px; font-weight: bold;")
            self._timer.start(1000)
        else:
            self._timer.stop()
            self.status_label.setText("✅ Hoàn thành!")
            self.status_label.setStyleSheet("color: #00c853; font-size: 12px; font-weight: bold;")

    def _update_time_counter(self):
        """Update elapsed time display."""
        self._elapsed_seconds += 1
        m = self._elapsed_seconds // 60
        s = self._elapsed_seconds % 60
        self.time_label.setText(f"⏱ {m:02d}:{s:02d}")

    def _detect_and_show_gpu_info(self):
        """Detect and display GPU info."""
        try:
            import torch
            if torch.cuda.is_available():
                name = torch.cuda.get_device_name(0)
                self.gpu_label.setText(f"🟢 GPU: {name}")
            else:
                self.gpu_label.setText("🔴 CPU Mode")
        except ImportError:
            self.gpu_label.setText("🔴 CPU Mode")

    # ═══════════════════════════════════════════════════════
    # P3-13: Batch row management
    # ═══════════════════════════════════════════════════════
    def populate_batch(self, items):
        """Reset the batch table and populate it from the queued list.

        Each item is a dict with at least ``video_path`` and
        ``output_path``. The progress column gets a real
        :class:`QProgressBar` so subsequent calls to
        :meth:`set_batch_item_progress` can update without rebuilding
        the row.
        """
        self.table.setRowCount(0)
        for idx, item in enumerate(items, start=1):
            row = self.table.rowCount()
            self.table.insertRow(row)
            video_name = os.path.basename(item.get('video_path', ''))
            output_name = os.path.basename(item.get('output_path', ''))
            self.table.setItem(row, 0, QTableWidgetItem(str(idx)))
            self.table.setItem(row, 1, QTableWidgetItem(video_name))
            self.table.setItem(row, 2, QTableWidgetItem(output_name))
            status_item = QTableWidgetItem("⏳ Chờ")
            status_item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(row, 3, status_item)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            bar.setFixedHeight(18)
            self.table.setCellWidget(row, 4, bar)

    def clear_batch(self):
        """Drop every row from the batch table."""
        self.table.setRowCount(0)

    def set_batch_item_status(self, row: int, label: str,
                              colour: str = '#888'):
        """Update the per-row status cell (col 3)."""
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, 3)
            if item is None:
                item = QTableWidgetItem(label)
                self.table.setItem(row, 3, item)
            else:
                item.setText(label)
            item.setForeground(_qcolor(colour))

    def set_batch_item_progress(self, row: int, percent: int):
        """Update the embedded :class:`QProgressBar` for ``row``."""
        if not (0 <= row < self.table.rowCount()):
            return
        widget = self.table.cellWidget(row, 4)
        if isinstance(widget, QProgressBar):
            widget.setValue(max(0, min(100, int(percent))))

    def mark_batch_item_done(self, row: int, ok: bool = True,
                             message: str = ''):
        """Convenience that flips the row to a terminal state."""
        if ok:
            self.set_batch_item_status(row, message or '✅ Xong', '#00c853')
            self.set_batch_item_progress(row, 100)
        else:
            self.set_batch_item_status(row, message or '❌ Lỗi', '#ff5252')


def _qcolor(value):
    """Convert a ``#rrggbb`` string into a ``QColor`` we can hand to Qt."""
    from PyQt6.QtGui import QColor
    return QColor(value)
