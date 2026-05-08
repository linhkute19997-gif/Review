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

from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt
)
from PyQt6.QtGui import QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableView, QVBoxLayout, QWidget,
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
        if not (0 <= row < len(self._rows)):
            return False
        new_text = str(value)
        old_text = self._rows[row].get('translated_text', '')
        if new_text == old_text:
            return True
        # Route the edit through the parent's undo stack so Ctrl+Z / Ctrl+Y
        # work consistently across paginated edits and search/replace.
        section = self.parent()
        if isinstance(section, SubtitleEditSection):
            section._push_edit_command(
                self._page_offset + row, old_text, new_text)
        else:
            self._rows[row]['translated_text'] = new_text
            self.dataChanged.emit(
                index, index, [Qt.ItemDataRole.DisplayRole])
        return True

    def refresh_cell(self, abs_index: int) -> None:
        """Force a redraw for ``abs_index`` if it's on the visible page."""
        local = abs_index - self._page_offset
        if 0 <= local < len(self._rows):
            idx = self.index(local, 2)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])


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


class _TranslatedEditCommand(QUndoCommand):
    """Undoable edit of a single ``translated_text`` cell (P3-6)."""

    def __init__(self, section: 'SubtitleEditSection', abs_index: int,
                 old_text: str, new_text: str,
                 description: str = 'Sửa phụ đề'):
        super().__init__(description)
        self._section = section
        self._index = abs_index
        self._old = old_text
        self._new = new_text

    def redo(self) -> None:  # noqa: D401 — Qt API
        self._section._set_translated(self._index, self._new)

    def undo(self) -> None:  # noqa: D401 — Qt API
        self._section._set_translated(self._index, self._old)


class _BulkReplaceCommand(QUndoCommand):
    """Undoable batch replace from the search/replace dialog (P3-5/P3-6)."""

    def __init__(self, section: 'SubtitleEditSection',
                 changes: List[tuple], description: str = 'Tìm & thay'):
        # ``changes`` is a list of ``(abs_index, old_text, new_text)``.
        super().__init__(f"{description} ({len(changes)} dòng)")
        self._section = section
        self._changes = changes

    def redo(self) -> None:  # noqa: D401 — Qt API
        for idx, _old, new in self._changes:
            self._section._set_translated(idx, new)

    def undo(self) -> None:  # noqa: D401 — Qt API
        for idx, old, _new in self._changes:
            self._section._set_translated(idx, old)


class SearchReplaceDialog(QDialog):
    """Find-and-replace dialog over the translated subtitle column."""

    def __init__(self, section: 'SubtitleEditSection', parent=None):
        super().__init__(parent or section)
        self._section = section
        self.setWindowTitle("Tìm & Thay (Ctrl+H)")
        self.resize(420, 200)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Tìm:"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Nhập chuỗi cần tìm...")
        find_row.addWidget(self.find_input)
        layout.addLayout(find_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Thay bằng:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("(để trống = xoá)")
        replace_row.addWidget(self.replace_input)
        layout.addLayout(replace_row)

        opts = QHBoxLayout()
        self.chk_case = QCheckBox("Phân biệt hoa/thường")
        opts.addWidget(self.chk_case)
        self.chk_original = QCheckBox("Tìm trong cột gốc (chỉ hiển thị)")
        opts.addWidget(self.chk_original)
        layout.addLayout(opts)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888;")
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        btn_count = QPushButton("Đếm")
        btn_count.clicked.connect(self._count)
        buttons.addWidget(btn_count)
        btn_replace_all = QPushButton("Thay tất cả")
        btn_replace_all.setObjectName("btnTienHanh")
        btn_replace_all.clicked.connect(self._replace_all)
        buttons.addWidget(btn_replace_all)
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)

    def _matches(self, needle: str, hay: str) -> bool:
        if not self.chk_case.isChecked():
            return needle.lower() in hay.lower()
        return needle in hay

    def _count(self) -> None:
        needle = self.find_input.text()
        if not needle:
            self._status.setText("Nhập chuỗi cần tìm trước.")
            return
        col = 'text' if self.chk_original.isChecked() else 'translated_text'
        hits = sum(1 for sub in self._section._subtitles
                   if self._matches(needle, sub.get(col, '') or ''))
        self._status.setText(f"Tìm thấy {hits} khớp trong cột '{col}'.")

    def _replace_all(self) -> None:
        needle = self.find_input.text()
        if not needle:
            self._status.setText("Nhập chuỗi cần tìm trước.")
            return
        # The original text column is read-only on purpose; replacing
        # there silently would desync the editor from the source SRT.
        if self.chk_original.isChecked():
            QMessageBox.information(
                self, "Tìm & Thay",
                "Cột phụ đề gốc là chỉ-đọc. Hãy bỏ tick tuỳ chọn này"
                " để thay trên bản đã dịch.")
            return
        replacement = self.replace_input.text()
        case_sensitive = self.chk_case.isChecked()
        changes: List[tuple] = []
        for idx, sub in enumerate(self._section._subtitles):
            old = sub.get('translated_text', '') or ''
            if case_sensitive:
                if needle not in old:
                    continue
                new = old.replace(needle, replacement)
            else:
                if needle.lower() not in old.lower():
                    continue
                # Case-insensitive replace via simple lowercase search.
                new = _ireplace(old, needle, replacement)
            if new != old:
                changes.append((idx, old, new))
        if not changes:
            self._status.setText("Không có dòng nào khớp.")
            return
        self._section._apply_bulk_replace(changes)
        self._status.setText(f"Đã thay {len(changes)} dòng.")


