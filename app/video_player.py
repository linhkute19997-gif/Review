"""
Video Player Section
====================
QMediaPlayer-based video preview with overlay support.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QGraphicsView, QGraphicsScene, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSizeF, QUrl, QTimer
from PyQt6.QtGui import QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

from app.utils.srt_parser import format_time_display, parse_srt_time_to_ms


class VideoPlayerSection(QWidget):
    """Video player with overlay support for preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._overlays = []
        self._video_files = []
        self._current_page = 0
        # P3-3: live subtitle preview state. ``_subtitle_entries`` is
        # the master list (each entry has ``start_time``, ``end_time``
        # and ``translated_text`` / ``text``); ``_subtitle_show_text``
        # toggles between original and translated text on the overlay.
        self._subtitle_entries: list = []
        self._subtitle_show_text = True

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
        # P3-3: keep the live subtitle text overlay in sync with the
        # player's current position. Cheap O(n) scan — SRT lists are
        # rarely above a few thousand entries; a binary search is
        # not worth the extra complexity here.
        self._refresh_live_subtitle(position)

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
        self._subtitle_show_text = bool(visible)
        if visible:
            self.subtitle_label.show()
            self._refresh_live_subtitle(self.player.position())
        else:
            self.subtitle_label.hide()

    # ── P3-3 live subtitle text preview ─────────────────────────
    def set_subtitle_entries(self, entries: list) -> None:
        """Feed the editor's subtitle list into the player overlay.

        Accepts entries in either format:

        * the SRT-parser shape: ``{'start': 'HH:MM:SS,mmm',
          'end': 'HH:MM:SS,mmm', ...}`` (default for ``parse_srt``).
        * the legacy millisecond shape: ``{'start_time': int,
          'end_time': int, ...}``.

        We normalise every entry to carry ``start_time`` / ``end_time``
        in milliseconds so :meth:`_refresh_live_subtitle` does not need
        to know which producer fed the list. ``translated_text`` is
        preferred for display, falling back to ``text``.
        """
        normalised: list = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            start_ms = entry.get('start_time')
            end_ms = entry.get('end_time')
            if start_ms is None and 'start' in entry:
                try:
                    start_ms = parse_srt_time_to_ms(str(entry['start']))
                except (ValueError, TypeError, AttributeError):
                    start_ms = 0
            if end_ms is None and 'end' in entry:
                try:
                    end_ms = parse_srt_time_to_ms(str(entry['end']))
                except (ValueError, TypeError, AttributeError):
                    end_ms = 0
            # Keep the original entry intact (other consumers still
            # need ``start`` / ``end`` strings) but augment with the
            # cached ms values for the live overlay.
            normalised.append({
                **entry,
                'start_time': int(start_ms or 0),
                'end_time': int(end_ms or 0),
            })
        self._subtitle_entries = normalised
        self._refresh_live_subtitle(self.player.position())

    def _refresh_live_subtitle(self, position_ms: int) -> None:
        """Pick the entry that contains ``position_ms`` and paint it."""
        if not self._subtitle_show_text:
            return
        if not self._subtitle_entries:
            self.subtitle_label.setText('')
            return
        active = None
        for entry in self._subtitle_entries:
            start = entry.get('start_time') or 0
            end = entry.get('end_time') or 0
            if start <= position_ms <= end:
                active = entry
                break
        if active is None:
            self.subtitle_label.setText('')
            self.subtitle_label.adjustSize()
            return
        text = active.get('translated_text') or active.get('text') or ''
        self.subtitle_label.setText(text)
        self.subtitle_label.adjustSize()
        self._reposition_subtitle_label()

    def _reposition_subtitle_label(self) -> None:
        """Anchor the subtitle label centred near the bottom of the view."""
        view_w = self.view.width()
        view_h = self.view.height()
        label_w = min(self.subtitle_label.width(), max(view_w - 40, 120))
        label_h = self.subtitle_label.height()
        x = max(0, (view_w - label_w) // 2)
        y = max(0, view_h - label_h - 24)
        self.subtitle_label.setGeometry(x, y, label_w, label_h)

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
        """Get all overlay data for rendering.

        ``preview_width`` / ``preview_height`` MUST report the video
        item's size, not the view's size. Overlay coordinates returned
        by ``get_data()`` / ``get_region_data()`` are scene-space, and
        :meth:`_fit_video_to_view` sets ``scene.sceneRect()`` equal to
        ``video_item.boundingRect()`` — so the scene is bounded by the
        scaled video preview, not by the surrounding view.

        Earlier this returned ``self.view.width()`` /
        ``self.view.height()`` which is *larger* than the video item
        whenever the view has aspect-ratio letterboxing on the sides
        or top/bottom. ``video_creator`` then scaled overlays by
        ``video_w / view_w`` instead of ``video_w / item_w``, which
        squeezed text/blur regions toward the top-left corner of the
        rendered video instead of leaving them where the user placed
        them.
        """
        from app.overlays import DraggableTextItem, DraggableBlurRegion
        texts = []
        blurs = []
        for item in self.scene.items():
            if isinstance(item, DraggableTextItem):
                texts.append(item.get_data())
            elif isinstance(item, DraggableBlurRegion):
                blurs.append(item.get_region_data())
        item_size = self.video_item.size()
        return {
            'texts': texts,
            'blurs': blurs,
            'logo_path': '',
            'preview_width': max(int(item_size.width()), 1),
            'preview_height': max(int(item_size.height()), 1),
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
        if self.subtitle_label.isVisible():
            self._reposition_subtitle_label()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key.Key_Left:
            self.player.setPosition(max(0, self.player.position() - 5000))
        elif event.key() == Qt.Key.Key_Right:
            self.player.setPosition(self.player.position() + 5000)
        else:
            super().keyPressEvent(event)
