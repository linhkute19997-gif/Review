"""
Main Window — Central orchestrator for the application.
"""
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSplitter, QStackedWidget, QFileDialog, QMessageBox,
    QApplication, QSizePolicy, QTableWidgetItem, QDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QSize, QTimer, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QKeySequence, QShortcut

from app.video_player import VideoPlayerSection
from app.config_section import ConfigSection
from app.subtitle_edit import SubtitleEditSection
from app.render_section import RenderSection
from app.domain.models import Job, MediaAsset, Project, Stage, StageStatus
from app.domain.prewarm import PrewarmService, PrewarmStatus
from app.domain.project_file import (
    PROJECT_EXT, load as load_project_file, save as save_project_file,
)
from app.domain.render_queue import RenderQueue
from app.render_queue_dialog import RenderQueueDialog
from app.utils.config import (
    BASE_DIR, load_api_config,
    load_user_preferences, save_user_preferences,
    LANGUAGES, TRANSLATION_MODELS
)
from app.utils.logger import get_logger
from app.utils.srt_parser import parse_srt

logger = get_logger('main_window')

_RENDER_QUEUE_DB = BASE_DIR / 'render_queue.db'


class _PreviewThread(QThread):
    """Background thread for voice preview generation (Edge TTS)."""
    done = pyqtSignal(str)
    err = pyqtSignal(str)

    def __init__(self, vc, rate_str, text, path):
        super().__init__()
        self.vc = vc
        self.rate_str = rate_str
        self.text = text
        self.path = path

    def run(self):
        import asyncio
        try:
            import edge_tts
        except ImportError:
            self.err.emit("edge-tts chưa được cài đặt")
            return

        async def gen():
            communicate = edge_tts.Communicate(
                self.text, self.vc['voice'],
                pitch=self.vc.get('pitch', '+0Hz'),
                rate=self.rate_str)
            await communicate.save(self.path)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(gen())
            self.done.emit(self.path)
        except Exception as exc:
            self.err.emit(str(exc))
        finally:
            loop.close()


