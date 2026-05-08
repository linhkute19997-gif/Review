"""
Video Player Section
====================
QMediaPlayer-based video preview with overlay support.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QGraphicsView, QGraphicsScene, QSizePolicy, QStackedWidget
)
from PyQt6.QtCore import Qt, QSize, QSizeF, QUrl, QRectF, QTimer
from PyQt6.QtGui import QFont, QPixmap, QIcon, QPainter, QColor, QBrush, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

from app.utils.srt_parser import format_time_display


class VideoPlayerSection(QWidget):
    """Video player with overlay support for preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._overlays = []
        self._video_files = []
        self._current_page = 0

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Graphics scene for video + overlays
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setStyleSheet("background: #000; border: 1px solid #2a2a4e;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.view)

        # Video item
        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)

        # Media player
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.5)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_item)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)

        # Subtitle overlay label
        self.subtitle_label = QLabel("", self.view)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setStyleSheet("""
            QLabel {
                background: rgba(0,0,0,0.6);
                color: white;
                padding: 8px;
                border-radius: 4px;
                font-size: 13px;
            }
        """)
        self.subtitle_label.hide()

        # Top/bottom border overlays
        self.top_border = QLabel(self.view)
        self.top_border.setStyleSheet("background: #ffff00;")
        self.top_border.hide()

        self.bottom_border = QLabel(self.view)
        self.bottom_border.setStyleSheet("background: #000000;")
        self.bottom_border.hide()

        # Controls bar
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(36, 28)
        self.btn_play.clicked.connect(self.toggle_play)
        controls.addWidget(self.btn_play)

        self.btn_replay = QPushButton("⟲")
        self.btn_replay.setFixedSize(36, 28)
        self.btn_replay.clicked.connect(self.replay)
        controls.addWidget(self.btn_replay)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._seek)
        controls.addWidget(self.slider, 1)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #888; font-size: 11px;")
        self.time_label.setFixedWidth(90)
        controls.addWidget(self.time_label)

        layout.addLayout(controls)

    # ═══════════════════════════════════════════════════════
    # Video loading
    # ═══════════════════════════════════════════════════════

    def load_video(self, path: str):
        """Load a video file into the player."""
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.player.pause()
        self.btn_play.setText("▶")

    def set_video_files(self, files: list):
        """Set batch video files for navigation."""
        self._video_files = files
        self._current_page = 0
        if files:
            self.load_video(files[0])

    # ═══════════════════════════════════════════════════════
    # Playback controls
    # ═══════════════════════════════════════════════════════

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def play(self):
        self.player.play()
        self.btn_play.setText("⏸")

    def pause(self):
        self.player.pause()
        self.btn_play.setText("▶")

    def replay(self):
        self.player.setPosition(0)
        self.play()

    def _seek(self, position):
        self.player.setPosition(position)

    def _on_position_changed(self, position):
        self.slider.setValue(position)
        self._update_time_label()

    def _on_duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self._update_time_label()
        # Fit video to view
        QTimer.singleShot(100, self._fit_video_to_view)

    def _update_time_label(self):
        pos = self.player.position()
        dur = self.player.duration()
        self.time_label.setText(
            f"{format_time_display(pos)} / {format_time_display(dur)}"
        )

    def _fit_video_to_view(self):
        """Scale video to fit the view area."""
        native = self.video_item.nativeSize()
        if native.width() > 0 and native.height() > 0:
            vw = self.view.width() - 4
            vh = self.view.height() - 4
            scale_w = vw / native.width()
            scale_h = vh / native.height()
            scale = min(scale_w, scale_h)
            self.video_item.setSize(QSizeF(native.width() * scale, native.height() * scale))
            self.scene.setSceneRect(self.video_item.boundingRect())
            self.view.fitInView(self.video_item, Qt.AspectRatioMode.KeepAspectRatio)

    # ═══════════════════════════════════════════════════════
    # Overlay management
    # ═══════════════════════════════════════════════════════

    def show_subtitle_bar(self, visible: bool):
        """Show/hide subtitle preview bar."""
        if visible:
            self.subtitle_label.show()
        else:
            self.subtitle_label.hide()

    def update_subtitle_opacity(self, value: int):
        """Update subtitle opacity (1-200%)."""
        opacity = value / 100.0
        self.subtitle_label.setStyleSheet(f"""
            QLabel {{
                background: rgba(0,0,0,{min(opacity * 0.6, 1.0)});
                color: rgba(255,255,255,{min(opacity, 1.0)});
                padding: 8px;
                border-radius: 4px;
                font-size: 13px;
            }}
        """)

    def set_top_border(self, enabled: bool, color: str = '#ffff00',
                       height: int = 40, text: str = '', text_color: str = '#000000'):
        """Set top border overlay."""
        if enabled:
            self.top_border.setFixedHeight(height)
            self.top_border.setStyleSheet(f"background: {color}; color: {text_color}; "
                                          f"font-size: 14px; font-weight: bold;")
            self.top_border.setText(text)
            self.top_border.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.top_border.setGeometry(0, 0, self.view.width(), height)
            self.top_border.show()
        else:
            self.top_border.hide()

    def set_bottom_border(self, enabled: bool, color: str = '#000000',
                          height: int = 40, text: str = '', text_color: str = '#ffffff'):
        """Set bottom border overlay."""
        if enabled:
            self.bottom_border.setFixedHeight(height)
            self.bottom_border.setStyleSheet(f"background: {color}; color: {text_color}; "
                                              f"font-size: 14px; font-weight: bold;")
            self.bottom_border.setText(text)
            self.bottom_border.setAlignment(Qt.AlignmentFlag.AlignCenter)
            y = self.view.height() - height
            self.bottom_border.setGeometry(0, y, self.view.width(), height)
            self.bottom_border.show()
        else:
            self.bottom_border.hide()

    def get_all_overlays(self) -> dict:
        """Get all overlay data for rendering."""
        from app.overlays import DraggableTextItem, DraggableBlurRegion
        texts = []
        blurs = []
        for item in self.scene.items():
            if isinstance(item, DraggableTextItem):
                texts.append(item.get_data())
            elif isinstance(item, DraggableBlurRegion):
                blurs.append(item.get_region_data())
        return {
            'texts': texts,
            'blurs': blurs,
            'logo_path': '',
            'preview_width': max(self.view.width(), 1),
            'preview_height': max(self.view.height(), 1),
        }

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_video_to_view()
        # Reposition borders
        if self.top_border.isVisible():
            self.top_border.setGeometry(0, 0, self.view.width(), self.top_border.height())
        if self.bottom_border.isVisible():
            y = self.view.height() - self.bottom_border.height()
            self.bottom_border.setGeometry(0, y, self.view.width(), self.bottom_border.height())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key.Key_Left:
            self.player.setPosition(max(0, self.player.position() - 5000))
        elif event.key() == Qt.Key.Key_Right:
            self.player.setPosition(self.player.position() + 5000)
        else:
            super().keyPressEvent(event)
