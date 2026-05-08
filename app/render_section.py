"""
Render Section
==============
Render status display with progress, GPU info, timer, and batch table.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView
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
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['ID', 'Tên Video', 'Đầu Ra', 'Tiến Trình'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(3, 120)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(150)
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
