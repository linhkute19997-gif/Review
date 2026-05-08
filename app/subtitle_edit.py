"""
Subtitle Edit Section
=====================
Two-column SRT editor (original + translated) with pagination.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel
)
from PyQt6.QtCore import Qt
from typing import List, Dict


class PageNavWidget(QWidget):
    """Page navigation widget for pagination."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0
        self._current = 0
        self._page_changed_callback = None
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)

    def set_total(self, total: int):
        self._total = total
        self._rebuild()

    def set_current(self, current: int):
        self._current = current
        self._rebuild()

    def on_page_changed(self, callback):
        self._page_changed_callback = callback

    def _rebuild(self):
        # Clear existing buttons
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i in range(self._total):
            btn = QPushButton(str(i + 1))
            btn.setFixedSize(30, 24)
            btn.setStyleSheet(
                "background: #00c853; color: white; font-weight: bold; border-radius: 4px;"
                if i == self._current else
                "background: #2a2a4e; color: #888; border-radius: 4px;"
            )
            btn.clicked.connect(lambda checked, page=i: self._on_page_click(page))
            self.layout.addWidget(btn)
        self.layout.addStretch()

    def _on_page_click(self, page: int):
        self._current = page
        self._rebuild()
        if self._page_changed_callback:
            self._page_changed_callback(page)


class SubtitleEditSection(QWidget):
    """Two-column SRT editor with pagination."""

    ITEMS_PER_PAGE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subtitles = []
        self._srt_files = []
        self._current_srt_page = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel("📝 Chỉnh Sửa Phụ Đề"))
        header.addStretch()
        layout.addLayout(header)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['#', 'Phụ đề gốc', 'Phụ đề đã dịch'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)

        # Pagination
        self.page_nav = PageNavWidget()
        self.page_nav.on_page_changed(self._on_page_changed)
        layout.addWidget(self.page_nav)

    def load_subtitles(self, subtitles: List[Dict]):
        """Load subtitle entries into the editor."""
        self._subtitles = subtitles
        total_pages = max(1, (len(subtitles) + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE)
        self.page_nav.set_total(total_pages)
        self.page_nav.set_current(0)
        self._load_page(0)

    def update_translated(self, index: int, text: str):
        """Update translated text for a specific subtitle."""
        if 0 <= index < len(self._subtitles):
            self._subtitles[index]['translated_text'] = text
            # Update table if visible
            page_start = self.page_nav._current * self.ITEMS_PER_PAGE
            page_end = page_start + self.ITEMS_PER_PAGE
            if page_start <= index < page_end:
                row = index - page_start
                if row < self.table.rowCount():
                    item = self.table.item(row, 2)
                    if item:
                        item.setText(text)

    def _on_page_changed(self, page: int):
        self._sync_table_edits()
        self._load_page(page)

    def _load_page(self, page: int):
        """Load a specific page of subtitles."""
        self._syncing = True  # Prevent recursive sync
        start = page * self.ITEMS_PER_PAGE
        end = min(start + self.ITEMS_PER_PAGE, len(self._subtitles))
        page_subs = self._subtitles[start:end]

        self.table.setRowCount(len(page_subs))
        for row, sub in enumerate(page_subs):
            # Index
            idx_item = QTableWidgetItem(str(sub.get('index', start + row + 1)))
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, idx_item)

            # Original text
            orig_item = QTableWidgetItem(sub.get('text', ''))
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, orig_item)

            # Translated text (editable)
            trans_item = QTableWidgetItem(sub.get('translated_text', ''))
            self.table.setItem(row, 2, trans_item)
        self._syncing = False

    def _on_cell_changed(self, row, col):
        """Sync user edits in column 2 back to _subtitles."""
        if getattr(self, '_syncing', False):
            return
        if col == 2:
            page_start = self.page_nav._current * self.ITEMS_PER_PAGE
            abs_index = page_start + row
            if 0 <= abs_index < len(self._subtitles):
                item = self.table.item(row, 2)
                if item:
                    self._subtitles[abs_index]['translated_text'] = item.text()

    def _sync_table_edits(self):
        """Flush all current table edits back to _subtitles."""
        page_start = self.page_nav._current * self.ITEMS_PER_PAGE
        for row in range(self.table.rowCount()):
            abs_index = page_start + row
            if 0 <= abs_index < len(self._subtitles):
                item = self.table.item(row, 2)
                if item:
                    self._subtitles[abs_index]['translated_text'] = item.text()

    def set_srt_files(self, files: list):
        """Set multiple SRT files for batch editing."""
        self._srt_files = files
        self._current_srt_page = 0