def _ireplace(text: str, needle: str, replacement: str) -> str:
    """Case-insensitive ``str.replace``."""
    if not needle:
        return text
    out = []
    lower = text.lower()
    nlower = needle.lower()
    i = 0
    while i < len(text):
        idx = lower.find(nlower, i)
        if idx == -1:
            out.append(text[i:])
            break
        out.append(text[i:idx])
        out.append(replacement)
        i = idx + len(needle)
    return ''.join(out)


class SubtitleEditSection(QWidget):
    """Two-column SRT editor with pagination, backed by a virtual model."""

    ITEMS_PER_PAGE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subtitles: List[Dict] = []
        self._srt_files: List = []
        self._current_srt_page = 0
        # P3-6: each user edit is wrapped in a QUndoCommand so Ctrl+Z
        # / Ctrl+Y reverse / replay them. The stack also drives bulk
        # replace from the search dialog.
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(200)
        self._search_dialog: Optional[SearchReplaceDialog] = None
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
        """Update translated text from a non-UI source (e.g. translator thread).

        Skips the undo stack: machine-driven updates shouldn't end up
        as user-visible undo steps. Manual edits flow through
        :meth:`SubtitleTableModel.setData` instead.
        """
        if 0 <= index < len(self._subtitles):
            self._subtitles[index]['translated_text'] = text
            self._model.refresh_cell(index)

    # ── Undo / redo (P3-6) ───────────────────────────────────────
    def undo(self) -> None:
        if self._undo_stack.canUndo():
            self._undo_stack.undo()

    def redo(self) -> None:
        if self._undo_stack.canRedo():
            self._undo_stack.redo()

    def _push_edit_command(self, abs_index: int,
                           old_text: str, new_text: str) -> None:
        """Wrap a manual cell edit in a :class:`QUndoCommand`."""
        self._undo_stack.push(
            _TranslatedEditCommand(self, abs_index, old_text, new_text))

    def _set_translated(self, abs_index: int, text: str) -> None:
        """Low-level write — used by undo/redo commands."""
        if 0 <= abs_index < len(self._subtitles):
            self._subtitles[abs_index]['translated_text'] = text
            self._model.refresh_cell(abs_index)

    def _apply_bulk_replace(self, changes: List[tuple]) -> None:
        """Push a bulk replace command — see :class:`SearchReplaceDialog`."""
        if changes:
            self._undo_stack.push(_BulkReplaceCommand(self, changes))

    # ── Search / Replace (P3-5) ─────────────────────────────────
    def open_search_replace(self) -> None:
        """Show (or focus) the find-and-replace dialog."""
        if self._search_dialog is None:
            self._search_dialog = SearchReplaceDialog(self, self)
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.activateWindow()

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
