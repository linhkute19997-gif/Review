"""
Subtitle Edit Section
=====================
Two-column SRT editor (original + translated) with pagination.

Phase 2 (P2-10) replaces the previous ``QTableWidget`` (which
allocates a ``QTableWidgetItem`` per cell and re-creates them on
every page flip / batch translation update) with a virtualised
``QTableView`` + :class:`SubtitleTableModel`. The model holds plain
dict references into the master subtitles list so:

* Loading a 5 000-line SRT no longer pays 15 000 Qt allocations
  per page render.
* Translation updates flow as targeted ``dataChanged`` signals
  instead of full table re-population.
* The same widget handles 50 000+ rows without UI hitches.

The public API (``load_subtitles``, ``update_translated``,
``_subtitles``, ``_sync_table_edits``) is preserved so the rest of
the app keeps working unchanged.
"""

from typing import Dict, List

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView,
    QVBoxLayout, QWidget,
)


class SubtitleTableModel(QAbstractTableModel):
    """Virtual model: rows == one paginated slice of ``_subtitles``."""

    HEADERS = ['#', 'Phụ đề gốc', 'Phụ đề đã dịch']

    def __init__(self, parent=None):
        super().__init__(parent)
        # ``_rows`` holds *references* into the master list owned by
        # ``SubtitleEditSection`` — mutating a dict here mutates the
        # master too, so user edits are persisted without a copy.
        self._rows: List[Dict] = []
        self._page_offset = 0

    def set_rows(self, rows: List[Dict], page_offset: int) -> None:
        """Swap the visible page in one transaction."""
        self.beginResetModel()
        self._rows = rows
        self._page_offset = page_offset
        self.endResetModel()

    def update_translated(self, abs_index: int, text: str) -> bool:
        """If ``abs_index`` is on the visible page, emit a targeted update."""
        local = abs_index - self._page_offset
        if 0 <= local < len(self._rows):
            self._rows[local]['translated_text'] = text
            idx = self.index(local, 2)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
            return True
        return False

    # ── QAbstractTableModel overrides ────────────────────────────
    def rowCount(self, parent=QModelIndex()):  # noqa: B008 — Qt API
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: B008 — Qt API
        return 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (role == Qt.ItemDataRole.DisplayRole
                and orientation == Qt.Orientation.Horizontal
                and 0 <= section < len(self.HEADERS)):
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._rows)):
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            sub = self._rows[row]
            if col == 0:
                return str(sub.get('index', self._page_offset + row + 1))
            if col == 1:
                return sub.get('text', '')
            if col == 2:
                return sub.get('translated_text', '')
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 0:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return base
        if index.column() == 2:
            return base | Qt.ItemFlag.ItemIsEditable
        # Index + original text are read-only.
        return base & ~Qt.ItemFlag.ItemIsEditable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if index.column() != 2:
            return False
        row = index.row()
        if 0 <= row < len(self._rows):
            self._rows[row]['translated_text'] = str(value)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
            return True
        return False


class PageNavWidget(QWidget):
    """Compact page navigation widget for paginated subtitle editing."""

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
        # Clear existing buttons.
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Don't crowd the UI with thousands of buttons. Show the
        # current page ± 4 plus first/last with a "…" gap.
        if self._total <= 12:
            visible = list(range(self._total))
        else:
            visible = sorted({
                0, self._total - 1,
                *range(max(0, self._current - 4),
                       min(self._total, self._current + 5)),
            })

        prev_idx = -2
        for i in visible:
            if prev_idx >= 0 and i - prev_idx > 1:
                gap = QLabel("…")
                gap.setStyleSheet("color: #666; padding: 0 4px;")
                self.layout.addWidget(gap)
            btn = QPushButton(str(i + 1))
            btn.setFixedSize(30, 24)
            btn.setStyleSheet(
                "background: #00c853; color: white; font-weight: bold;"
                " border-radius: 4px;"
                if i == self._current else
                "background: #2a2a4e; color: #888; border-radius: 4px;"
            )
            btn.clicked.connect(
                lambda _checked, page=i: self._on_page_click(page))
            self.layout.addWidget(btn)
            prev_idx = i
        self.layout.addStretch()

    def _on_page_click(self, page: int):
        self._current = page
        self._rebuild()
        if self._page_changed_callback:
            self._page_changed_callback(page)


class SubtitleEditSection(QWidget):
    """Two-column SRT editor with pagination, backed by a virtual model."""

    ITEMS_PER_PAGE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subtitles: List[Dict] = []
        self._srt_files: List = []
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
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #888;")
        header.addWidget(self._count_label)
        layout.addLayout(header)

        # Virtualised view + model.
        self._model = SubtitleTableModel(self)
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableView.EditTrigger.DoubleClicked
            | QTableView.EditTrigger.EditKeyPressed
            | QTableView.EditTrigger.SelectedClicked)
        self.table.setWordWrap(True)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        layout.addWidget(self.table, 1)

        # Pagination
        self.page_nav = PageNavWidget()
        self.page_nav.on_page_changed(self._on_page_changed)
        layout.addWidget(self.page_nav)

    # ── Public API ────────────────────────────────────────────────
    def load_subtitles(self, subtitles: List[Dict]):
        """Load subtitle entries into the editor."""
        self._subtitles = subtitles
        total = len(subtitles)
        total_pages = max(
            1, (total + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE)
        self.page_nav.set_total(total_pages)
        self.page_nav.set_current(0)
        self._count_label.setText(f"{total} dòng")
        self._load_page(0)

    def update_translated(self, index: int, text: str):
        """Update translated text for a specific subtitle.

        Mutates the master list (always) and emits a targeted
        ``dataChanged`` for the model only if the row is on the
        visible page. Other pages pick up the change automatically
        the next time they're loaded.
        """
        if 0 <= index < len(self._subtitles):
            self._subtitles[index]['translated_text'] = text
            self._model.update_translated(index, text)

    # ── Internals ────────────────────────────────────────────────
    def _on_page_changed(self, page: int):
        # The view writes through ``setData`` directly, so no manual
        # sync is needed here.
        self._load_page(page)

    def _load_page(self, page: int):
        """Swap the visible slice into the virtual model."""
        start = page * self.ITEMS_PER_PAGE
        end = min(start + self.ITEMS_PER_PAGE, len(self._subtitles))
        self._model.set_rows(self._subtitles[start:end], start)

    def _sync_table_edits(self):
        """No-op kept for API compatibility.

        With ``QAbstractTableModel.setData`` writing through to the
        master list immediately, there's nothing left to flush.
        Callers (``main_window``) still invoke this defensively.
        """
        return

    def set_srt_files(self, files: list):
        """Set multiple SRT files for batch editing."""
        self._srt_files = files
        self._current_srt_page = 0
