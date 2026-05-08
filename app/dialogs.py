"""
Dialogs — API Config, Output Folder, Style Manager, ElevenLabs, Douyin Download
"""
import os
import subprocess
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTextEdit, QListWidget, QMessageBox,
    QFileDialog, QProgressBar
)
from PyQt6.QtCore import QThread, pyqtSignal
from app.utils.config import (
    TRANSLATION_MODELS, DEFAULT_STYLES,
    load_api_config, save_api_config,
    load_styles_config, save_styles_config
)


class APIConfigDialog(QDialog):
    """Configure API keys for translation models."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Cấu Hình API")
        self.resize(500, 350)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Model selector
        row = QHBoxLayout()
        row.addWidget(QLabel("Mô hình:"))
        self.combo_model = QComboBox()
        for m in TRANSLATION_MODELS:
            self.combo_model.addItem(m['name'])
        self.combo_model.currentIndexChanged.connect(self._on_model_changed)
        row.addWidget(self.combo_model)
        layout.addLayout(row)

        # API key input
        layout.addWidget(QLabel("API Key(s):"))
        self.api_input = QTextEdit()
        self.api_input.setPlaceholderText("Nhập API key(s), mỗi key 1 dòng...")
        self.api_input.setMaximumHeight(120)
        layout.addWidget(self.api_input)

        # Hint
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.hint_label)

        # Save
        btn_save = QPushButton("💾 Lưu Cấu Hình")
        btn_save.setObjectName("btnBatDau")
        btn_save.clicked.connect(self._save_config)
        layout.addWidget(btn_save)

        # Load existing
        self._on_model_changed(0)

    def _on_model_changed(self, index):
        model = self.combo_model.currentText()
        config = load_api_config()
        keys = config.get(model, {}).get('api_key', '')
        if isinstance(keys, list):
            self.api_input.setPlainText('\n'.join(keys))
        else:
            self.api_input.setPlainText(str(keys))

        # Set hint
        for m in TRANSLATION_MODELS:
            if m['name'] == model:
                if not m.get('needs_api'):
                    self.hint_label.setText("✅ Mô hình miễn phí, không cần API key")
                elif 'key_format' in m:
                    self.hint_label.setText(f"Format: {m['key_format']}")
                else:
                    self.hint_label.setText("Mỗi key 1 dòng. Hỗ trợ multi-key rotation.")

    def _save_config(self):
        model = self.combo_model.currentText()
        text = self.api_input.toPlainText().strip()
        keys = [k.strip() for k in text.splitlines() if k.strip()]

        config = load_api_config()
        config[model] = {'api_key': keys if len(keys) > 1 else (keys[0] if keys else '')}
        save_api_config(config)
        QMessageBox.information(self, "Thành Công", f"Đã lưu API key cho {model}")
        self.accept()


class StyleManagerDialog(QDialog):
    """Manage translation styles (15 presets + custom)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📝 Quản Lý Phong Cách Dịch")
        self.resize(400, 400)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        # Add new
        row = QHBoxLayout()
        self.new_input = QLineEdit()
        self.new_input.setPlaceholderText("Nhập tên phong cách mới...")
        row.addWidget(self.new_input)
        btn_add = QPushButton("➕ Thêm")
        btn_add.clicked.connect(self._add_style)
        row.addWidget(btn_add)
        layout.addLayout(row)

        # Buttons
        row2 = QHBoxLayout()
        btn_del = QPushButton("🗑 Xóa")
        btn_del.clicked.connect(self._delete_selected)
        row2.addWidget(btn_del)
        btn_reset = QPushButton("🔄 Reset Mặc Định")
        btn_reset.clicked.connect(self._reset_default)
        row2.addWidget(btn_reset)
        btn_save = QPushButton("💾 Lưu")
        btn_save.setObjectName("btnBatDau")
        btn_save.clicked.connect(self._save_and_close)
        row2.addWidget(btn_save)
        layout.addLayout(row2)

    def _refresh_list(self):
        self.list_widget.clear()
        styles = load_styles_config()
        for s in styles:
            self.list_widget.addItem(s)

    def _add_style(self):
        name = self.new_input.text().strip()
        if name:
            self.list_widget.addItem(name)
            self.new_input.clear()

    def _delete_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def _reset_default(self):
        self.list_widget.clear()
        for s in DEFAULT_STYLES:
            self.list_widget.addItem(s)

    def _save_and_close(self):
        styles = [self.list_widget.item(i).text()
                  for i in range(self.list_widget.count())]
        save_styles_config(styles)
        self.accept()

    def get_styles(self):
        return [self.list_widget.item(i).text()
                for i in range(self.list_widget.count())]


