"""
Subtitle Extract Page — Whisper (audio) + PaddleOCR (visual)
"""
import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QFileDialog, QTabWidget, QProgressBar,
    QMessageBox, QDialog, QDoubleSpinBox
)
from PyQt6.QtCore import QThread, pyqtSignal

from app.utils.config import WHISPER_MODEL_OPTIONS, BASE_DIR
from app.utils.logger import get_logger

logger = get_logger('subtitle_extract')

# Module-level caches — Whisper and PaddleOCR each take ~30–60s to
# load from disk; without these every extract reloaded them.
_WHISPER_CACHE = {}
_OCR_CACHE = {}


def _get_whisper_model(model_name, device):
    key = (model_name, device)
    if key not in _WHISPER_CACHE:
        import whisper  # local import — may need install on first run
        _WHISPER_CACHE[key] = whisper.load_model(model_name, device=device)
    return _WHISPER_CACHE[key]


def _get_paddle_ocr(lang):
    if lang not in _OCR_CACHE:
        from paddleocr import PaddleOCR
        _OCR_CACHE[lang] = PaddleOCR(use_angle_cls=True, lang=lang)
    return _OCR_CACHE[lang]

# PaddleOCR language codes — see
# https://www.paddlepaddle.org.cn/paddle/paddleocr for the full set.
OCR_LANGUAGE_OPTIONS = [
    ('en', 'Tiếng Anh (en)'),
    ('ch', 'Tiếng Trung giản thể (ch)'),
    ('chinese_cht', 'Tiếng Trung phổn thể (chinese_cht)'),
    ('vi', 'Tiếng Việt (vi)'),
    ('japan', 'Tiếng Nhật (japan)'),
    ('korean', 'Tiếng Hàn (korean)'),
    ('th', 'Tiếng Thái (th)'),
    ('fr', 'Tiếng Pháp (fr)'),
    ('german', 'Tiếng Đức (german)'),
    ('es', 'Tiếng Tây Ban Nha (es)'),
    ('ru', 'Tiếng Nga (ru)'),
    ('it', 'Tiếng Ý (it)'),
    ('pt', 'Tiếng Bồ Đào Nha (pt)'),
    ('ar', 'Tiếng Ả Rập (ar)'),
]


class WhisperModelDownloadThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, model_name):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            self.progress.emit(f"Đang tải model {self.model_name}...")
            import whisper
            whisper.load_model(self.model_name)
            self.progress.emit(f"✅ Model {self.model_name} đã sẵn sàng!")
            self.finished.emit(True)
        except ImportError:
            self.progress.emit("Đang cài đặt Whisper...")
            import subprocess
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'openai-whisper'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                import whisper
                whisper.load_model(self.model_name)
                self.progress.emit(f"✅ Đã cài đặt và tải model {self.model_name}!")
                self.finished.emit(True)
            except Exception as e:
                self.progress.emit(f"❌ Lỗi: {e}")
                self.finished.emit(False)
        except Exception as e:
            self.progress.emit(f"❌ Lỗi: {e}")
            self.finished.emit(False)


class SubtitleExtractThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, video_files, use_audio=True, lang_code='vi',
                 whisper_model_name='base', ocr_lang='ch',
                 ocr_y_top=0.7, ocr_y_bot=1.0):
        super().__init__()
        self.video_files = video_files
        self.use_audio = use_audio
        self.lang_code = lang_code
        self.whisper_model_name = whisper_model_name
        self.ocr_lang = ocr_lang
        # Vertical band of the frame to scan (0.0 = top, 1.0 = bottom).
        # Default scans the bottom 30% where subtitles usually appear.
        self.ocr_y_top = max(0.0, min(1.0, float(ocr_y_top)))
        self.ocr_y_bot = max(self.ocr_y_top + 0.05,
                             min(1.0, float(ocr_y_bot)))
        self.device = self._detect_device()

    def _detect_device(self):
        try:
            import torch
            if torch.cuda.is_available():
                name = torch.cuda.get_device_name(0)
                logger.info("GPU detected: %s", name)
                return 'cuda'
        except (ImportError, Exception):
            pass
        logger.info("Using CPU for inference")
        return 'cpu'

    def run(self):
        results = []
        total = len(self.video_files)
        for i, video_path in enumerate(self.video_files):
            self.progress.emit(f"Đang xử lý: {i+1}/{total}")
            try:
                if self.use_audio:
                    srt_text = self._extract_from_audio(video_path)
                else:
                    srt_text = self._extract_from_ocr(video_path)
                results.append((video_path, srt_text))
            except Exception as e:
                self.error.emit(f"Lỗi {video_path}: {e}")
        self.progress.emit("Hoàn thành!")
        self.finished.emit(results)

    def _extract_from_audio(self, video_path):
        """Extract subtitles from audio using Whisper."""
        self.progress.emit(f"Tải model {self.whisper_model_name}...")
        try:
            model = _get_whisper_model(self.whisper_model_name, self.device)
        except ImportError:
            import subprocess
            self.progress.emit("Đang cài đặt Whisper...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'openai-whisper'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            model = _get_whisper_model(self.whisper_model_name, self.device)

        self.progress.emit("Đang nhận diện giọng nói...")
        result = model.transcribe(str(video_path), language=self.lang_code)

        # Convert to SRT format
        srt_lines = []
        for i, seg in enumerate(result.get('segments', []), 1):
            start = self._format_time(seg['start'])
            end = self._format_time(seg['end'])
            text = seg['text'].strip()
            srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")

        return '\n'.join(srt_lines)

    def _extract_from_ocr(self, video_path):
        """Extract subtitles from video using PaddleOCR.

        Only the configured vertical band (default = bottom 30%) is
        scanned, which both speeds up OCR and reduces false positives
        from on-screen non-subtitle text.
        """
        try:
            import cv2
            ocr = _get_paddle_ocr(self.ocr_lang)
        except ImportError:
            import subprocess
            self.progress.emit("Đang cài đặt OCR...")
            subprocess.run([sys.executable, '-m', 'pip', 'install',
                          'opencv-python', 'paddleocr', 'paddlepaddle'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            import cv2
            ocr = _get_paddle_ocr(self.ocr_lang)
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        sample_interval = max(1, int(fps))  # 1 frame/second

        srt_entries = []
        frame_idx = 0
        prev_text = ""

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % sample_interval == 0:
                # Crop to the configured band before OCR.
                h = frame.shape[0]
                y0 = int(h * self.ocr_y_top)
                y1 = int(h * self.ocr_y_bot)
                crop = frame[y0:y1, :] if y1 > y0 else frame
                result = ocr.ocr(crop, cls=True)
                if result and result[0]:
                    texts = [line[1][0] for line in result[0] if line[1][1] > 0.7]
                    text = ' '.join(texts)
                    if text and text != prev_text:
                        start_sec = frame_idx / fps
                        end_sec = start_sec + 1.0
                        idx = len(srt_entries) + 1
                        srt_entries.append(
                            f"{idx}\n{self._format_time(start_sec)} --> "
                            f"{self._format_time(end_sec)}\n{text}\n")
                        prev_text = text
            frame_idx += 1

        cap.release()
        return '\n'.join(srt_entries)

    def _format_time(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class SubtitleExtractPage(QDialog):
    """Dialog for subtitle extraction with Audio and OCR tabs."""

    subtitle_extracted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("✂ Tách Phụ Đề")
        self.resize(600, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self._build_audio_tab()
        self._build_ocr_tab()
        layout.addWidget(self.tabs)

        # Progress
        self.progress_label = QLabel("Sẵn sàng")
        self.progress_label.setStyleSheet("color: #888;")
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

    def _build_audio_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Video selection
        row = QHBoxLayout()
        row.addWidget(QLabel("Video:"))
        self.audio_video_input = QLineEdit()
        self.audio_video_input.setPlaceholderText("Chọn video...")
        row.addWidget(self.audio_video_input)
        btn = QPushButton("📂")
        btn.setFixedWidth(36)
        btn.clicked.connect(lambda: self._add_videos('audio'))
        row.addWidget(btn)
        layout.addLayout(row)

        # Model selection
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Model Whisper:"))
        self.combo_model = QComboBox()
        for key, label in WHISPER_MODEL_OPTIONS:
            self.combo_model.addItem(label, key)
        self.combo_model.setCurrentIndex(1)  # base
        row2.addWidget(self.combo_model)
        btn_dl = QPushButton("⬇ Tải Model")
        btn_dl.clicked.connect(self._download_model)
        row2.addWidget(btn_dl)
        layout.addLayout(row2)

        # Language
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Ngôn ngữ:"))
        self.combo_lang = QComboBox()
        self.combo_lang.addItems(['vi', 'en', 'zh', 'ja', 'ko', 'th', 'fr', 'de', 'es'])
        row3.addWidget(self.combo_lang)
        layout.addLayout(row3)

        # Output
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Lưu tại:"))
        self.audio_output = QLineEdit(str(BASE_DIR / 'output'))
        row4.addWidget(self.audio_output)
        btn_out = QPushButton("📂")
        btn_out.setFixedWidth(36)
        btn_out.clicked.connect(lambda: self._browse_output('audio'))
        row4.addWidget(btn_out)
        layout.addLayout(row4)

        # Start button
        self.btn_audio_start = QPushButton("▶ Tách Phụ Đề Bằng AI (Whisper)")
        self.btn_audio_start.setObjectName("btnBatDau")
        self.btn_audio_start.clicked.connect(self._start_audio_extract)
        layout.addWidget(self.btn_audio_start)

        layout.addStretch()
        self.tabs.addTab(tab, "🎵 Từ Âm Thanh (Whisper)")

    def _build_ocr_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        row.addWidget(QLabel("Video:"))
        self.ocr_video_input = QLineEdit()
        self.ocr_video_input.setPlaceholderText("Chọn video...")
        row.addWidget(self.ocr_video_input)
        btn = QPushButton("📂")
        btn.setFixedWidth(36)
        btn.clicked.connect(lambda: self._add_videos('ocr'))
        row.addWidget(btn)
        layout.addLayout(row)

        # OCR language picker (PaddleOCR codes)
        row_lang = QHBoxLayout()
        row_lang.addWidget(QLabel("Ngôn ngữ OCR:"))
        self.combo_ocr_lang = QComboBox()
        for code, label in OCR_LANGUAGE_OPTIONS:
            self.combo_ocr_lang.addItem(label, code)
        # Default to English (most common for visual subtitles)
        default_idx = next(
            (i for i, (c, _) in enumerate(OCR_LANGUAGE_OPTIONS) if c == 'en'),
            0)
        self.combo_ocr_lang.setCurrentIndex(default_idx)
        row_lang.addWidget(self.combo_ocr_lang)
        layout.addLayout(row_lang)

        # Vertical scan band — only OCR a slice of each frame to skip
        # logos / watermarks and run faster.
        row_band = QHBoxLayout()
        row_band.addWidget(QLabel("Vùng quét (% chiều cao):"))
        self.ocr_y_top = QDoubleSpinBox()
        self.ocr_y_top.setRange(0.0, 0.95)
        self.ocr_y_top.setSingleStep(0.05)
        self.ocr_y_top.setDecimals(2)
        self.ocr_y_top.setValue(0.70)
        row_band.addWidget(self.ocr_y_top)
        row_band.addWidget(QLabel("→"))
        self.ocr_y_bot = QDoubleSpinBox()
        self.ocr_y_bot.setRange(0.05, 1.0)
        self.ocr_y_bot.setSingleStep(0.05)
        self.ocr_y_bot.setDecimals(2)
        self.ocr_y_bot.setValue(1.00)
        row_band.addWidget(self.ocr_y_bot)
        layout.addLayout(row_band)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Lưu tại:"))
        self.ocr_output = QLineEdit(str(BASE_DIR / 'output'))
        row2.addWidget(self.ocr_output)
        layout.addLayout(row2)

        self.btn_ocr_start = QPushButton("▶ Tách Phụ Đề Bằng OCR")
        self.btn_ocr_start.setObjectName("btnBatDau")
        self.btn_ocr_start.clicked.connect(self._start_ocr_extract)
        layout.addWidget(self.btn_ocr_start)

        layout.addStretch()
        self.tabs.addTab(tab, "👁 Từ Hình Ảnh (OCR)")

    def _add_videos(self, mode):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Chọn Video", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov);;All (*)")
        if fp:
            if mode == 'audio':
                self.audio_video_input.setText(fp)
            else:
                self.ocr_video_input.setText(fp)

    def _browse_output(self, mode):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục")
        if d:
            if mode == 'audio':
                self.audio_output.setText(d)
            else:
                self.ocr_output.setText(d)

    def _download_model(self):
        model = self.combo_model.currentData()
        self.progress_bar.show()
        self.dl_thread = WhisperModelDownloadThread(model)
        self.dl_thread.progress.connect(lambda msg: self.progress_label.setText(msg))
        self.dl_thread.finished.connect(self._on_download_finished)
        self.dl_thread.start()

    def _on_download_finished(self, success):
        self.progress_bar.hide()
        if success:
            self.progress_label.setText("✅ Model sẵn sàng!")

    def _start_audio_extract(self):
        video = self.audio_video_input.text()
        if not video or not os.path.exists(video):
            QMessageBox.warning(self, "Lỗi", "Chọn video trước!")
            return
        model = self.combo_model.currentData()
        lang = self.combo_lang.currentText()
        self.btn_audio_start.setEnabled(False)
        self.progress_bar.show()

        self.extract_thread = SubtitleExtractThread([video], True, lang, model)
        self.extract_thread.progress.connect(lambda msg: self.progress_label.setText(msg))
        self.extract_thread.finished.connect(self._on_extract_finished)
        self.extract_thread.error.connect(lambda msg: QMessageBox.warning(self, "Lỗi", msg))
        self.extract_thread.start()

    def _start_ocr_extract(self):
        video = self.ocr_video_input.text()
        if not video or not os.path.exists(video):
            QMessageBox.warning(self, "Lỗi", "Chọn video trước!")
            return
        self.btn_ocr_start.setEnabled(False)
        self.progress_bar.show()

        ocr_lang = self.combo_ocr_lang.currentData() or 'en'
        y_top = self.ocr_y_top.value()
        y_bot = self.ocr_y_bot.value()
        self.extract_thread = SubtitleExtractThread(
            [video], use_audio=False, ocr_lang=ocr_lang,
            ocr_y_top=y_top, ocr_y_bot=y_bot)
        self.extract_thread.progress.connect(lambda msg: self.progress_label.setText(msg))
        self.extract_thread.finished.connect(self._on_extract_finished)
        self.extract_thread.error.connect(lambda msg: QMessageBox.warning(self, "Lỗi", msg))
        self.extract_thread.start()

    def _on_extract_finished(self, results):
        self.progress_bar.hide()
        self.btn_audio_start.setEnabled(True)
        self.btn_ocr_start.setEnabled(True)

        for video_path, srt_text in results:
            # Determine output dir based on active tab
            if self.tabs.currentIndex() == 0:  # Audio tab
                out_dir = self.audio_output.text() or str(BASE_DIR / 'output')
            else:  # OCR tab
                out_dir = self.ocr_output.text() or str(BASE_DIR / 'output')
            os.makedirs(out_dir, exist_ok=True)
            srt_path = os.path.join(out_dir, Path(video_path).stem + '.srt')
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_text)
            self.progress_label.setText(f"✅ Đã lưu: {srt_path}")
            self.subtitle_extracted.emit(srt_path)
