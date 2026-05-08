"""
Config Section — 8-tab configuration panel
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QTabWidget, QSlider, QRadioButton, QButtonGroup,
    QCheckBox, QSpinBox, QFileDialog, QColorDialog,
)
from PyQt6.QtCore import Qt, QTimer

from app.utils.config import (
    LANGUAGES, TRANSLATION_MODELS, VOICE_CONFIGS_EDGE_VI,
    load_styles_config, load_user_preferences, save_user_preferences
)
from app.utils.logger import get_logger
from app.utils.theme import THEMES, apply_theme, save_theme_preference

logger = get_logger('config_section')


def make_label(text, style=""):
    lbl = QLabel(text)
    if style:
        lbl.setProperty("class", style)
    return lbl


class ConfigSection(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_player = None
        # Single-shot debounce timer (P2-7) — every signal connected
        # via ``_wire_live_preview`` restarts the timer; only the
        # last keystroke / spin-tick within 150 ms repaints the
        # video player. Keeps the UI smooth at 60 fps even while
        # the user is dragging a slider.
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(150)
        self._preview_debounce.timeout.connect(self._apply_live_preview)
        self._build_ui()
        self._load_user_preferences()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self._build_tab_dich_phude()
        self._build_tab_longtieng()
        self._build_tab_phudevb()
        self._build_tab_amthanh()
        self._build_tab_khung_logo()
        self._build_tab_lach_banquyen()
        self._build_tab_cai_dat()
        self._build_tab_tach_phude()
        layout.addWidget(self.tabs)
        # P3-9: tooltips for every config widget so first-run users
        # don't have to guess what each toggle does. Done after the
        # tab tree exists so the helper can resolve every widget.
        self._install_tooltips()

    # ── Tab 1: Dịch Phụ Đề ──
    def _build_tab_dich_phude(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        # Source language
        row = QHBoxLayout()
        row.addWidget(QLabel("Dịch phụ đề từ:"))
        self.combo_src_lang = QComboBox()
        for name, code in LANGUAGES:
            self.combo_src_lang.addItem(name, code)
        self.combo_src_lang.setCurrentIndex(0)
        row.addWidget(self.combo_src_lang)
        row.addWidget(QLabel("→"))
        self.combo_tgt_lang = QComboBox()
        for name, code in LANGUAGES:
            self.combo_tgt_lang.addItem(name, code)
        self.combo_tgt_lang.setCurrentIndex(1)
        row.addWidget(self.combo_tgt_lang)
        layout.addLayout(row)
        # Model
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Mô Hình Dịch:"))
        self.combo_model = QComboBox()
        for m in TRANSLATION_MODELS:
            self.combo_model.addItem(m['name'])
        row2.addWidget(self.combo_model)
        self.btn_api_config = QPushButton("⚙️ Cấu Hình")
        self.btn_api_config.setObjectName("btnCauHinh")
        row2.addWidget(self.btn_api_config)
        layout.addLayout(row2)
        # Style
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Phong Cách Dịch:"))
        self.combo_style = QComboBox()
        self._load_styles()
        row3.addWidget(self.combo_style)
        self.btn_style_mgr = QPushButton("📝")
        self.btn_style_mgr.setFixedWidth(36)
        row3.addWidget(self.btn_style_mgr)
        layout.addLayout(row3)
        # Status
        self.translate_status = QLabel("Sẵn Sàng Dịch")
        self.translate_status.setProperty("class", "labelGreen")
        layout.addWidget(self.translate_status)
        # Translate button
        self.btn_translate = QPushButton("▶ Tiến Hành Dịch Phụ Đề")
        self.btn_translate.setObjectName("btnTienHanh")
        layout.addWidget(self.btn_translate)
        # Progress
        self.translate_progress = QLabel("Tiến trình:")
        self.translate_progress.setProperty("class", "labelDim")
        layout.addWidget(self.translate_progress)
        layout.addStretch()
        self.tabs.addTab(content, "Dịch Phụ Đề")

    # ── Tab 2: Lồng Tiếng ──
    def _build_tab_longtieng(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.addWidget(QLabel("ℹ️ Phụ đề để lồng tiếng sẽ lấy từ tab chỉnh sửa phụ đề."))
        # Provider
        row = QHBoxLayout()
        row.addWidget(QLabel("Nhà Cung Cấp:"))
        self.combo_voice_provider = QComboBox()
        self.combo_voice_provider.addItems(["Edge TTS (miễn phí)", "Google TTS", "ElevenLabs"])
        self.combo_voice_provider.currentIndexChanged.connect(self._on_voice_provider_changed)
        row.addWidget(self.combo_voice_provider)
        layout.addLayout(row)
        # Voice type
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Giọng Đọc:"))
        self.combo_voice_type = QComboBox()
        for vc in VOICE_CONFIGS_EDGE_VI:
            self.combo_voice_type.addItem(vc['label'])
        row2.addWidget(self.combo_voice_type)
        layout.addLayout(row2)
        # Speed
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Tốc Độ Đọc:"))
        self.voice_speed = QSpinBox()
        self.voice_speed.setRange(50, 200)
        self.voice_speed.setValue(100)
        self.voice_speed.setSuffix("%")
        row3.addWidget(self.voice_speed)
        layout.addLayout(row3)
        # Preview
        self.btn_preview_voice = QPushButton("🔊 Nghe Thử")
        layout.addWidget(self.btn_preview_voice)
        # SRT browse for standalone voice
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Hoặc chọn file SRT:"))
        self.voice_srt_input = QLineEdit()
        self.voice_srt_input.setPlaceholderText("Chọn file .srt để lồng tiếng...")
        row4.addWidget(self.voice_srt_input)
        btn_browse = QPushButton("📂")
        btn_browse.setFixedWidth(36)
        btn_browse.clicked.connect(self._browse_srt_for_voice)
        row4.addWidget(btn_browse)
        layout.addLayout(row4)
        self.btn_voice_only = QPushButton("▶ Tạo File Lồng Tiếng")
        self.btn_voice_only.setObjectName("btnTienHanh")
        layout.addWidget(self.btn_voice_only)
        layout.addStretch()
        self.tabs.addTab(content, "Lồng Tiếng")

    # ── Tab 3: Phụ Đề Văn Bản ──
    def _build_tab_phudevb(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        self.chk_subtitle_enabled = QCheckBox("Thêm Phụ Đề Văn Bản:")
        self.chk_subtitle_enabled.setChecked(True)
        layout.addWidget(self.chk_subtitle_enabled)
        # Preview
        self.subtitle_preview = QLabel("Đây là nơi chứa phụ đề")
        self.subtitle_preview.setStyleSheet("background:#1a1a1a;color:white;padding:8px;border-radius:4px;font-size:13px;")
        self.subtitle_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.subtitle_preview)
        # Font size
        row = QHBoxLayout()
        row.addWidget(QLabel("Cỡ Chữ:"))
        self.subtitle_size = QSpinBox()
        self.subtitle_size.setRange(8, 72)
        self.subtitle_size.setValue(20)
        row.addWidget(self.subtitle_size)
        row.addWidget(QLabel("Màu:"))
        self.btn_subtitle_color = QPushButton()
        self.btn_subtitle_color.setFixedSize(30, 24)
        self.btn_subtitle_color.setStyleSheet("background:#ffffff;border:1px solid #666;")
        self.btn_subtitle_color.clicked.connect(lambda: self._pick_color(self.btn_subtitle_color))
        row.addWidget(self.btn_subtitle_color)
        layout.addLayout(row)
        # BG
        row2 = QHBoxLayout()
        self.chk_subtitle_bg = QCheckBox("Nền Phụ Đề")
        row2.addWidget(self.chk_subtitle_bg)
        self.btn_subtitle_bg_color = QPushButton()
        self.btn_subtitle_bg_color.setFixedSize(30, 24)
        self.btn_subtitle_bg_color.setStyleSheet("background:#000000;border:1px solid #666;")
        self.btn_subtitle_bg_color.clicked.connect(lambda: self._pick_color(self.btn_subtitle_bg_color))
        row2.addWidget(self.btn_subtitle_bg_color)
        row2.addWidget(QLabel("Opacity:"))
        self.subtitle_bg_opacity = QSlider(Qt.Orientation.Horizontal)
        self.subtitle_bg_opacity.setRange(0, 100)
        self.subtitle_bg_opacity.setValue(80)
        row2.addWidget(self.subtitle_bg_opacity)
        layout.addLayout(row2)
        # Y position
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Vị trí Y:"))
        self.subtitle_y = QSlider(Qt.Orientation.Horizontal)
        self.subtitle_y.setRange(0, 100)
        self.subtitle_y.setValue(90)
        row3.addWidget(self.subtitle_y)
        layout.addLayout(row3)
        # Opacity
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Độ trong suốt:"))
        self.subtitle_opacity = QSlider(Qt.Orientation.Horizontal)
        self.subtitle_opacity.setRange(10, 200)
        self.subtitle_opacity.setValue(100)
        row4.addWidget(self.subtitle_opacity)
        layout.addLayout(row4)
        layout.addStretch()
        self.tabs.addTab(content, "Phụ Đề VB")

    # ── Tab 4: Âm Thanh ──
    def _build_tab_amthanh(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Âm Thanh Gốc:"))
        self.audio_group = QButtonGroup(self)
        self.rb_audio_mute = QRadioButton("Tắt Âm Thanh Gốc")
        self.rb_audio_keep = QRadioButton("Giữ Lại Toàn Bộ Âm Thanh Gốc")
        self.rb_audio_keep.setChecked(True)
        self.rb_audio_cond = QRadioButton("Giữ Âm Thanh Gốc Khi Không Có Lồng Tiếng")
        self.audio_group.addButton(self.rb_audio_mute, 0)
        self.audio_group.addButton(self.rb_audio_keep, 1)
        self.audio_group.addButton(self.rb_audio_cond, 2)
        layout.addWidget(self.rb_audio_mute)
        layout.addWidget(self.rb_audio_keep)
        layout.addWidget(self.rb_audio_cond)
        # Volume
        row = QHBoxLayout()
        row.addWidget(QLabel("Âm Lượng Âm Thanh Gốc:"))
        self.orig_volume = QSlider(Qt.Orientation.Horizontal)
        self.orig_volume.setRange(0, 200)
        self.orig_volume.setValue(100)
        row.addWidget(self.orig_volume)
        self.orig_vol_label = QLabel("100%")
        row.addWidget(self.orig_vol_label)
        self.orig_volume.valueChanged.connect(lambda v: self.orig_vol_label.setText(f"{v}%"))
        layout.addLayout(row)
        # BG Music
        layout.addWidget(QLabel("Nhạc Nền:"))
        self.chk_bg_music = QCheckBox("Bật Nhạc Nền")
        layout.addWidget(self.chk_bg_music)
        row2 = QHBoxLayout()
        self.bg_music_path = QLineEdit()
        self.bg_music_path.setPlaceholderText("Chọn file nhạc nền...")
        row2.addWidget(self.bg_music_path)
        btn = QPushButton("📂")
        btn.setFixedWidth(36)
        btn.clicked.connect(self._select_music_file)
        row2.addWidget(btn)
        layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Âm Lượng Nhạc Nền:"))
        self.bg_music_volume = QSlider(Qt.Orientation.Horizontal)
        self.bg_music_volume.setRange(0, 100)
        self.bg_music_volume.setValue(30)
        row3.addWidget(self.bg_music_volume)
        layout.addLayout(row3)
        # Voice file
        self.chk_voice_file = QCheckBox("Thêm File Voice:")
        layout.addWidget(self.chk_voice_file)
        row4 = QHBoxLayout()
        self.voice_file_path = QLineEdit()
        self.voice_file_path.setPlaceholderText("Chọn file voice...")
        row4.addWidget(self.voice_file_path)
        btn2 = QPushButton("📂")
        btn2.setFixedWidth(36)
        btn2.clicked.connect(self._select_voice_file)
        row4.addWidget(btn2)
        layout.addLayout(row4)
        layout.addStretch()
        self.tabs.addTab(content, "Âm Thanh")

    # ── Tab 5: Khung & Logo ──
    def _build_tab_khung_logo(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        # Top border
        self.chk_top_border = QCheckBox("Thêm Viền Trên:")
        layout.addWidget(self.chk_top_border)
        row = QHBoxLayout()
        row.addWidget(QLabel("Màu:"))
        self.btn_top_color = QPushButton()
        self.btn_top_color.setFixedSize(30, 24)
        self.btn_top_color.setStyleSheet("background:#ffff00;border:1px solid #3a3a5e;")
        self.btn_top_color.clicked.connect(lambda: self._pick_color(self.btn_top_color))
        row.addWidget(self.btn_top_color)
        row.addWidget(QLabel("Chiều Cao:"))
        self.top_height = QSpinBox()
        self.top_height.setRange(10, 200)
        self.top_height.setValue(40)
        row.addWidget(self.top_height)
        layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Tiêu đề:"))
        self.top_text = QLineEdit()
        self.top_text.setPlaceholderText("Nhập tiêu đề...")
        row2.addWidget(self.top_text)
        row2.addWidget(QLabel("Màu chữ:"))
        self.btn_top_text_color = QPushButton()
        self.btn_top_text_color.setFixedSize(30, 24)
        self.btn_top_text_color.setStyleSheet("background:#000000;border:1px solid #3a3a5e;")
        self.btn_top_text_color.clicked.connect(lambda: self._pick_color(self.btn_top_text_color))
        row2.addWidget(self.btn_top_text_color)
        layout.addLayout(row2)
        # Bottom border
        self.chk_bot_border = QCheckBox("Thêm Viền Dưới:")
        layout.addWidget(self.chk_bot_border)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Màu:"))
        self.btn_bot_color = QPushButton()
        self.btn_bot_color.setFixedSize(30, 24)
        self.btn_bot_color.setStyleSheet("background:#000000;border:1px solid #3a3a5e;")
        self.btn_bot_color.clicked.connect(lambda: self._pick_color(self.btn_bot_color))
        row3.addWidget(self.btn_bot_color)
        row3.addWidget(QLabel("Chiều Cao:"))
        self.bot_height = QSpinBox()
        self.bot_height.setRange(10, 200)
        self.bot_height.setValue(40)
        row3.addWidget(self.bot_height)
        layout.addLayout(row3)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Chú thích:"))
        self.bot_text = QLineEdit()
        self.bot_text.setPlaceholderText("Nhập chú thích...")
        row4.addWidget(self.bot_text)
        row4.addWidget(QLabel("Màu chữ:"))
        self.btn_bot_text_color = QPushButton()
        self.btn_bot_text_color.setFixedSize(30, 24)
        self.btn_bot_text_color.setStyleSheet("background:#ffffff;border:1px solid #3a3a5e;")
        self.btn_bot_text_color.clicked.connect(lambda: self._pick_color(self.btn_bot_text_color))
        row4.addWidget(self.btn_bot_text_color)
        layout.addLayout(row4)
        # Logo
        layout.addWidget(QLabel("Logo:"))
        row5 = QHBoxLayout()
        self.logo_path = QLineEdit()
        self.logo_path.setPlaceholderText("Chọn file logo...")
        row5.addWidget(self.logo_path)
        btn_logo = QPushButton("📂")
        btn_logo.setFixedWidth(36)
        btn_logo.clicked.connect(self._select_logo_file)
        row5.addWidget(btn_logo)
        btn_del = QPushButton("🗑")
        btn_del.setFixedWidth(36)
        btn_del.clicked.connect(lambda: self.logo_path.clear())
        row5.addWidget(btn_del)
        layout.addLayout(row5)
        layout.addStretch()
        self.tabs.addTab(content, "Khung & Logo")

    # ── Tab 6: Lách Bản Quyền ──
    def _build_tab_lach_banquyen(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        self.chk_zoom = QCheckBox("Phóng To Video")
        layout.addWidget(self.chk_zoom)
        self.chk_flip = QCheckBox("Lật Ngang Video")
        layout.addWidget(self.chk_flip)
        # Dynamic zoom
        self.chk_dynamic_zoom = QCheckBox("Thu Phóng Video Theo Thời Gian:")
        layout.addWidget(self.chk_dynamic_zoom)
        row = QHBoxLayout()
        row.addWidget(QLabel("Zoom (%):"))
        self.zoom_value = QSpinBox()
        self.zoom_value.setRange(1, 50)
        self.zoom_value.setValue(5)
        row.addWidget(self.zoom_value)
        row.addWidget(QLabel("Chu kỳ (s):"))
        self.zoom_interval = QSpinBox()
        self.zoom_interval.setRange(1, 60)
        self.zoom_interval.setValue(10)
        row.addWidget(self.zoom_interval)
        layout.addLayout(row)
        # Scan lines
        layout.addWidget(QLabel("Đường Quét:"))
        self.line_group = QButtonGroup(self)
        rb1 = QRadioButton("Không")
        rb1.setChecked(True)
        rb2 = QRadioButton("Ngang")
        rb3 = QRadioButton("Dọc")
        rb4 = QRadioButton("Ngẫu nhiên")
        self.line_group.addButton(rb1, 0)
        self.line_group.addButton(rb2, 1)
        self.line_group.addButton(rb3, 2)
        self.line_group.addButton(rb4, 3)
        for rb in [rb1, rb2, rb3, rb4]:
            layout.addWidget(rb)
        layout.addStretch()
        self.tabs.addTab(content, "Lách BQ")

    # ── Tab 7: Cài Đặt ──
    def _build_tab_cai_dat(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        # P3-15: theme switcher (Dark / Light / System) lives at the
        # top of the settings tab so users don't have to dig through
        # menus to flip palettes.
        layout.addWidget(QLabel("🎨 Giao Diện"))
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self.combo_theme = QComboBox()
        self.combo_theme.addItems([
            'Dả tối (Dark)',
            'Sáng (Light)',
            'Theo hệ điều hành',
        ])
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.combo_theme)
        theme_row.addStretch()
        layout.addLayout(theme_row)
        layout.addWidget(QLabel("📺 Độ Phân Giải Video Xuất"))
        self.chk_1080p = QCheckBox("  1080P (1920×1080 - Chuẩn HD)")
        self.chk_4k = QCheckBox("  4K HD Ultra (3840×2160 - Siêu Nét)")
        self.chk_1080p.stateChanged.connect(lambda s: (
            self.chk_4k.blockSignals(True),
            self.chk_4k.setChecked(False),
            self.chk_4k.blockSignals(False),
        ) if s else None)
        self.chk_4k.stateChanged.connect(lambda s: (
            self.chk_1080p.blockSignals(True),
            self.chk_1080p.setChecked(False),
            self.chk_1080p.blockSignals(False),
        ) if s else None)
        layout.addWidget(self.chk_1080p)
        layout.addWidget(self.chk_4k)
        # GPU
        layout.addWidget(QLabel("GPU:"))
        self.combo_gpu = QComboBox()
        self.combo_gpu.addItems(['auto', 'nvidia', 'amd', 'intel', 'cpu'])
        layout.addWidget(self.combo_gpu)
        layout.addWidget(QLabel("Tự Động Tắt Máy:"))
        self.chk_shutdown = QCheckBox("Tắt máy sau khi render xong")
        layout.addWidget(self.chk_shutdown)
        layout.addStretch()
        self.tabs.addTab(content, "Cài Đặt")

    # ── Tab 8: Tách Phụ Đề ──
    def _build_tab_tach_phude(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.addWidget(QLabel("✂ Tách phụ đề tự động bằng AI hoặc OCR"))
        layout.addWidget(QLabel("• Whisper AI: Nhận dạng giọng nói → phụ đề"))
        layout.addWidget(QLabel("• PaddleOCR: Quét chữ trên video → phụ đề"))
        self.btn_open_extract = QPushButton("✂ Mở Công Cụ Tách Phụ Đề")
        self.btn_open_extract.setObjectName("btnTienHanh")
        layout.addWidget(self.btn_open_extract)
        layout.addStretch()
        self.tabs.addTab(content, "Tách PĐ")

    # ── Helpers ──
    def _pick_color(self, button):
        color = QColorDialog.getColor()
        if color.isValid():
            button.setStyleSheet(f"background:{color.name()};border:1px solid #3a3a5e;")

    def _get_button_color(self, button):
        style = button.styleSheet()
        if 'background:' in style:
            start = style.index('background:') + 11
            end = style.index(';', start)
            return style[start:end].strip()
        return '#ffff00'

    def _select_music_file(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn nhạc nền", "",
                    "Audio Files (*.mp3 *.wav *.aac *.m4a);;All Files (*)")
        if fp:
            self.bg_music_path.setText(fp)

    def _select_voice_file(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn tệp voice", "",
                    "Audio Files (*.mp3 *.wav *.aac *.m4a);;All Files (*)")
        if fp:
            self.voice_file_path.setText(fp)

    def _select_logo_file(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn logo", "",
                    "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)")
        if fp:
            self.logo_path.setText(fp)

    def _browse_srt_for_voice(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn SRT", "",
                    "SRT Files (*.srt);;All Files (*)")
        if fp:
            self.voice_srt_input.setText(fp)

    def _on_voice_provider_changed(self, index):
        # P3-8: persist the *previous* provider's voice choice before
        # we wipe the combo so every provider keeps its own "last used"
        # voice independently.
        self._save_active_provider_voice()
        provider = self.combo_voice_provider.currentText()
        self.combo_voice_type.clear()
        if 'Edge' in provider:
            for vc in VOICE_CONFIGS_EDGE_VI:
                self.combo_voice_type.addItem(vc['label'])
        elif 'Google' in provider:
            self.combo_voice_type.addItems(['Việt Nam (Nữ)', 'Việt Nam (Nam)'])
        elif 'ElevenLabs' in provider:
            self.combo_voice_type.addItems(['Rachel', 'Domi', 'Bella', 'Antoni', 'Elli', 'Josh'])
        # Restore the saved voice for the newly-selected provider.
        self._restore_provider_voice(provider)

    def _provider_pref_key(self, provider: str) -> str:
        """Stable key for ``user_preferences.voice_per_provider``."""
        return ''.join(c.lower() for c in provider if c.isalnum())

    def _save_active_provider_voice(self) -> None:
        """Stash the current provider's voice in user_preferences."""
        provider = self.combo_voice_provider.currentText()
        if not provider:
            return
        key = self._provider_pref_key(provider)
        prefs = load_user_preferences()
        per_provider = prefs.get('voice_per_provider', {}) or {}
        per_provider[key] = self.combo_voice_type.currentIndex()
        prefs['voice_per_provider'] = per_provider
        save_user_preferences(prefs)

    def _restore_provider_voice(self, provider: str) -> None:
        """Re-apply the per-provider preference, if any."""
        if not provider:
            return
        key = self._provider_pref_key(provider)
        prefs = load_user_preferences()
        per_provider = prefs.get('voice_per_provider', {}) or {}
        idx = per_provider.get(key)
        if isinstance(idx, int) and 0 <= idx < self.combo_voice_type.count():
            self.combo_voice_type.setCurrentIndex(idx)

    # ── P3-15 Theme switcher ────────────────────────────────────
    def _on_theme_changed(self, index: int) -> None:
        """Apply the new QSS theme and persist the choice."""
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            return
        if not (0 <= index < len(THEMES)):
            return
        theme = THEMES[index]
        save_theme_preference(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)

    def _load_styles(self):
        styles = load_styles_config()
        self.combo_style.clear()
        for s in styles:
            self.combo_style.addItem(s)

    def _load_user_preferences(self):
        prefs = load_user_preferences()
        if 'model_index' in prefs:
            self.combo_model.setCurrentIndex(prefs['model_index'])
        if 'src_lang_index' in prefs:
            self.combo_src_lang.setCurrentIndex(prefs['src_lang_index'])
        if 'tgt_lang_index' in prefs:
            self.combo_tgt_lang.setCurrentIndex(prefs['tgt_lang_index'])
        if 'voice_provider' in prefs:
            idx = prefs['voice_provider']
            self.combo_voice_provider.setCurrentIndex(idx)
            # Re-populate combo_voice_type to match the restored provider —
            # currentIndexChanged may not fire if the index was already idx.
            self._on_voice_provider_changed(idx)
        if 'gpu_device' in prefs:
            idx = self.combo_gpu.findText(prefs['gpu_device'])
            if idx >= 0:
                self.combo_gpu.setCurrentIndex(idx)
        if 'voice_speed' in prefs:
            self.voice_speed.setValue(prefs['voice_speed'])
        if 'subtitle_size' in prefs:
            self.subtitle_size.setValue(prefs['subtitle_size'])
        if 'subtitle_y' in prefs:
            self.subtitle_y.setValue(prefs['subtitle_y'])
        if 'audio_mode' in prefs:
            btn = self.audio_group.button(prefs['audio_mode'])
            if btn:
                btn.setChecked(True)
        if 'zoom_enabled' in prefs:
            self.chk_zoom.setChecked(prefs['zoom_enabled'])
        if 'flip_horizontal' in prefs:
            self.chk_flip.setChecked(prefs['flip_horizontal'])
        if 'orig_volume' in prefs:
            self.orig_volume.setValue(prefs['orig_volume'])
        # P3-15: restore theme dropdown to the saved value (no QSS
        # reapplication — main.py already did that on boot).
        theme = prefs.get('theme', 'dark')
        try:
            theme_idx = THEMES.index(theme)
        except ValueError:
            theme_idx = 0
        self.combo_theme.blockSignals(True)
        self.combo_theme.setCurrentIndex(theme_idx)
        self.combo_theme.blockSignals(False)

    def _save_user_preferences(self):
        # P3-8: capture the current provider's voice in the per-provider
        # map before we serialise so the next launch picks it up.
        self._save_active_provider_voice()
        prefs = load_user_preferences()
        per_provider = prefs.get('voice_per_provider', {}) or {}
        save_user_preferences({
            'model_index': self.combo_model.currentIndex(),
            'src_lang_index': self.combo_src_lang.currentIndex(),
            'tgt_lang_index': self.combo_tgt_lang.currentIndex(),
            'voice_provider': self.combo_voice_provider.currentIndex(),
            'gpu_device': self.combo_gpu.currentText(),
            'voice_speed': self.voice_speed.value(),
            'subtitle_size': self.subtitle_size.value(),
            'subtitle_y': self.subtitle_y.value(),
            'audio_mode': self.audio_group.checkedId(),
            'zoom_enabled': self.chk_zoom.isChecked(),
            'flip_horizontal': self.chk_flip.isChecked(),
            'orig_volume': self.orig_volume.value(),
            'theme': prefs.get('theme', 'dark'),
            'voice_per_provider': per_provider,
            'output_folder': prefs.get('output_folder', ''),
        })

    def set_video_player(self, vp):
        self.video_player = vp
        # Wire WYSIWYG preview now that we have a player to drive.
        # All subtitle / border / opacity widgets push their state through
        # ``_apply_live_preview`` so the user sees the same look in the
        # video panel that the FFmpeg render will produce.
        self._wire_live_preview()
        self._apply_live_preview()

    def _wire_live_preview(self):
        """Connect every preview-affecting widget to the debounced trigger."""
        widgets_state = [
            self.chk_subtitle_enabled.toggled,
            self.subtitle_opacity.valueChanged,
            self.chk_top_border.toggled,
            self.top_height.valueChanged,
            self.top_text.textChanged,
            self.chk_bot_border.toggled,
            self.bot_height.valueChanged,
            self.bot_text.textChanged,
        ]
        for sig in widgets_state:
            sig.connect(lambda *_: self._schedule_preview())

    def _schedule_preview(self):
        """Restart the 150 ms preview debounce timer."""
        self._preview_debounce.start()

    def _apply_live_preview(self):
        """Push current subtitle/border config into the attached video player."""
        if not self.video_player:
            return
        try:
            self.video_player.show_subtitle_bar(
                self.chk_subtitle_enabled.isChecked())
            self.video_player.update_subtitle_opacity(
                self.subtitle_opacity.value())
            self.video_player.set_top_border(
                self.chk_top_border.isChecked(),
                color=self._get_button_color(self.btn_top_color),
                height=self.top_height.value(),
                text=self.top_text.text(),
                text_color=self._get_button_color(self.btn_top_text_color))
            self.video_player.set_bottom_border(
                self.chk_bot_border.isChecked(),
                color=self._get_button_color(self.btn_bot_color),
                height=self.bot_height.value(),
                text=self.bot_text.text(),
                text_color=self._get_button_color(self.btn_bot_text_color))
        except Exception as exc:  # noqa: BLE001
            logger.debug("live preview update failed: %s", exc)

    def get_config(self) -> dict:
        audio_mode = self.audio_group.checkedId()
        line_mode = self.line_group.checkedId()
        return {
            'text_subtitle_enabled': self.chk_subtitle_enabled.isChecked(),
            'text_subtitle_size': self.subtitle_size.value(),
            'text_subtitle_font': 'Arial',
            'text_subtitle_color': self._get_button_color(self.btn_subtitle_color),
            'text_subtitle_bg_enabled': self.chk_subtitle_bg.isChecked(),
            'text_subtitle_bg_color': self._get_button_color(self.btn_subtitle_bg_color),
            'text_subtitle_bg_opacity': self.subtitle_bg_opacity.value(),
            'text_subtitle_y': self.subtitle_y.value(),
            'text_subtitle_opacity': self.subtitle_opacity.value(),
            'audio_mode': audio_mode,
            'orig_volume': self.orig_volume.value(),
            'bg_music_enabled': self.chk_bg_music.isChecked(),
            'bg_music_path': self.bg_music_path.text(),
            'bg_music_volume': self.bg_music_volume.value(),
            'voice_file_enabled': self.chk_voice_file.isChecked(),
            'voice_file_path': self.voice_file_path.text(),
            'voice_file_volume': 100,
            'top_border_enabled': self.chk_top_border.isChecked(),
            'top_border_color': self._get_button_color(self.btn_top_color),
            'top_border_text': self.top_text.text(),
            'top_text_color': self._get_button_color(self.btn_top_text_color),
            'top_border_height': self.top_height.value(),
            'bot_border_enabled': self.chk_bot_border.isChecked(),
            'bot_border_color': self._get_button_color(self.btn_bot_color),
            'bot_border_text': self.bot_text.text(),
            'bot_text_color': self._get_button_color(self.btn_bot_text_color),
            'bot_border_height': self.bot_height.value(),
            'logo_path': self.logo_path.text(),
            'zoom_enabled': self.chk_zoom.isChecked(),
            'flip_horizontal': self.chk_flip.isChecked(),
            'dynamic_zoom_enabled': self.chk_dynamic_zoom.isChecked(),
            'zoom_value': self.zoom_value.value(),
            'zoom_interval': self.zoom_interval.value(),
            'line_mode': line_mode,
            'voice_speed_input': self.voice_speed.value(),
            'resolution_1080p': self.chk_1080p.isChecked(),
            'resolution_4k': self.chk_4k.isChecked(),
            'gpu_device': self.combo_gpu.currentText(),
            'auto_shutdown': self.chk_shutdown.isChecked(),
        }

    def get_selected_model(self): return self.combo_model.currentText()
    def get_selected_style(self): return self.combo_style.currentText()
    def get_source_lang(self): return self.combo_src_lang.currentData()
    def get_target_lang(self): return self.combo_tgt_lang.currentData()

    # ── P3-9 Tooltips ──────────────────────────────────────────
    def _install_tooltips(self) -> None:
        """Set tooltips for every interactive config widget.

        Tooltips show on hover and are i18n-friendly: each entry is a
        complete Vietnamese sentence so it reads naturally for the
        primary user base.
        """
        tips = {
            self.combo_src_lang: 'Ngôn ngữ của phụ đề gốc.',
            self.combo_tgt_lang: 'Ngôn ngữ cần dịch sang.',
            self.combo_model: 'Chọn mô hình dịch (Google miễn phí / '
                              'Gemini / Baidu / ChatGPT).',
            self.btn_api_config: 'Mở hộp thoại nhập / kiểm tra API key.',
            self.combo_style: 'Phong cách dịch (vd: kinh dị, cổ trang).',
            self.btn_translate: 'Tiến hành dịch phụ đề bằng mô hình đã chọn.',
            self.combo_voice_provider:
                'Nhà cung cấp TTS — mỗi nhà cung cấp giữ giọng '
                'cuối cùng riêng.',
            self.combo_voice_type: 'Giọng đọc cho lồng tiếng.',
            self.voice_speed: 'Tốc độ đọc; 100% là mặc định.',
            self.btn_preview_voice: 'Nghe thử giọng đã chọn bằng đoạn mẫu.',
            self.btn_voice_only: 'Chỉ tạo file MP3 lồng tiếng (không render '
                                 'video).',
            self.chk_subtitle_enabled:
                'Bật phụ đề văn bản trong video xuất.',
            self.subtitle_size: 'Cỡ chữ phụ đề (px).',
            self.btn_subtitle_color: 'Màu chữ phụ đề.',
            self.chk_subtitle_bg: 'Bật nền cho chữ phụ đề.',
            self.btn_subtitle_bg_color: 'Màu nền phụ đề.',
            self.subtitle_bg_opacity: 'Độ trong suốt nền phụ đề (0–100%).',
            self.subtitle_y: 'Vị trí Y của phụ đề (90 = gần cạnh dưới).',
            self.subtitle_opacity: 'Độ trong suốt chữ (10–200%).',
            self.rb_audio_mute: 'Tắt hoàn toàn âm thanh gốc.',
            self.rb_audio_keep: 'Giữ âm thanh gốc.',
            self.rb_audio_cond:
                'Giữ âm gốc khi không có lồng tiếng (overlay TTS).',
            self.orig_volume: 'Âm lượng âm thanh gốc (0–200%).',
            self.chk_bg_music: 'Phát nhạc nền trong toàn bộ video.',
            self.bg_music_path: 'File MP3/WAV sẽ được dùng làm nhạc nền.',
            self.bg_music_volume: 'Âm lượng nhạc nền (0–100%).',
            self.chk_voice_file: 'Dùng file voice có sẵn thay vì TTS.',
            self.voice_file_path: 'File audio lồng tiếng bên ngoài.',
            self.chk_top_border: 'Thêm thanh viền trên (header).',
            self.btn_top_color: 'Màu nền thanh viền trên.',
            self.top_height: 'Chiều cao thanh viền trên (px).',
            self.top_text: 'Tiêu đề hiển thị trên thanh viền trên.',
            self.btn_top_text_color: 'Màu chữ thanh viền trên.',
            self.chk_bot_border: 'Thêm thanh viền dưới (footer).',
            self.btn_bot_color: 'Màu nền thanh viền dưới.',
            self.bot_height: 'Chiều cao thanh viền dưới (px).',
            self.bot_text: 'Chú thích hiển thị trên thanh viền dưới.',
            self.btn_bot_text_color: 'Màu chữ thanh viền dưới.',
            self.logo_path: 'Ảnh logo se đông trên video xuất.',
            self.chk_zoom: 'Phóng to nhẹ video để tránh đội biên.',
            self.chk_flip: 'Lật ngang video (mẹo lách phát hiện bản quyền).',
            self.chk_dynamic_zoom:
                'Tự động phong/tiềm theo chu kỳ để video sinh động.',
            self.zoom_value: 'Biên độ zoom (% so với bình thường).',
            self.zoom_interval: 'Chu kỳ đổi zoom (giây).',
            self.chk_1080p: 'Xuất độ phân giải 1920×1080.',
            self.chk_4k: 'Xuất độ phân giải 3840×2160 (nặng hơn).',
            self.combo_gpu:
                'Chọn GPU encoder. \'auto\' để app tự dò.',
            self.chk_shutdown: 'Tự tắt máy sau khi render xong (hỏi trước).',
            self.combo_theme: 'Theme giao diện: Dark / Light / theo hệ điều hành.',
            self.btn_open_extract:
                'Mở công cụ tách phụ đề (Whisper AI hoặc PaddleOCR).',
        }
        for widget, text in tips.items():
            try:
                widget.setToolTip(text)
            except Exception as exc:  # noqa: BLE001
                logger.debug('tooltip set failed for %s: %s', widget, exc)
