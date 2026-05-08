"""
Overlay System — Draggable text and blur regions on video preview.

Logos are rendered through ``ConfigSection`` + FFmpeg ``overlay`` filter,
not as draggable scene items. The legacy ``DraggableLogo`` class was
unused and has been removed.
"""
from PyQt6.QtWidgets import (
    QGraphicsRectItem, QGraphicsItem, QMenu,
    QColorDialog, QInputDialog,
)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QFont, QColor, QBrush, QPen,
)


class SelectableOverlayItem(QGraphicsRectItem):
    """Base class for draggable, resizable overlay items on the video."""

    HANDLE_SIZE = 8

    def __init__(self, x, y, w, h, parent=None):
        super().__init__(x, y, w, h, parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._selected = False
        self._resize_edge = None
        self._drag_start = None
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setPen(QPen(QColor("#00c853"), 2, Qt.PenStyle.DashLine))
        else:
            self.setPen(QPen(QColor(255, 255, 255, 60), 1))

    def setSelected(self, selected):
        self._selected = selected
        self._update_style()
        super().setSelected(selected)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self._selected:
            rect = self.rect()
            painter.setPen(QPen(QColor("#00c853"), 1))
            painter.setBrush(QBrush(QColor("#00c853")))
            handles = [
                rect.topLeft(), rect.topRight(),
                rect.bottomLeft(), rect.bottomRight(),
            ]
            for h in handles:
                painter.drawRect(QRectF(
                    h.x() - self.HANDLE_SIZE / 2, h.y() - self.HANDLE_SIZE / 2,
                    self.HANDLE_SIZE, self.HANDLE_SIZE))

    def _get_resize_edge(self, pos):
        rect = self.rect()
        hs = self.HANDLE_SIZE
        if abs(pos.x() - rect.right()) < hs and abs(pos.y() - rect.bottom()) < hs:
            return 'br'
        if abs(pos.x() - rect.left()) < hs and abs(pos.y() - rect.top()) < hs:
            return 'tl'
        if abs(pos.x() - rect.right()) < hs and abs(pos.y() - rect.top()) < hs:
            return 'tr'
        if abs(pos.x() - rect.left()) < hs and abs(pos.y() - rect.bottom()) < hs:
            return 'bl'
        return None

    def mousePressEvent(self, event):
        self._resize_edge = self._get_resize_edge(event.pos())
        self._drag_start = event.pos()
        if self._resize_edge:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edge and self._drag_start:
            delta = event.pos() - self._drag_start
            rect = self.rect()
            if 'r' in self._resize_edge:
                rect.setRight(rect.right() + delta.x())
            if 'b' in self._resize_edge:
                rect.setBottom(rect.bottom() + delta.y())
            if 'l' in self._resize_edge:
                rect.setLeft(rect.left() + delta.x())
            if 't' in self._resize_edge:
                rect.setTop(rect.top() + delta.y())
            if rect.width() > 20 and rect.height() > 20:
                self.setRect(rect)
            self._drag_start = event.pos()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        self.setSelected(False)
        super().focusOutEvent(event)

    def _show_context_menu(self, event):
        menu = QMenu()
        delete_action = menu.addAction("🗑 Xóa")
        dup_action = menu.addAction("📋 Nhân bản")
        action = menu.exec(event.screenPos())
        if action == delete_action:
            scene = self.scene()
            if scene:
                scene.removeItem(self)
        elif action == dup_action:
            self._duplicate()

    def _duplicate(self):
        pass  # Override in subclass


class DraggableTextItem(SelectableOverlayItem):
    """Text overlay with font/color/size customization."""

    def __init__(self, x, y, text="Text", font_size=20, color="#ffffff", parent=None):
        super().__init__(x, y, 200, 40, parent)
        self.text_content = text
        self.font_size = font_size
        self.text_color = color
        self.setBrush(QBrush(QColor(0, 0, 0, 80)))

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.setFont(QFont("Arial", self.font_size))
        painter.setPen(QPen(QColor(self.text_color)))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text_content)

    def _show_context_menu(self, event):
        menu = QMenu()
        edit_action = menu.addAction("✏️ Sửa text")
        color_action = menu.addAction("🎨 Đổi màu")
        size_action = menu.addAction("📏 Cỡ chữ")
        delete_action = menu.addAction("🗑 Xóa")
        dup_action = menu.addAction("📋 Nhân bản")

        action = menu.exec(event.screenPos())
        if action == edit_action:
            text, ok = QInputDialog.getText(None, "Sửa Text", "Nội dung:", text=self.text_content)
            if ok and text:
                self.text_content = text
                self.update()
        elif action == color_action:
            color = QColorDialog.getColor(QColor(self.text_color))
            if color.isValid():
                self.text_color = color.name()
                self.update()
        elif action == size_action:
            size, ok = QInputDialog.getInt(None, "Cỡ Chữ", "Font size:", self.font_size, 8, 120)
            if ok:
                self.font_size = size
                self.update()
        elif action == delete_action:
            if self.scene():
                self.scene().removeItem(self)
        elif action == dup_action:
            self._duplicate()

    def _duplicate(self):
        if self.scene():
            new_item = DraggableTextItem(
                self.pos().x() + 20, self.pos().y() + 20,
                self.text_content, self.font_size, self.text_color)
            self.scene().addItem(new_item)

    def contextMenuEvent(self, event):
        self._show_context_menu(event)

    def get_data(self):
        # Combine scene pos + local rect offset for accurate FFmpeg coordinates
        return {
            'text': self.text_content,
            'font_size': self.font_size,
            'color': self.text_color,
            'x': self.pos().x() + self.rect().x(),
            'y': self.pos().y() + self.rect().y(),
            'width': self.rect().width(),
            'height': self.rect().height(),
        }