class OutputFolderDialog(QDialog):
    """Set output save directory."""

    def __init__(self, current_dir='', parent=None):
        super().__init__(parent)
        self.setWindowTitle("📁 Thư Mục Lưu")
        self.resize(450, 120)
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.path_input = QLineEdit(current_dir)
        row.addWidget(self.path_input)
        btn = QPushButton("📂")
        btn.clicked.connect(self._browse)
        row.addWidget(btn)
        btn_open = QPushButton("📂 Mở")
        btn_open.clicked.connect(self._open_folder)
        row.addWidget(btn_open)
        layout.addLayout(row)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu")
        if d:
            self.path_input.setText(d)

    def _open_folder(self):
        path = self.path_input.text()
        if not (path and os.path.isdir(path)):
            return
        try:
            if sys.platform.startswith('win'):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as exc:
            QMessageBox.warning(self, "Lỗi", f"Không thể mở thư mục: {exc}")


class DouyinDownloadThread(QThread):
    """Download Douyin/TikTok videos using yt-dlp.

    yt-dlp tracks the official Douyin/TikTok extractors so URLs work
    out-of-the-box without scraping a third-party HTML page.
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal()

    # Hosts allowed for download — guards against SSRF / arbitrary URLs.
    ALLOWED_HOSTS = (
        'douyin.com', 'iesdouyin.com', 'v.douyin.com',
        'tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com',
        'snssdk.com',
    )

    def __init__(self, links, save_dir):
        super().__init__()
        self.links = links
        self.save_dir = str(save_dir)
        self._cancelled = False

    def stop(self):
        self._cancelled = True

    def _is_allowed(self, link):
        try:
            from urllib.parse import urlparse
            host = (urlparse(link).hostname or '').lower()
        except Exception:
            return False
        return any(host == h or host.endswith('.' + h)
                   for h in self.ALLOWED_HOSTS)

    def _ensure_ytdlp(self):
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            self.progress.emit("Đang cài đặt yt-dlp...")
            try:
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', 'yt-dlp'],
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    check=True)
                import yt_dlp  # noqa: F401
                return True
            except Exception as exc:
                self.progress.emit(f"❌ Không cài được yt-dlp: {exc}")
                return False

    def run(self):
        if not self._ensure_ytdlp():
            self.finished.emit()
            return

        import yt_dlp
        os.makedirs(self.save_dir, exist_ok=True)

        for i, link in enumerate(self.links):
            if self._cancelled:
                break
            link = link.strip()
            if not link:
                continue
            if not self._is_allowed(link):
                self.progress.emit(
                    f"❌ URL không thuộc Douyin/TikTok: {link}")
                continue

            self.progress.emit(f"Đang tải ({i+1}/{len(self.links)}): {link}")
            outtmpl = os.path.join(
                self.save_dir, f"douyin_{i+1:03d}.%(ext)s")
            ydl_opts = {
                'outtmpl': outtmpl,
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'merge_output_format': 'mp4',
                'socket_timeout': 30,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([link])
                self.progress.emit(f"✅ Đã tải video #{i+1}")
            except Exception as exc:
                self.progress.emit(f"❌ Lỗi tải #{i+1}: {exc}")

        self.finished.emit()


class DouyinDownloadDialog(QDialog):
    """Paste Douyin/TikTok links → batch download."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tải Video Douyin")
        self.resize(500, 350)
        self.save_dir = str(os.path.join(os.path.expanduser('~'), 'Videos'))

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Nhập link video, mỗi video 1 dòng\nVí dụ: https://v.douyin.com/xxx")
        layout.addWidget(self.text_edit)

        # Save directory selector
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("📂 Thư mục lưu:"))
        self.dir_input = QLineEdit(self.save_dir)
        dir_row.addWidget(self.dir_input)
        btn_browse = QPushButton("📂")
        btn_browse.setFixedWidth(36)
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(btn_browse)
        layout.addLayout(dir_row)

        self.btn_download = QPushButton("Download")
        self.btn_download.clicked.connect(self.start_download)
        layout.addWidget(self.btn_download)

        self.progress_label = QLabel("Sẵn sàng")
        self.progress_label.setStyleSheet("color: #888;")
        layout.addWidget(self.progress_label)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", self.save_dir)
        if d:
            self.save_dir = d
            self.dir_input.setText(d)

    def start_download(self):
        text = self.text_edit.toPlainText().strip()
        links = [u.strip() for u in text.splitlines() if u.strip()]
        if not links:
            return
        self.save_dir = self.dir_input.text() or self.save_dir
        os.makedirs(self.save_dir, exist_ok=True)
        self.btn_download.setEnabled(False)
        self.progress_label.setText("Đang xử lý...")
        self.download_thread = DouyinDownloadThread(links, self.save_dir)
        self.download_thread.progress.connect(self.update_progress)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.start()

    def update_progress(self, msg):
        self.progress_label.setText(msg)

    def download_finished(self):
        self.btn_download.setEnabled(True)
        self.progress_label.setText("Hoàn thành!")
        QMessageBox.information(self, "Thành công",
                                f"Đã tải xong video và lưu vào {self.save_dir}")
