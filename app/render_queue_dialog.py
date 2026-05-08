"""
Render Queue dialog
===================
Lightweight UI in front of :class:`app.domain.render_queue.RenderQueue`.
Lets the user inspect persistent jobs, retry / remove individual rows
and clear out completed entries.

The dialog is intentionally read-mostly: actually re-running a job is
done from the main render flow, so this dialog only mutates the queue
in three ways:

* **Retry** — flip the render stage back to ``PENDING`` and ask the
  caller to re-queue.
* **Remove** — drop a single row from the SQLite store.
* **Clear completed** — bulk-delete every ``DONE`` row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.domain.models import Job, Stage, StageStatus
from app.domain.render_queue import RenderQueue


_STATUS_LABEL = {
    StageStatus.PENDING.value: 'Đang chờ',
    StageStatus.RUNNING.value: 'Đang chạy',
    StageStatus.DONE.value: '✅ Xong',
    StageStatus.FAILED.value: '❌ Lỗi',
    StageStatus.CANCELLED.value: '⏸ Đã huỷ',
    StageStatus.SKIPPED.value: '— Bỏ qua',
}


class RenderQueueDialog(QDialog):
    """Show the persistent render queue and let the user manage it."""

    def __init__(self, queue: RenderQueue,
                 on_retry: Optional[Callable[[Job], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._queue = queue
        self._on_retry = on_retry
        self.setWindowTitle("📋 Hàng Đợi Render")
        self.resize(780, 480)
        self._build_ui()
        self.refresh()

    # ── UI ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QLabel("Các job render được lưu lại trên đĩa và có thể "
                        "tiếp tục sau khi mở lại app.")
        header.setWordWrap(True)
        layout.addWidget(header)

        # P3-4: filter row — search by name/output + status combo. We
        # filter client-side because the queue is small (<1k rows in
        # typical use); a SQL-side filter would be marginal at best.
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Lọc:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Tên hoặc output chứa...")
        self.search_input.textChanged.connect(self.refresh)
        filter_row.addWidget(self.search_input, 1)

        filter_row.addWidget(QLabel("Trạng thái:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem('Tất cả', userData=None)
        for status_value, label in _STATUS_LABEL.items():
            self.status_filter.addItem(label, userData=status_value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.status_filter)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #888;")
        filter_row.addWidget(self._summary_label)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            'Job', 'Trạng thái', 'Tiến độ', 'Output',
            'Thời gian', 'Cập nhật', 'ID',
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.btn_refresh = QPushButton("🔄 Làm mới")
        self.btn_refresh.clicked.connect(self.refresh)
        buttons.addWidget(self.btn_refresh)

        self.btn_retry = QPushButton("↻ Render lại")
        self.btn_retry.clicked.connect(self._retry_selected)
        buttons.addWidget(self.btn_retry)

        self.btn_remove = QPushButton("🗑 Xoá")
        self.btn_remove.clicked.connect(self._remove_selected)
        buttons.addWidget(self.btn_remove)

        self.btn_clear = QPushButton("🧹 Xoá job đã xong")
        self.btn_clear.clicked.connect(self._clear_completed)
        buttons.addWidget(self.btn_clear)

        buttons.addStretch()
        self.btn_close = QPushButton("Đóng")
        self.btn_close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

    # ── Slots ──────────────────────────────────────────────
    def refresh(self) -> None:
        jobs: List[Job] = self._queue.all()
        # P3-4: client-side filter against the search box + status combo.
        needle = (
            self.search_input.text().strip().lower()
            if hasattr(self, 'search_input') else '')
        target_status = (
            self.status_filter.currentData()
            if hasattr(self, 'status_filter') else None)
        filtered: List[Job] = []
        for job in jobs:
            render_status = job.stages.get(
                Stage.RENDER.value, StageStatus.PENDING.value)
            if target_status and render_status != target_status:
                continue
            if needle:
                blob = ' '.join([
                    job.name or '',
                    job.output_path or '',
                    job.id,
                ]).lower()
                if needle not in blob:
                    continue
            filtered.append(job)
        self.table.setRowCount(len(filtered))
        for row, job in enumerate(filtered):
            render_status = job.stages.get(
                Stage.RENDER.value, StageStatus.PENDING.value)
            label = _STATUS_LABEL.get(render_status, render_status)
            updated = (datetime.fromtimestamp(job.updated_at).strftime(
                       '%Y-%m-%d %H:%M') if job.updated_at else '—')
            duration = _format_duration(job.created_at, job.updated_at)
            row_items = [
                QTableWidgetItem(job.name or '(không tên)'),
                QTableWidgetItem(label),
                QTableWidgetItem(f"{job.progress}%"),
                QTableWidgetItem(job.output_path or '—'),
                QTableWidgetItem(duration),
                QTableWidgetItem(updated),
                QTableWidgetItem(job.id[:8]),
            ]
            for col, item in enumerate(row_items):
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.table.setItem(row, col, item)
        if hasattr(self, '_summary_label'):
            self._summary_label.setText(
                f"{len(filtered)}/{len(jobs)} job")

    def _selected_job_id(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _retry_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, "Hàng đợi", "Chọn một job trước.")
            return
        job = self._queue.get(job_id)
        if not job:
            return
        # Flip render back to PENDING and persist; caller can re-queue.
        job.stages[Stage.RENDER.value] = StageStatus.PENDING.value
        job.errors.pop(Stage.RENDER.value, None)
        job.progress = 0
        self._queue.update(job)
        if self._on_retry:
            try:
                self._on_retry(job)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Hàng đợi",
                                    f"Không thể bắt đầu lại job: {exc}")
        self.refresh()

    def _remove_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, "Hàng đợi", "Chọn một job trước.")
            return
        self._queue.remove(job_id)
        self.refresh()

    def _clear_completed(self) -> None:
        removed = self._queue.clear_completed()
        QMessageBox.information(self, "Hàng đợi",
                                f"Đã xoá {removed} job đã hoàn tất.")
        self.refresh()


def _format_duration(start: float, end: float) -> str:
    """Render ``end - start`` as ``MM:SS`` or ``HH:MM:SS``.

    Returns ``—`` if the times are missing or non-positive (e.g. job
    that never moved out of PENDING).
    """
    if not start or not end or end <= start:
        return '—'
    total = int(end - start)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


__all__ = ['RenderQueueDialog']
