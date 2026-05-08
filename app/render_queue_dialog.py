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
    QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            'Job', 'Trạng thái', 'Tiến độ', 'Output', 'Cập nhật', 'ID',
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents)
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
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            render_status = job.stages.get(
                Stage.RENDER.value, StageStatus.PENDING.value)
            label = _STATUS_LABEL.get(render_status, render_status)
            updated = (datetime.fromtimestamp(job.updated_at).strftime(
                       '%Y-%m-%d %H:%M') if job.updated_at else '—')
            row_items = [
                QTableWidgetItem(job.name or '(không tên)'),
                QTableWidgetItem(label),
                QTableWidgetItem(f"{job.progress}%"),
                QTableWidgetItem(job.output_path or '—'),
                QTableWidgetItem(updated),
                QTableWidgetItem(job.id[:8]),
            ]
            for col, item in enumerate(row_items):
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.table.setItem(row, col, item)

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


__all__ = ['RenderQueueDialog']