class DraggableBlurRegion(SelectableOverlayItem):
    """Blur region overlay with adjustable blur strength."""

    def __init__(self, x, y, w=100, h=80, parent=None):
        super().__init__(x, y, w, h, parent)
        self.setBrush(QBrush(QColor(100, 100, 255, 60)))
        self.blur_strength = 15

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.setPen(QPen(QColor(100, 100, 255, 200)))
        painter.setFont(QFont("Arial", 10))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                        f"Blur ({self.blur_strength})")

    def _show_context_menu(self, event):
        menu = QMenu()
        strength_action = menu.addAction("💪 Độ mờ")
        delete_action = menu.addAction("🗑 Xóa")
        dup_action = menu.addAction("📋 Nhân bản")

        action = menu.exec(event.screenPos())
        if action == strength_action:
            val, ok = QInputDialog.getInt(None, "Blur", "Độ mờ:", self.blur_strength, 1, 100)
            if ok:
                self.blur_strength = val
                self.update()
        elif action == delete_action:
            if self.scene():
                self.scene().removeItem(self)
        elif action == dup_action:
            self._duplicate()

    def _duplicate(self):
        if self.scene():
            new_item = DraggableBlurRegion(
                self.pos().x() + 20, self.pos().y() + 20,
                self.rect().width(), self.rect().height())
            new_item.blur_strength = self.blur_strength
            self.scene().addItem(new_item)

    def contextMenuEvent(self, event):
        self._show_context_menu(event)

    def get_region_data(self):
        # Combine scene pos + local rect offset for accurate FFmpeg coordinates
        return {
            'x': self.pos().x() + self.rect().x(),
            'y': self.pos().y() + self.rect().y(),
            'width': self.rect().width(),
            'height': self.rect().height(),
            'strength': self.blur_strength,
        }


class AddTextDialog:
    """Static method to show add-text dialog."""

    @staticmethod
    def get_data(parent=None):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton
        dialog = QDialog(parent)
        dialog.setWindowTitle("Thêm Text")
        dialog.resize(350, 180)
        layout = QVBoxLayout(dialog)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Nội dung:"))
        text_input = QLineEdit("Text mới")
        row1.addWidget(text_input)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Cỡ chữ:"))
        size_input = QSpinBox()
        size_input.setRange(8, 120)
        size_input.setValue(20)
        row2.addWidget(size_input)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Màu:"))
        color_btn = QPushButton()
        color_btn.setFixedSize(30, 24)
        color_btn.setStyleSheet("background: #ffffff; border: 1px solid #666;")
        selected_color = ['#ffffff']

        def pick():
            c = QColorDialog.getColor(QColor(selected_color[0]))
            if c.isValid():
                selected_color[0] = c.name()
                color_btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #666;")
        color_btn.clicked.connect(pick)
        row3.addWidget(color_btn)
        layout.addLayout(row3)

        btn_ok = QPushButton("✅ Thêm")
        btn_ok.clicked.connect(dialog.accept)
        layout.addWidget(btn_ok)

        if dialog.exec():
            return {
                'text': text_input.text(),
                'font_size': size_input.value(),
                'color': selected_color[0],
            }
        return None
