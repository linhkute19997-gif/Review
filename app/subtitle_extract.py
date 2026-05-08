"""
Subtitle Extract Page — Whisper (audio) + PaddleOCR (visual)
"""
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QFileDialog, QTabWidget, QProgressBar,
    QMessageBox, QDialog, QDoubleSpinBox
)
from PyQt6.QtCore import QThread, pyqtSignal

from app.utils.config import WHISPER_MODEL_OPTIONS, BASE_DIR, FFMPEG_PATH
from app.utils.logger import get_logger

logger = get_logger('subtitle_extract')

# Module-level caches — Whisper and PaddleOCR each take ~30–60s to
# load from disk; without these every extract reloaded them.
_WHISPER_CACHE = {}
_OCR_CACHE = {}

# Audio chunks longer than this (seconds) get split for Whisper so the
# user gets a real progress bar and very long files don't OOM.
_WHISPER_CHUNK_SECONDS = 5 * 60


def _detect_cuda() -> bool:
    """True if torch reports a working CUDA device."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — any failure ⇒ no GPU
        return False


def _get_whisper_model(model_name, device):
    key = (model_name, device)
    if key not in _WHISPER_CACHE:
        import whisper  # local import — may need install on first run
        _WHISPER_CACHE[key] = whisper.load_model(model_name, device=device)
    return _WHISPER_CACHE[key]


def _get_paddle_ocr(lang):
    """PaddleOCR singleton, GPU-aware.

    PaddleOCR needs ``use_gpu=True`` *and* a GPU build of paddlepaddle
    to actually benefit. We probe via torch (already a Whisper dep)
    and fall back to CPU if the GPU constructor explodes.
    """
    if lang in _OCR_CACHE:
        return _OCR_CACHE[lang]
    from paddleocr import PaddleOCR
    use_gpu = _detect_cuda()
    try:
        _OCR_CACHE[lang] = PaddleOCR(
            use_angle_cls=True, lang=lang, use_gpu=use_gpu)
    except Exception as exc:  # noqa: BLE001 — paddlepaddle CPU build, etc.
        if use_gpu:
            logger.warning(
                "PaddleOCR GPU init failed (%s) — falling back to CPU", exc)
            _OCR_CACHE[lang] = PaddleOCR(
                use_angle_cls=True, lang=lang, use_gpu=False)
        else:
            raise
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
                 ocr_y_top=0.7, ocr_y_bot=1.0,
                 ocr_scene_threshold=8.0, ocr_min_interval_s=0.5):
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
        # Scene-change OCR sampling: only run OCR when the cropped
        # subtitle band actually changes (mean abs delta on uint8
        # pixels). Threshold ~8 catches typical subtitle transitions
        # without firing on minor video noise.
        self.ocr_scene_threshold = float(ocr_scene_threshold)
        self.ocr_min_interval_s = max(0.1, float(ocr_min_interval_s))
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

    def _ffmpeg_executable(self):
        """Return bundled FFmpeg or PATH lookup."""
        if os.path.exists(FFMPEG_PATH):
            return FFMPEG_PATH
        return shutil.which('ffmpeg') or 'ffmpeg'

    def _probe_duration_seconds(self, video_path) -> float:
        """Return media duration via FFprobe (0.0 on failure)."""
        from app.utils.config import FFPROBE_PATH
        ffprobe = FFPROBE_PATH if os.path.exists(FFPROBE_PATH) else 'ffprobe'
        try:
            result = subprocess.run(
                [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return float((result.stdout or '0').strip() or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ffprobe duration failed: %s", exc)
            return 0.0

    def _extract_from_audio(self, video_path):
        """Extract subtitles from audio using Whisper.

        Long sources (> 5 min) are split into FFmpeg WAV chunks so the
        UI can report ``Đang xử lý chunk i/N`` and we don't load a
        full hour of audio into RAM at once.
        """
        self.progress.emit(f"Tải model {self.whisper_model_name}...")
        try:
            model = _get_whisper_model(self.whisper_model_name, self.device)
        except ImportError:
            self.progress.emit("Đang cài đặt Whisper...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'openai-whisper'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            model = _get_whisper_model(self.whisper_model_name, self.device)

        duration = self._probe_duration_seconds(video_path)
        if duration <= _WHISPER_CHUNK_SECONDS:
            self.progress.emit("Đang nhận diện giọng nói...")
            result = model.transcribe(
                str(video_path), language=self.lang_code)
            segments = list(result.get('segments', []))
        else:
            segments = self._transcribe_in_chunks(model, video_path, duration)

        srt_lines = []
        for i, seg in enumerate(segments, 1):
            start = self._format_time(seg['start'])
            end = self._format_time(seg['end'])
            text = (seg.get('text') or '').strip()
            if not text:
                continue
            srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")
        return '\n'.join(srt_lines)

    def _transcribe_in_chunks(self, model, video_path, duration: float):
        """Split into _WHISPER_CHUNK_SECONDS WAV files, transcribe each."""
        chunks = max(1, math.ceil(duration / _WHISPER_CHUNK_SECONDS))
        ffmpeg = self._ffmpeg_executable()
        scratch = tempfile.mkdtemp(prefix='rpp-whisper-')
        all_segments = []
        try:
            for c in range(chunks):
                offset = c * _WHISPER_CHUNK_SECONDS
                self.progress.emit(
                    f"Whisper chunk {c+1}/{chunks} (từ giây {int(offset)})")
                wav_path = os.path.join(scratch, f"chunk_{c:03d}.wav")
                cmd = [
                    ffmpeg, '-y', '-loglevel', 'error',
                    '-ss', str(offset), '-t', str(_WHISPER_CHUNK_SECONDS),
                    '-i', str(video_path),
                    '-vn', '-ac', '1', '-ar', '16000',
                    '-c:a', 'pcm_s16le', wav_path,
                ]
                try:
                    subprocess.run(
                        cmd, check=True, capture_output=True, timeout=600,
                        creationflags=getattr(
                            subprocess, 'CREATE_NO_WINDOW', 0))
                except subprocess.CalledProcessError as exc:
                    logger.warning(
                        "ffmpeg chunk %d failed: %s",
                        c, exc.stderr.decode('utf-8', 'ignore')[:200])
                    continue
                if not os.path.exists(wav_path):
                    continue
                try:
                    chunk_result = model.transcribe(
                        wav_path, language=self.lang_code)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Whisper chunk %d transcribe failed: %s", c, exc)
                    continue
                for seg in chunk_result.get('segments', []):
                    seg_copy = dict(seg)
                    seg_copy['start'] = float(seg.get('start', 0)) + offset
                    seg_copy['end'] = float(seg.get('end', 0)) + offset
                    all_segments.append(seg_copy)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return all_segments

    def _extract_from_ocr(self, video_path):
        """Extract subtitles from video using PaddleOCR with scene-change sampling.

        We crop to the configured vertical band (default = bottom 30%)
        and only run OCR when the band actually changes — measured as
        mean absolute pixel delta. Static frames between subtitle
        transitions are skipped, which is typically a 3–8x speedup
        for review videos with long stable shots.
        """
        try:
            import cv2
            import numpy as np
            ocr = _get_paddle_ocr(self.ocr_lang)
        except ImportError:
            self.progress.emit("Đang cài đặt OCR...")
            subprocess.run([sys.executable, '-m', 'pip', 'install',
                          'opencv-python', 'paddleocr', 'paddlepaddle'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            import cv2
            import numpy as np
            ocr = _get_paddle_ocr(self.ocr_lang)
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        # Sample at roughly _ocr_min_interval_s_ but never less than
        # one frame — at >0.5s we still catch every subtitle, plus the
        # scene-delta check makes redundant samples free.
        sample_interval = max(1, int(round(fps * self.ocr_min_interval_s)))

        # Hold OCR hits as structured entries so we can extend end-time
        # while the same subtitle is on screen.
        entries: list[dict] = []  # {start, end, text}
        frame_idx = 0
        prev_text = ""
        prev_band_small = None  # 32-row downsample for fast diff
        threshold = self.ocr_scene_threshold

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % sample_interval == 0:
                h = frame.shape[0]
                y0 = int(h * self.ocr_y_top)
                y1 = int(h * self.ocr_y_bot)
                crop = frame[y0:y1, :] if y1 > y0 else frame
                cur_sec = frame_idx / fps

                # Cheap delta check on a downsample to skip static frames.
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (160, 32),
                                   interpolation=cv2.INTER_AREA)
                changed = (
                    prev_band_small is None
                    or float(np.mean(
                        np.abs(small.astype(np.int16)
                               - prev_band_small.astype(np.int16))))
                    >= threshold
                )
                if changed:
                    prev_band_small = small
                    result = ocr.ocr(crop, cls=True)
                    text = ''
                    if result and result[0]:
                        texts = [line[1][0] for line in result[0]
                                 if line[1][1] > 0.7]
                        text = ' '.join(texts)
                    if text and text != prev_text:
                        entries.append({
                            'start': cur_sec,
                            'end': cur_sec + 1.0,
                            'text': text,
                        })
                        prev_text = text
                    elif not text and prev_text:
                        # Subtitle vanished — close out lingering entry.
                        prev_text = ''
                else:
                    # Static frame, same subtitle still on screen — push
                    # the active entry's end forward so it tracks reality.
                    if entries and prev_text:
                        entries[-1]['end'] = max(
                            entries[-1]['end'], cur_sec + 0.5)
            frame_idx += 1

        cap.release()
        logger.debug("OCR scanned %d frames, found %d subtitles",
                     frame_idx, len(entries))

        srt_lines = []
        for i, ent in enumerate(entries, 1):
            srt_lines.append(
                f"{i}\n{self._format_time(ent['start'])} --> "
                f"{self._format_time(ent['end'])}\n{ent['text']}\n")
        return '\n'.join(srt_lines)

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