class MainWindow(QMainWindow):
    def __init__(self, prewarm: PrewarmService = None):
        super().__init__()
        self.setWindowTitle("Review Phim Pro | V1.0.0")
        self.setMinimumSize(1100, 750)
        self.resize(1280, 900)

        self._lang = 'vi'
        self.current_render_session = None
        self.video_files = []
        self.srt_files = []
        self.translate_thread = None
        self.render_thread = None
        self.voiceover_thread = None
        self._batch_pairs = []
        self._current_project_path = ''
        self._active_job = None

        # Persistent render queue — used both for crash-recovery and
        # the "Hàng Đợi Render" panel.
        self.render_queue = RenderQueue(str(_RENDER_QUEUE_DB))

        # Background pre-warm service (Whisper / PaddleOCR). Optional;
        # ``main.py`` injects one but tests / scripts may pass ``None``.
        self.prewarm = prewarm

        # Load QSS
        qss_path = Path(__file__).parent / 'styles' / 'dark_theme.qss'
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding='utf-8'))

        # P3-1: accept dropped video / SRT files anywhere in the window.
        # Actual MIME inspection lives in :meth:`dragEnterEvent` /
        # :meth:`dropEvent`.
        self.setAcceptDrops(True)

        self._build_menu()
        self._build_ui()
        self._build_status_bar()
        self._build_shortcuts()
        self._wire_prewarm()

        # Offer to resume any jobs that were in-flight when the app last
        # exited. Run after the event loop kicks in so the dialog appears
        # on top of the fully-rendered main window.
        QTimer.singleShot(500, self._maybe_resume_pending_jobs)

    # ═══════════════════════════════════════════════════════
    # Menu Bar
    # ═══════════════════════════════════════════════════════
    def _build_menu(self):
        menubar = self.menuBar()

        # File / Project menu — .rpp save/load + render queue.
        file_menu = menubar.addMenu("📁 Tệp")
        new_action = QAction("Dự Án Mới", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Mở Dự Án (.rpp)…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Lưu Dự Án", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Lưu Dự Án Như…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        queue_action = QAction("📋 Hàng Đợi Render…", self)
        queue_action.triggered.connect(self._open_render_queue)
        file_menu.addAction(queue_action)

        # System menu
        system_menu = menubar.addMenu("⚙️ Hệ Thống")
        info_action = QAction("Thông tin cấu hình", self)
        info_action.triggered.connect(self._show_system_info)
        system_menu.addAction(info_action)

        clear_cache = QAction("Xóa Cache cấu hình", self)
        clear_cache.triggered.connect(self._clear_encoder_cache)
        system_menu.addAction(clear_cache)

        test_action = QAction("Test cấu hình", self)
        test_action.triggered.connect(self._test_encoders)
        system_menu.addAction(test_action)

        out_action = QAction("📁 Thư mục lưu", self)
        out_action.triggered.connect(self._open_output_folder_dialog)
        system_menu.addAction(out_action)
        self.menus = {'system': system_menu, 'file': file_menu}

        # i18n is intentionally not exposed: only ~3 strings are translated,
        # so a switch action would mislead users. Implement full coverage
        # before re-adding a language menu.

        # Tools menu
        tools_menu = menubar.addMenu("Công Cụ")
        douyin_action = QAction("Tải Video Douyin/TikTok", self)
        douyin_action.triggered.connect(self._open_douyin_download_dialog)
        tools_menu.addAction(douyin_action)

        add_text_action = QAction("Thêm Text Overlay", self)
        add_text_action.triggered.connect(self._add_text_overlay)
        tools_menu.addAction(add_text_action)

        add_blur_action = QAction("Thêm Vùng Blur", self)
        add_blur_action.triggered.connect(self._add_blur_overlay)
        tools_menu.addAction(add_blur_action)

        snow_action = QAction("❄ Tuyết Rơi", self)
        snow_action.setCheckable(True)
        snow_action.toggled.connect(self._toggle_snowflake)
        tools_menu.addAction(snow_action)

        tools_menu.addSeparator()
        # P3-17: overlay layout presets — JSON snapshot of every text /
        # blur item currently on the video scene plus the logo path.
        # Border bars are sourced from ``ConfigSection`` so they are
        # not (yet) part of the preset; we keep this scoped to the
        # scene-level overlays the user actually drags around.
        save_layout_action = QAction("💾 Lưu Layout Overlay…", self)
        save_layout_action.triggered.connect(self._save_overlay_preset)
        tools_menu.addAction(save_layout_action)

        load_layout_action = QAction("📂 Tải Layout Overlay…", self)
        load_layout_action.triggered.connect(self._load_overlay_preset)
        tools_menu.addAction(load_layout_action)

        tools_menu.addSeparator()
        prewarm_action = QAction("🔥 Nạp trước Whisper / OCR", self)
        prewarm_action.triggered.connect(self._trigger_prewarm)
        tools_menu.addAction(prewarm_action)

    # ═══════════════════════════════════════════════════════
    # Main UI
    # ═══════════════════════════════════════════════════════
    def _build_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(8, 4, 8, 4)
        editor_layout.setSpacing(4)

        # ── Toolbar row ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        toolbar.addWidget(QLabel("🎬"))
        lbl_vg = QLabel("Video Gốc:")
        lbl_vg.setProperty("class", "labelDim")
        lbl_vg.setFixedWidth(70)
        toolbar.addWidget(lbl_vg)

        self.video_input = QLineEdit()
        self.video_input.setPlaceholderText("Chọn video gốc...")
        toolbar.addWidget(self.video_input)

        btn_video = QPushButton("📂")
        btn_video.setFixedWidth(36)
        btn_video.clicked.connect(self._select_video_file)
        toolbar.addWidget(btn_video)

        div = QLabel("|")
        div.setStyleSheet("background:#2a2a4e;")
        div.setFixedWidth(2)
        toolbar.addWidget(div)

        btn_extract = QPushButton("✂ TÁCH PHỤ ĐỀ")
        btn_extract.setObjectName("btnTachPhuDe")
        btn_extract.clicked.connect(self._open_extract_subtitle_dialog)
        toolbar.addWidget(btn_extract)

        toolbar.addWidget(QLabel("📄"))
        lbl_srt = QLabel("SRT:")
        lbl_srt.setProperty("class", "labelDim")
        lbl_srt.setFixedWidth(30)
        toolbar.addWidget(lbl_srt)

        self.srt_input = QLineEdit()
        self.srt_input.setPlaceholderText("Chọn tệp phụ đề *.srt cần dịch...")
        toolbar.addWidget(self.srt_input)

        btn_srt = QPushButton("📂")
        btn_srt.setFixedWidth(36)
        btn_srt.clicked.connect(self._select_srt_file)
        toolbar.addWidget(btn_srt)

        btn_batch = QPushButton("📁 Hàng Loạt")
        btn_batch.setObjectName("btnHangLoat")
        btn_batch.clicked.connect(self._load_batch_dialog)
        toolbar.addWidget(btn_batch)

        editor_layout.addLayout(toolbar)

        # ── Content splitter ──
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setStyleSheet("QSplitter::handle { background: #1e1e3e; }")

        # Left: video player + subtitle editor
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.video_player = VideoPlayerSection()
        left_layout.addWidget(self.video_player, 3)

        self.subtitle_edit = SubtitleEditSection()
        left_layout.addWidget(self.subtitle_edit, 1)

        content_splitter.addWidget(left)

        # Right: config section
        self.config_section = ConfigSection()
        self.config_section.set_video_player(self.video_player)
        self.config_section.btn_translate.clicked.connect(self._start_translate)
        self.config_section.btn_api_config.clicked.connect(self._open_api_config)
        self.config_section.btn_style_mgr.clicked.connect(self._open_style_manager)
        self.config_section.btn_voice_only.clicked.connect(self._start_voiceover)
        self.config_section.btn_preview_voice.clicked.connect(self._preview_voice)
        # Wire extract tab button
        self.config_section.btn_open_extract.clicked.connect(self._open_extract_subtitle_dialog)
        content_splitter.addWidget(self.config_section)

        content_splitter.setSizes([700, 400])
        editor_layout.addWidget(content_splitter, 1)

        # ── Render section (bottom) ──
        render_row = QHBoxLayout()
        self.render_section = RenderSection()
        render_row.addWidget(self.render_section, 1)

        self.btn_start_video = QPushButton("▶ BẮT ĐẦU TẠO VIDEO")
        self.btn_start_video.setObjectName("btnBatDau")
        self.btn_start_video.setFixedHeight(44)
        self.btn_start_video.clicked.connect(self._start_create_video)
        render_row.addWidget(self.btn_start_video)

        self.btn_batch_render = QPushButton("📦 BATCH RENDER")
        self.btn_batch_render.setObjectName("btnBatDau")
        self.btn_batch_render.setFixedHeight(44)
        self.btn_batch_render.clicked.connect(self._start_batch_render)
        render_row.addWidget(self.btn_batch_render)

        self.btn_save_srt = QPushButton("💾 LƯU SRT")
        self.btn_save_srt.setFixedHeight(44)
        self.btn_save_srt.clicked.connect(self._save_translated_srt)
        render_row.addWidget(self.btn_save_srt)

        editor_layout.addLayout(render_row)

        self.stack.addWidget(editor_page)
        self.stack.setCurrentIndex(0)

    # ═══════════════════════════════════════════════════════
    # File selection
    # ═══════════════════════════════════════════════════════
    def _select_video_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Chọn Video", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm);;All Files (*)")
        if fp:
            self.video_input.setText(fp)
            self.video_player.load_video(fp)

    def _select_srt_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Chọn tệp SRT", "",
            "SRT Files (*.srt);;All Files (*)")
        if fp:
            self.srt_input.setText(fp)
            try:
                with open(fp, 'r', encoding='utf-8-sig') as f:
                    subs = parse_srt(f.read())
                self.subtitle_edit.load_subtitles(subs)
                # P3-3: feed entries into the player so the live
                # subtitle overlay can paint actual text as the
                # cursor moves.
                self.video_player.set_subtitle_entries(subs)
            except Exception as e:
                QMessageBox.warning(self, "Lỗi", f"Không đọc được SRT:\n{e}")

    def _load_batch_dialog(self):
        vids, _ = QFileDialog.getOpenFileNames(
            self, "Chọn nhiều video", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov);;All (*)")
        if not vids:
            return
        self.video_files = vids
        srts, _ = QFileDialog.getOpenFileNames(
            self, "Chọn nhiều SRT", "",
            "SRT Files (*.srt);;All (*)")
        if not srts:
            self.video_files = []  # Clear to prevent mismatch
            QMessageBox.information(self, "Thông Báo", "Đã hủy chọn batch.")
            return
        self.srt_files = srts
        QMessageBox.information(self, "Batch",
            f"Đã chọn {len(self.video_files)} video + {len(self.srt_files)} SRT")

    # ═══════════════════════════════════════════════════════
    # Translation
    # ═══════════════════════════════════════════════════════
    def _start_translate(self):
        # Guard against duplicate threads
        if self.translate_thread and self.translate_thread.isRunning():
            QMessageBox.warning(self, "Thông Báo", "Đang dịch! Vui lòng chờ hoàn thành.")
            return
        srt_path = self.srt_input.text()
        if not srt_path or not os.path.exists(srt_path):
            QMessageBox.warning(self, "Lỗi", "Chọn tệp SRT trước!")
            return
        try:
            with open(srt_path, 'r', encoding='utf-8-sig') as f:
                subs = parse_srt(f.read())
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không đọc được SRT:\n{e}")
            return
        if not subs:
            QMessageBox.warning(self, "Lỗi", "SRT trống!")
            return

        model = self.config_section.get_selected_model()
        src = self.config_section.get_source_lang()
        tgt = self.config_section.get_target_lang()
        api_config = load_api_config()
        api_keys = api_config.get(model, {}).get('api_key', '')

        # Disable button while translating
        self.config_section.btn_translate.setEnabled(False)
        self.config_section.btn_translate.setText("⏳ Đang dịch...")

        from app.threads.translate_thread import TranslateThread
        # Use existing edited subtitles if already loaded
        existing_subs = self.subtitle_edit._subtitles
        if existing_subs and len(existing_subs) == len(subs):
            subs = existing_subs  # Preserve user edits
        else:
            self.subtitle_edit.load_subtitles(subs)

        self.translate_thread = TranslateThread(subs, 4, src, tgt, model, api_keys)
        self.translate_thread.progress.connect(self._on_translate_progress)
        self.translate_thread.finished_signal.connect(self._on_translate_done)
        self.translate_thread.start()

        self.config_section.translate_status.setText("Đang dịch...")

    def _on_translate_progress(self, index, text):
        self.subtitle_edit.update_translated(index, text)
        total = len(self.subtitle_edit._subtitles)
        self.config_section.translate_progress.setText(f"Tiến trình: {index+1}/{total}")

    def _on_translate_done(self):
        self.config_section.translate_status.setText("✅ Dịch xong!")
        self.config_section.translate_progress.setText("")
        self.config_section.btn_translate.setEnabled(True)
        self.config_section.btn_translate.setText("▶ Tiến Hành Dịch Phụ Đề")
        self.translate_thread = None  # Cleanup stale thread reference
        # P3-3: refresh the player overlay with the newly-translated
        # text so the user sees the translation while reviewing.
        self.video_player.set_subtitle_entries(
            self.subtitle_edit._subtitles)

    # ═══════════════════════════════════════════════════════
    # Video creation
    # ═══════════════════════════════════════════════════════
    def _start_create_video(self):
        video_path = self.video_input.text()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "⚠️ Chưa Chọn Video",
                "Vui lòng chọn video gốc trước khi bắt đầu tạo video!")
            return

        srt_path = self.srt_input.text()
        subtitles = self.subtitle_edit._subtitles or []
        if not subtitles and srt_path and os.path.exists(srt_path):
            try:
                with open(srt_path, 'r', encoding='utf-8-sig') as f:
                    subtitles = parse_srt(f.read())
            except Exception:
                pass

        config = self.config_section.get_config()
        overlays = self._collect_overlay_data()
        base_name = Path(video_path).stem
        output_dir = self._resolve_output_dir()
        output_video = os.path.join(output_dir, f"{base_name}_output.mp4")

        # Disable button
        self.btn_start_video.setEnabled(False)
        self.btn_start_video.setText("⏳ Đang render...")

        # Persist this render to the SQLite queue so a crash mid-render
        # leaves a recoverable trail. _on_video_finished / _on_video_error
        # finalise the entry on success / failure.
        self._enqueue_active_render(
            video_path=video_path,
            output_path=output_video,
            config=config,
            subs=subtitles,
            srt_path=srt_path,
        )

        from app.threads.video_creator import VideoCreatorThread
        self.render_thread = VideoCreatorThread(
            video_path, output_video, config, subtitles, output_dir, overlays)
        self.render_thread.progress.connect(self._on_video_progress)
        self.render_thread.status.connect(self._on_video_status)
        self.render_thread.error.connect(self._on_video_error)
        self.render_thread.finished_video.connect(self._on_video_finished)
        self.render_thread.start()
        self.render_section.set_exporting_status(True, "Detecting...")

    def _on_video_progress(self, value):
        self.render_section.progress_bar.setValue(value)

    def _on_video_status(self, text):
        self.render_section.status_label.setText(text)

    def _on_video_error(self, text):
        self._finalise_active_job(StageStatus.FAILED, error=text)
        QMessageBox.critical(self, "Lỗi Render", text)
        self.render_section.set_exporting_status(False)
        self.btn_start_video.setEnabled(True)
        self.btn_start_video.setText("▶ BẮT ĐẦU TẠO VIDEO")
        self.render_thread = None

    def _on_video_finished(self, path):
        self._finalise_active_job(StageStatus.DONE)
        self.render_section.set_exporting_status(False)
        self.btn_start_video.setEnabled(True)
        self.btn_start_video.setText("▶ BẮT ĐẦU TẠO VIDEO")
        self.render_thread = None  # Cleanup stale reference
        # P3-2: ship a custom "Open output folder" button with the
        # done dialog so the user doesn't have to hand-navigate to
        # the saved video.
        self._show_render_done_dialog(path)
        # Auto shutdown if configured
        if self.config_section.get_config().get('auto_shutdown'):
            self._schedule_shutdown(delay_seconds=60)

    def _show_render_done_dialog(self, path: str):
        """Post-render confirmation with shortcuts to the output file."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Hoàn Thành")
        box.setText(f"Video đã được tạo:\n{path}")
        btn_open_folder = box.addButton(
            "📂 Mở thư mục", QMessageBox.ButtonRole.ActionRole)
        btn_open_file = box.addButton(
            "▶ Mở video", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_open_folder:
            self._open_folder_native(os.path.dirname(path) or '.')
        elif clicked is btn_open_file:
            self._open_folder_native(path)

    @staticmethod
    def _open_folder_native(target: str):
        """Cross-platform ``open`` for files or directories."""
        if not target:
            return
        try:
            if sys.platform.startswith('win'):
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', target])
            else:
                subprocess.Popen(['xdg-open', target])
        except Exception as exc:  # noqa: BLE001
            logger.warning('open native target failed (%s): %s', target, exc)

    def _save_translated_srt(self):
        """Save translated subtitles to SRT file."""
        # Sync edits from table first
        self.subtitle_edit._sync_table_edits()
        subs = self.subtitle_edit._subtitles
        if not subs:
            QMessageBox.warning(self, "Lỗi", "Chưa có phụ đề để lưu!")
            return
        from app.utils.srt_parser import subtitles_to_srt
        # Default filename from current SRT path or video name
        default_name = ''
        if self.srt_input.text():
            default_name = os.path.splitext(self.srt_input.text())[0] + '_translated.srt'
        elif self.video_input.text():
            default_name = os.path.splitext(self.video_input.text())[0] + '_translated.srt'
        fp, _ = QFileDialog.getSaveFileName(
            self, "Lưu SRT", default_name, "SRT Files (*.srt);;All (*)")
        if fp:
            srt_text = subtitles_to_srt(subs, use_translated=True)
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(srt_text)
            QMessageBox.information(self, "Thành Công", f"Đã lưu SRT:\n{fp}")

    # ═══════════════════════════════════════════════════════
    # Menu actions
    # ═══════════════════════════════════════════════════════
    def _schedule_shutdown(self, delay_seconds=60):
        """Show an auto-shutdown countdown (P3-11).

        We pop a :class:`ShutdownCountdownDialog` so the user can
        cancel the shutdown if they realise they wanted to keep
        the machine running. If the dialog confirms, we dispatch
        ``shutdown`` on the user's platform.
        """
        try:
            from app.shutdown_dialog import ShutdownCountdownDialog
        except ImportError:
            ShutdownCountdownDialog = None  # type: ignore

        if ShutdownCountdownDialog is not None:
            dialog = ShutdownCountdownDialog(int(delay_seconds), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                # User cancelled — abort the shutdown entirely.
                return
        try:
            if sys.platform.startswith('win'):
                cmd = ['shutdown', '/s', '/t', '5']
            elif sys.platform == 'darwin':
                cmd = ['sudo', 'shutdown', '-h', '+1']
            else:
                cmd = ['shutdown', '-h', '+1']
            subprocess.Popen(cmd)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "Auto Shutdown",
                f"Không thể lên lịch tắt máy ({platform.system()}): {exc}")

    def _open_output_folder_dialog(self):
        """Open dialog to set persistent output folder for renders."""
        from app.dialogs import OutputFolderDialog
        prefs = load_user_preferences()
        current = prefs.get('output_folder', '') or str(BASE_DIR / 'output')
        dialog = OutputFolderDialog(current, self)
        if dialog.exec():
            new_path = dialog.path_input.text().strip()
            if new_path:
                prefs['output_folder'] = new_path
                save_user_preferences(prefs)
                try:
                    os.makedirs(new_path, exist_ok=True)
                except OSError as exc:
                    QMessageBox.warning(
                        self, "Lỗi",
                        f"Không thể tạo thư mục {new_path}: {exc}")

    def _resolve_output_dir(self):
        """Return the user-configured output dir, falling back to BASE_DIR/output."""
        prefs = load_user_preferences()
        out_dir = prefs.get('output_folder', '') or str(BASE_DIR / 'output')
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _show_system_info(self):
        from app.utils.encoder_detector import EncoderDetector
        detector = EncoderDetector()
        enc_info = detector.get_system_info()
        info = (f"Python: {sys.version}\nPlatform: {sys.platform}\n"
                f"Base Dir: {BASE_DIR}\n\n{enc_info}")
        try:
            import torch
            if torch.cuda.is_available():
                info += f"\n\nGPU: {torch.cuda.get_device_name(0)}"
            else:
                info += "\nGPU: Not available (CPU mode)"
        except ImportError:
            info += "\nGPU: PyTorch not installed"
        QMessageBox.information(self, "Thông Tin Hệ Thống", info)

    def _clear_encoder_cache(self):
        from app.utils.encoder_detector import EncoderDetector
        detector = EncoderDetector()
        detector.clear_cache()
        QMessageBox.information(self, "Cache", "Đã xóa cache encoder!")

    def _test_encoders(self):
        from app.utils.encoder_detector import EncoderDetector
        detector = EncoderDetector()
        detector.clear_cache()
        available = detector.detect_available_encoders()
        msg = f"Phát hiện {len(available)} encoder:\n"
        for enc in available:
            msg += f"  ✓ {enc['description']}\n"
        QMessageBox.information(self, "Test Encoder", msg)

    def _open_api_config(self):
        from app.dialogs import APIConfigDialog
        dialog = APIConfigDialog(self)
        dialog.exec()

    def _open_style_manager(self):
        from app.dialogs import StyleManagerDialog
        dialog = StyleManagerDialog(self)
        if dialog.exec():
            self.config_section._load_styles()

    def _open_extract_subtitle_dialog(self):
        from app.subtitle_extract import SubtitleExtractPage
        dialog = SubtitleExtractPage(self)
        dialog.subtitle_extracted.connect(self._on_subtitle_extracted)
        dialog.exec()

    def _on_subtitle_extracted(self, srt_path):
        self.srt_input.setText(srt_path)
        try:
            with open(srt_path, 'r', encoding='utf-8-sig') as f:
                subs = parse_srt(f.read())
            self.subtitle_edit.load_subtitles(subs)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không đọc được SRT:\n{e}")

    def _add_text_overlay(self):
        from app.overlays import AddTextDialog, DraggableTextItem
        data = AddTextDialog.get_data(self)
        if data:
            item = DraggableTextItem(100, 100, data['text'], data['font_size'], data['color'])
            self.video_player.scene.addItem(item)

    def _add_blur_overlay(self):
        from app.overlays import DraggableBlurRegion
        item = DraggableBlurRegion(50, 50, 150, 100)
        self.video_player.scene.addItem(item)

    def _toggle_snowflake(self, checked):
        if checked:
            if not hasattr(self, '_snow_overlay') or self._snow_overlay is None:
                from app.snow_overlay import SnowflakeOverlay
                self._snow_overlay = SnowflakeOverlay(self)
                self._snow_overlay.setGeometry(self.rect())
                self._snow_overlay.show()
        else:
            if hasattr(self, '_snow_overlay') and self._snow_overlay:
                self._snow_overlay.hide()
                self._snow_overlay.deleteLater()
                self._snow_overlay = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_snow_overlay') and self._snow_overlay:
            self._snow_overlay.setGeometry(self.rect())

    def _open_douyin_download_dialog(self):
        from app.dialogs import DouyinDownloadDialog
        dialog = DouyinDownloadDialog(self)
        dialog.exec()

    # ═══════════════════════════════════════════════════════
    # Drag-drop (P3-1)
    # ═══════════════════════════════════════════════════════
    _VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.m4v')
    _SRT_EXTS = ('.srt',)

    def dragEnterEvent(self, event):  # noqa: N802 — Qt API
        """Accept drops only when at least one file looks usable."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return super().dragEnterEvent(event)
        for url in mime.urls():
            path = url.toLocalFile().lower()
            if path.endswith(self._VIDEO_EXTS + self._SRT_EXTS):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 — Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 — Qt API
        """Route dropped files to the matching input.

        The first video drop populates the ``Video Gốc`` field and
        loads it into the player. The first ``.srt`` drop fills the
        SRT field and re-populates the editor.
        """
        urls = event.mimeData().urls()
        if not urls:
            return super().dropEvent(event)
        videos = [u.toLocalFile() for u in urls
                  if u.toLocalFile().lower().endswith(self._VIDEO_EXTS)]
        srts = [u.toLocalFile() for u in urls
                if u.toLocalFile().lower().endswith(self._SRT_EXTS)]
        if videos:
            self.video_input.setText(videos[0])
            try:
                self.video_player.load_video(videos[0])
            except Exception as exc:  # noqa: BLE001
                logger.warning('drop: load_video failed: %s', exc)
        if srts:
            self.srt_input.setText(srts[0])
            try:
                with open(srts[0], 'r', encoding='utf-8-sig') as f:
                    subs = parse_srt(f.read())
                self.subtitle_edit.load_subtitles(subs)
                # P3-3: drag-drop SRT also feeds the live preview.
                self.video_player.set_subtitle_entries(subs)
            except Exception as exc:  # noqa: BLE001
                logger.warning('drop: parse_srt failed: %s', exc)
        if videos or srts:
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ═══════════════════════════════════════════════════════
    # Keyboard shortcuts (P3-7)
    # ═══════════════════════════════════════════════════════
    def _build_shortcuts(self):
        """Wire global shortcuts that mirror the main toolbar buttons.

        Ctrl+S / Ctrl+Shift+S already come from the File menu actions
        wired in :meth:`_build_menu`; we only add the action shortcuts
        that don't have a menu entry.
        """
        QShortcut(QKeySequence("Ctrl+T"), self,
                  activated=self._start_translate)
        QShortcut(QKeySequence("Ctrl+R"), self,
                  activated=self._start_create_video)
        QShortcut(QKeySequence("Ctrl+B"), self,
                  activated=self._start_batch_render)
        QShortcut(QKeySequence("Ctrl+H"), self,
                  activated=self._open_search_replace_dialog)
        QShortcut(QKeySequence("Ctrl+Z"), self,
                  activated=self._undo_subtitle)
        QShortcut(QKeySequence("Ctrl+Y"), self,
                  activated=self._redo_subtitle)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self,
                  activated=self._redo_subtitle)
        QShortcut(QKeySequence("F2"), self,
                  activated=self._open_extract_subtitle_dialog)

    def _open_search_replace_dialog(self):
        """Forward Ctrl+H to the subtitle editor."""
        try:
            self.subtitle_edit.open_search_replace()
        except Exception as exc:  # noqa: BLE001
            logger.debug('search/replace dialog failed: %s', exc)

    def _undo_subtitle(self):
        try:
            self.subtitle_edit.undo()
        except Exception:  # noqa: BLE001
            pass

    def _redo_subtitle(self):
        try:
            self.subtitle_edit.redo()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        """Save preferences on exit and stop threads."""
        self.config_section._save_user_preferences()
        if hasattr(self, 'translate_thread') and self.translate_thread and self.translate_thread.isRunning():
            if hasattr(self.translate_thread, 'stop'):
                self.translate_thread.stop()
            else:
                self.translate_thread._running = False
        if hasattr(self, 'render_thread') and self.render_thread and self.render_thread.isRunning():
            self.render_thread.stop()
        if hasattr(self, 'voiceover_thread') and self.voiceover_thread and self.voiceover_thread.isRunning():
            self.voiceover_thread.stop()
        super().closeEvent(event)

    def _match_videos_with_subtitles(self, videos, srts):
        srt_dict = {Path(s).stem: s for s in srts}
        pairs = []
        unmatched = []
        for v in videos:
            stem = Path(v).stem
            if stem in srt_dict:
                pairs.append((v, srt_dict[stem]))
            else:
                unmatched.append(v)
        return pairs, unmatched

    # ═══════════════════════════════════════════════════════
    # Voice-Over
    # ═══════════════════════════════════════════════════════
    def _start_voiceover(self):
        # Guard against duplicate threads
        if self.voiceover_thread and self.voiceover_thread.isRunning():
            QMessageBox.warning(self, "Thông Báo", "Đang tạo lồng tiếng! Vui lòng chờ.")
            return
        subs = self.subtitle_edit._subtitles
        srt_path = self.config_section.voice_srt_input.text()
        if srt_path and os.path.exists(srt_path):
            try:
                with open(srt_path, 'r', encoding='utf-8-sig') as f:
                    subs = parse_srt(f.read())
            except Exception:
                pass
        if not subs:
            QMessageBox.warning(self, "Lỗi", "Không có phụ đề để lồng tiếng!")
            return

        voice_type = self.config_section.combo_voice_type.currentText()
        speed = self.config_section.voice_speed.value()
        provider = self.config_section.combo_voice_provider.currentText()
        target_lang = self.config_section.get_target_lang()

        self.config_section.btn_voice_only.setEnabled(False)
        self.config_section.btn_voice_only.setText("⏳ Đang tạo...")

        from app.threads.voiceover_thread import VoiceOverThread
        self.voiceover_thread = VoiceOverThread(subs, voice_type, speed, 100, provider, target_lang)
        self.voiceover_thread.progress.connect(self._on_voice_progress)
        self.voiceover_thread.finished_signal.connect(self._on_voice_done)
        self.voiceover_thread.error.connect(self._on_voice_error)
        self.voiceover_thread.start()

    def _on_voice_progress(self, msg):
        self.render_section.status_label.setText(msg)

    def _on_voice_done(self, path):
        self.config_section.btn_voice_only.setEnabled(True)
        self.config_section.btn_voice_only.setText("▶ Tạo File Lồng Tiếng")
        self.voiceover_thread = None
        QMessageBox.information(self, "Lồng Tiếng", f"✅ Đã tạo file:\n{path}")
        self.config_section.voice_file_path.setText(path)
        self.config_section.chk_voice_file.setChecked(True)

    def _on_voice_error(self, msg):
        self.config_section.btn_voice_only.setEnabled(True)
        self.config_section.btn_voice_only.setText("▶ Tạo File Lồng Tiếng")
        self.voiceover_thread = None
        QMessageBox.critical(self, "Lỗi Lồng Tiếng", msg)

    def _preview_voice(self):
        """Preview voice with a sample text — runs in background thread."""
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            QMessageBox.warning(self, "Lỗi", "edge-tts chưa được cài đặt!")
            return

        voice_type = self.config_section.combo_voice_type.currentText()
        from app.utils.config import VOICE_CONFIGS_EDGE_VI
        vc = None
        for v in VOICE_CONFIGS_EDGE_VI:
            if v['label'] == voice_type:
                vc = v
                break
        if not vc:
            vc = VOICE_CONFIGS_EDGE_VI[0]

        preview_text = "Xin chào, đây là giọng đọc thử nghiệm."
        preview_path = os.path.join(str(BASE_DIR), 'output', '_preview.mp3')
        os.makedirs(os.path.dirname(preview_path), exist_ok=True)

        # Use user's speed setting
        user_speed = self.config_section.voice_speed.value()
        user_rate_pct = user_speed - 100
        rate_str = f"{'+' if user_rate_pct >= 0 else ''}{user_rate_pct}%"

        self.render_section.status_label.setText("Đang tạo preview...")

        self._preview_thread = _PreviewThread(
            vc, rate_str, preview_text, preview_path)
        self._preview_thread.done.connect(self._on_preview_done)
        self._preview_thread.err.connect(
            lambda msg: self.render_section.status_label.setText(
                f"❌ Preview: {msg}"))
        self._preview_thread.start()

    def _on_preview_done(self, path):
        self.render_section.status_label.setText("✅ Preview sẵn sàng")
        if os.path.exists(path):
            self.video_player.player.setSource(QUrl.fromLocalFile(path))
            self.video_player.play()

    # ═══════════════════════════════════════════════════════
    # Batch Render
    # ═══════════════════════════════════════════════════════
    def _start_batch_render(self):
        """Batch render multiple video+SRT pairs."""
        if not self.video_files:
            QMessageBox.warning(self, "Lỗi", "Chưa chọn video!")
            return
        # Guard against duplicate batch render
        if self.render_thread and self.render_thread.isRunning():
            QMessageBox.warning(self, "Thông Báo", "Đang render! Vui lòng chờ.")
            return

        self.btn_batch_render.setEnabled(False)
        self.btn_batch_render.setText("⏳ Đang batch...")

        pairs, unmatched = self._match_videos_with_subtitles(self.video_files, self.srt_files)
        if not pairs and not unmatched:
            QMessageBox.warning(self, "Lỗi", "Không có video nào để render!")
            return

        config = self.config_section.get_config()
        output_dir = self._resolve_output_dir()

        # Add all to render table. P3-13 replaces the single status
        # column with a progress bar + status text by routing through
        # ``populate_batch`` which builds the full row up front.
        all_items = pairs + [(v, None) for v in unmatched]
        items_for_table = []
        for item in all_items:
            video_path = item[0] if isinstance(item, tuple) else item
            base = Path(video_path).stem
            out = os.path.join(output_dir, f"{base}_output.mp4")
            items_for_table.append({
                'video_path': video_path,
                'output_path': out,
            })
        self.render_section.populate_batch(items_for_table)

        self._batch_queue = all_items
        self._batch_index = 0
        self._batch_config = config
        self._batch_output_dir = output_dir
        self._run_next_batch_item()

    def _run_next_batch_item(self):
        if self._batch_index >= len(self._batch_queue):
            self.render_section.set_exporting_status(False)
            self.render_thread = None
            self.btn_batch_render.setEnabled(True)
            self.btn_batch_render.setText("📦 BATCH RENDER")
            QMessageBox.information(self, "Batch", "✅ Đã render xong tất cả video!")
            return

        item = self._batch_queue[self._batch_index]
        video_path, srt_path = (item if isinstance(item, tuple) else (item, None))
        base = Path(video_path).stem
        out = os.path.join(self._batch_output_dir, f"{base}_output.mp4")

        subs = []
        if srt_path and os.path.exists(srt_path):
            try:
                with open(srt_path, 'r', encoding='utf-8-sig') as f:
                    subs = parse_srt(f.read())
            except Exception:
                pass

        from app.threads.video_creator import VideoCreatorThread
        self.render_thread = VideoCreatorThread(
            video_path, out, self._batch_config, subs, self._batch_output_dir,
            self._collect_overlay_data())
        self.render_thread.progress.connect(self._on_batch_item_progress)
        self.render_thread.status.connect(self._on_video_status)
        self.render_thread.error.connect(self._on_batch_error)
        self.render_thread.finished_video.connect(self._on_batch_item_done)
        self.render_thread.start()
        self.render_section.set_exporting_status(
            True, f"Item {self._batch_index + 1}")
        # P3-13: flip the per-row status to in-progress; the progress
        # bar starts at 0 because populate_batch already initialised it.
        self.render_section.set_batch_item_status(
            self._batch_index, '⏵ Render...', '#ff9800')
        self.render_section.set_batch_item_progress(self._batch_index, 0)

    def _on_batch_item_progress(self, percent: int) -> None:
        """Forward worker progress to both the global + batch row bars."""
        self._on_video_progress(percent)
        self.render_section.set_batch_item_progress(
            self._batch_index, percent)

    def _on_batch_item_done(self, path):
        self.render_section.mark_batch_item_done(
            self._batch_index, ok=True)
        self.render_thread = None  # Cleanup before next
        self._batch_index += 1
        self._run_next_batch_item()

    def _on_batch_error(self, msg):
        self.render_section.mark_batch_item_done(
            self._batch_index, ok=False,
            message=f"❌ Lỗi: {msg[:40]}" if msg else '❌ Lỗi')
        logger.error("Batch render item %s failed: %s", self._batch_index, msg)
        self._batch_index += 1
        # If last item errored, re-enable button
        if self._batch_index >= len(self._batch_queue):
            self.render_thread = None
            self.btn_batch_render.setEnabled(True)
            self.btn_batch_render.setText("📦 BATCH RENDER")
        self._run_next_batch_item()

    def _collect_overlay_data(self):
        """Collect overlay data from video player scene."""
        from app.overlays import DraggableTextItem, DraggableBlurRegion
        texts = []
        blurs = []
        for item in self.video_player.scene.items():
            if isinstance(item, DraggableTextItem):
                texts.append(item.get_data())
            elif isinstance(item, DraggableBlurRegion):
                blurs.append(item.get_region_data())
        return {
            'texts': texts,
            'blurs': blurs,
            'logo_path': self.config_section.logo_path.text(),
            'preview_width': max(self.video_player.view.width(), 1),
            'preview_height': max(self.video_player.view.height(), 1),
        }

    # ── P3-17 overlay layout presets ─────────────────────────
    _OVERLAY_PRESET_VERSION = 1

    def _save_overlay_preset(self) -> None:
        """Persist the current overlay scene to a JSON preset file."""
        import json
        snapshot = self._collect_overlay_data()
        payload = {
            'version': self._OVERLAY_PRESET_VERSION,
            'logo_path': snapshot.get('logo_path', ''),
            'texts': snapshot.get('texts', []),
            'blurs': snapshot.get('blurs', []),
            'preview_width': snapshot.get('preview_width', 1),
            'preview_height': snapshot.get('preview_height', 1),
        }
        default = str(BASE_DIR / 'overlay_preset.json')
        fp, _ = QFileDialog.getSaveFileName(
            self, "Lưu Layout Overlay", default,
            "Layout JSON (*.json);;All Files (*)")
        if not fp:
            return
        if not fp.lower().endswith('.json'):
            fp += '.json'
        try:
            with open(fp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Layout Overlay",
                                  f"Không lưu được layout:\n{exc}")
            return
        self.statusBar().showMessage(f"Đã lưu layout {Path(fp).name}", 4000)

    def _load_overlay_preset(self) -> None:
        """Replace the current overlay scene with a preset from disk."""
        import json
        from app.overlays import DraggableBlurRegion, DraggableTextItem
        fp, _ = QFileDialog.getOpenFileName(
            self, "Tải Layout Overlay", str(BASE_DIR),
            "Layout JSON (*.json);;All Files (*)")
        if not fp:
            return
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Layout Overlay",
                                  f"Không đọc được layout:\n{exc}")
            return
        version = payload.get('version', 0)
        if version > self._OVERLAY_PRESET_VERSION:
            QMessageBox.warning(
                self, "Layout Overlay",
                f"Phiên bản layout {version} mới hơn app — sẽ thử tải nhưng "
                "có thể thiếu thuộc tính.")
        # Wipe existing draggable items so we don't accumulate.
        for item in list(self.video_player.scene.items()):
            if isinstance(item, (DraggableTextItem, DraggableBlurRegion)):
                self.video_player.scene.removeItem(item)
        for entry in payload.get('texts', []) or []:
            try:
                node = DraggableTextItem(
                    float(entry.get('x', 100)),
                    float(entry.get('y', 100)),
                    entry.get('text', 'Text'),
                    int(entry.get('font_size', 20)),
                    entry.get('color', '#ffffff'),
                )
                width = float(entry.get('width', 200))
                height = float(entry.get('height', 40))
                node.setRect(0, 0, max(20.0, width), max(20.0, height))
                self.video_player.scene.addItem(node)
            except Exception as exc:  # noqa: BLE001
                logger.warning("preset: skipped text overlay (%s)", exc)
        for entry in payload.get('blurs', []) or []:
            try:
                node = DraggableBlurRegion(
                    float(entry.get('x', 50)),
                    float(entry.get('y', 50)),
                    float(entry.get('width', 150)),
                    float(entry.get('height', 100)),
                )
                node.blur_strength = int(entry.get('strength', 15))
                self.video_player.scene.addItem(node)
            except Exception as exc:  # noqa: BLE001
                logger.warning("preset: skipped blur region (%s)", exc)
        logo = (payload.get('logo_path') or '').strip()
        if logo:
            self.config_section.logo_path.setText(logo)
        self.statusBar().showMessage(f"Đã tải layout {Path(fp).name}", 4000)

    # ═══════════════════════════════════════════════════════
    # Phase 1 — Project file (.rpp), render queue, pre-warm
    # ═══════════════════════════════════════════════════════
    def _build_status_bar(self):
        """Wire up the persistent status bar.

        We surface two pieces of long-lived state here:

        * the pre-warm progress (Whisper / OCR loading), and
        * the active project path so the user can tell whether they
          are editing a saved ``.rpp`` or an unsaved scratch session.
        """
        bar = self.statusBar()
        self._project_status = QLabel("Dự án: (chưa lưu)")
        self._prewarm_status = QLabel("Pre-warm: chưa chạy")
        bar.addWidget(self._project_status, 1)
        bar.addPermanentWidget(self._prewarm_status)

    def _wire_prewarm(self):
        if self.prewarm is None:
            return
        # Observers run on the worker thread; bounce back to GUI via
        # QTimer so we never touch widgets from a non-Qt thread.
        def _push(status: PrewarmStatus, label=self._prewarm_status):
            QTimer.singleShot(
                0, lambda: label.setText(f"Pre-warm: {status.summary()}"))
        self.prewarm.add_observer(_push)

    def _trigger_prewarm(self):
        if self.prewarm is None:
            QMessageBox.information(self, "Pre-warm",
                                     "Service pre-warm chưa được khởi tạo.")
            return
        self.prewarm.start()
        QMessageBox.information(self, "Pre-warm",
                                 "Đang nạp Whisper / PaddleOCR ở chế độ nền."
                                 " Tiến độ hiện ở thanh trạng thái.")

    # ── Project file (.rpp) ──────────────────────────────────
    def _new_project(self):
        self._current_project_path = ''
        self.video_input.clear()
        self.srt_input.clear()
        self.subtitle_edit.load_subtitles([])
        self._project_status.setText("Dự án: (chưa lưu)")

    def _open_project(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Mở dự án", "",
            f"Review Phim Pro (*{PROJECT_EXT});;All Files (*)")
        if not fp:
            return
        try:
            project = load_project_file(fp)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Mở dự án",
                                  f"Không đọc được file:\n{exc}")
            return
        self._apply_project(project, fp)

    def _save_project(self):
        if not self._current_project_path:
            self._save_project_as()
            return
        self._do_save_project(self._current_project_path)

    def _save_project_as(self):
        default = self._current_project_path or str(
            BASE_DIR / 'output' / 'project.rpp')
        fp, _ = QFileDialog.getSaveFileName(
            self, "Lưu dự án", default,
            f"Review Phim Pro (*{PROJECT_EXT});;All Files (*)")
        if not fp:
            return
        if not fp.lower().endswith(PROJECT_EXT):
            fp += PROJECT_EXT
        self._do_save_project(fp)

    def _do_save_project(self, fp: str):
        project = self._build_project_snapshot(name=Path(fp).stem)
        try:
            save_project_file(project, fp)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Lưu dự án",
                                  f"Không lưu được dự án:\n{exc}")
            return
        self._current_project_path = fp
        self._project_status.setText(f"Dự án: {Path(fp).name}")
        self.statusBar().showMessage(f"Đã lưu {fp}", 4000)

    def _build_project_snapshot(self, name: str) -> Project:
        """Capture the current editor state as a domain ``Project``."""
        # Sync any pending table edits back into the in-memory list.
        try:
            self.subtitle_edit._sync_table_edits()
        except Exception:  # noqa: BLE001 — defensive: editor may be empty
            pass
        subs = list(self.subtitle_edit._subtitles or [])
        translation = {
            'model': self.config_section.get_selected_model(),
            'source': self.config_section.get_source_lang(),
            'target': self.config_section.get_target_lang(),
        }
        project = Project(
            name=name,
            translation=translation,
            config=self.config_section.get_config(),
            subtitles=subs,
        )
        return project

    def _apply_project(self, project: Project, path: str):
        """Push a loaded project back into the editor widgets."""
        self._current_project_path = path
        self._project_status.setText(f"Dự án: {Path(path).name}")
        if project.subtitles:
            self.subtitle_edit.load_subtitles(list(project.subtitles))
        self.statusBar().showMessage(
            f"Đã mở dự án {project.name} ({len(project.jobs)} job)", 4000)

    # ── Render queue persistence ────────────────────────────
    def _open_render_queue(self):
        dialog = RenderQueueDialog(self.render_queue,
                                    on_retry=self._retry_queued_job,
                                    parent=self)
        dialog.exec()

    def _retry_queued_job(self, job: Job):
        """Re-render a job pulled from the persistent queue."""
        video_asset = job.asset_by_kind('video')
        if not video_asset or not os.path.exists(video_asset.path):
            QMessageBox.warning(self, "Hàng đợi",
                                 "Video gốc không còn tồn tại trên đĩa.")
            return
        srt_asset = job.asset_by_kind('srt')
        subs = []
        if srt_asset and os.path.exists(srt_asset.path):
            try:
                with open(srt_asset.path, 'r', encoding='utf-8-sig') as f:
                    subs = parse_srt(f.read())
            except Exception:  # noqa: BLE001
                pass
        config = dict(job.config or self.config_section.get_config())
        output_dir = (os.path.dirname(job.output_path)
                       if job.output_path else self._resolve_output_dir())
        os.makedirs(output_dir, exist_ok=True)

        self._active_job = job
        from app.threads.video_creator import VideoCreatorThread
        self.render_thread = VideoCreatorThread(
            video_asset.path, job.output_path, config, subs, output_dir,
            self._collect_overlay_data())
        self.render_thread.progress.connect(self._on_video_progress)
        self.render_thread.status.connect(self._on_video_status)
        self.render_thread.error.connect(self._on_video_error)
        self.render_thread.finished_video.connect(self._on_video_finished)
        self.render_thread.start()
        self.render_section.set_exporting_status(True, "Resume render…")

    def _maybe_resume_pending_jobs(self):
        try:
            pending = self.render_queue.pending()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read render queue: %s", exc)
            return
        if not pending:
            return
        # Mark anything that was RUNNING as PENDING so the queue UI
        # shows them correctly (the worker is dead by now).
        for job in pending:
            status = job.stages.get(Stage.RENDER.value,
                                     StageStatus.PENDING.value)
            if status == StageStatus.RUNNING.value:
                job.stages[Stage.RENDER.value] = StageStatus.PENDING.value
                self.render_queue.update(job)
        ans = QMessageBox.question(
            self, "Hàng đợi render",
            f"Có {len(pending)} job render chưa hoàn tất từ phiên trước.\n"
            f"Mở hàng đợi để tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self._open_render_queue()

    def _enqueue_active_render(self, video_path: str, output_path: str,
                                config: dict, subs: list,
                                srt_path: str = '') -> Job:
        """Persist the in-flight render so it survives a crash."""
        assets = [MediaAsset(kind='video', path=video_path)]
        if srt_path:
            assets.append(MediaAsset(kind='srt', path=srt_path))
        job = Job(
            name=Path(video_path).stem,
            assets=assets,
            config=dict(config),
            output_path=output_path,
            created_at=time.time(),
        )
        job.set_status(Stage.RENDER, StageStatus.RUNNING)
        try:
            self.render_queue.enqueue(job)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to enqueue render job: %s", exc)
        self._active_job = job
        return job

    def _finalise_active_job(self, status: StageStatus,
                              error: str = '') -> None:
        job = self._active_job
        if job is None:
            return
        job.set_status(Stage.RENDER, status, error=error or None)
        if status == StageStatus.DONE:
            job.progress = 100
        try:
            self.render_queue.update(job)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update queue: %s", exc)
        self._active_job = None

