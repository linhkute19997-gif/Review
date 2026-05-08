"""
Configuration Management
========================
API keys, user preferences, and translation styles persistence.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional

# Base directory (where the app is launched from)
BASE_DIR = Path(__file__).parent.parent.parent.resolve()

# Config file paths
API_CONFIG_FILE = str(BASE_DIR / 'api_config.json')
USER_PREFERENCES_FILE = str(BASE_DIR / 'user_preferences.json')
STYLES_CONFIG_FILE = str(BASE_DIR / 'styles_config.json')

# FFmpeg paths
FFMPEG_PATH = str(BASE_DIR / 'ffmpeg.exe')
FFPROBE_PATH = str(BASE_DIR / 'ffprobe.exe')

# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════

WHISPER_MODEL_OPTIONS = (
    ('tiny', 'tiny (Siêu nhanh, độ chính xác thấp)'),
    ('base', 'base (Rất nhanh, độ chính xác cơ bản)'),
    ('small', 'small (Nhanh, độ chính xác tốt)'),
    ('medium', 'medium (Vừa phải, độ chính xác cao)'),
    ('large', 'large (Chậm, độ chính xác rất cao)'),
)
WHISPER_MODEL_IDS = {k: v for k, v in WHISPER_MODEL_OPTIONS}

TRANSLATION_MODELS = (
    {
        'name': 'Google (miễn phí)',
        'needs_api': False,
    },
    {
        'name': 'Gemini',
        'needs_api': True,
        'default_model': 'gemma-3-27b-it',
    },
    {
        'name': 'Baidu',
        'needs_api': True,
        'key_format': 'appid|secretkey',
    },
    {
        'name': 'ChatGPT',
        'needs_api': True,
        'default_model': 'gpt-3.5-turbo',
    },
)

LANGUAGES = (
    ('Tiếng Trung', 'zh-cn'),
    ('Tiếng Việt', 'vi'),
    ('Tiếng Anh', 'en'),
    ('Tiếng Nhật', 'ja'),
    ('Tiếng Hàn', 'ko'),
    ('Tiếng Thái', 'th'),
    ('Tiếng Indonesia', 'id'),
    ('Tiếng Mã Lai', 'ms'),
    ('Tiếng Philippines', 'tl'),
    ('Tiếng Ả Rập', 'ar'),
    ('Tiếng Hindi', 'hi'),
    ('Tiếng Pháp', 'fr'),
    ('Tiếng Đức', 'de'),
    ('Tiếng Tây Ban Nha', 'es'),
    ('Tiếng Bồ Đào Nha', 'pt'),
    ('Tiếng Ý', 'it'),
    ('Tiếng Nga', 'ru'),
    ('Tiếng Ba Lan', 'pl'),
    ('Tiếng Hà Lan', 'nl'),
    ('Tiếng Thụy Điển', 'sv'),
    ('Tiếng Đan Mạch', 'da'),
    ('Tiếng Na Uy', 'no'),
    ('Tiếng Phần Lan', 'fi'),
    ('Tiếng Thổ Nhĩ Kỳ', 'tr'),
    ('Tiếng Hy Lạp', 'el'),
    ('Tiếng Hebrew', 'he'),
    ('Tiếng Séc', 'cs'),
    ('Tiếng Romania', 'ro'),
    ('Tiếng Hungary', 'hu'),
    ('Tiếng Ukraine', 'uk'),
    ('Tiếng Bengal', 'bn'),
    ('Tiếng Tamil', 'ta'),
    ('Tiếng Telugu', 'te'),
    ('Tiếng Urdu', 'ur'),
    ('Tiếng Swahili', 'sw'),
    ('Tiếng Myanmar', 'my'),
    ('Khmer', 'km'),
    ('Tiếng Lào', 'lo'),
)

VOICE_CONFIGS_EDGE_VI = (
    {'label': 'Alex (Nam chuẩn)', 'voice': 'vi-VN-NamMinhNeural', 'pitch': '+0Hz', 'rate': '+50%'},
    {'label': 'Trẻ em (Giọng cao)', 'voice': 'vi-VN-NamMinhNeural', 'pitch': '+20Hz', 'rate': '+50%'},
    {'label': 'Nam trầm (Giọng dày)', 'voice': 'vi-VN-NamMinhNeural', 'pitch': '-15Hz', 'rate': '+50%'},
    {'label': 'Nữ ấm (Truyền cảm)', 'voice': 'vi-VN-HoaiMyNeural', 'pitch': '-5Hz', 'rate': '+50%'},
    {'label': 'Nữ trẻ (Năng động)', 'voice': 'vi-VN-HoaiMyNeural', 'pitch': '+10Hz', 'rate': '+50%'},
    {'label': 'Maria (Nữ chuẩn)', 'voice': 'vi-VN-HoaiMyNeural', 'pitch': '+0Hz', 'rate': '+50%'},
)

DEFAULT_STYLES = (
    'Người kể chuyện tự nhiên',
    'Chuẩn phụ đề phim gốc',
    'Hài hước nhẹ nhàng',
    'Hài hước cực mạnh',
    'Kinh dị và căng thẳng',
    'Lãng mạn',
    'Phim hành động',
    'Phim cổ trang',
    'Dành cho thiếu nhi',
    'Võ hiệp và khí chất giang hồ',
    'Gameshow giải trí',
    'Giáo viên giảng bài',
    'Bác sĩ tư vấn rõ ràng',
    'Tư vấn kinh doanh',
    'Chăm sóc khách hàng',
)

# UI translations (Vietnamese / English)
UI_TRANSLATIONS = {
    'vi': {
        'ready': 'Sẵn Sàng Dịch',
        'config': '⚙️ Cấu Hình',
        'progress': 'Tiến trình:',
        'system': 'Hệ Thống',
        'translate_tab': 'Dịch Phụ Đề',
        'voiceover_tab': 'Lồng Tiếng',
        'merge_tab': 'Ghép Video',
        'create_tab': 'Tạo Video',
        'voiceover_hint': 'ℹ️ Phụ đề để lồng tiếng sẽ lấy từ tab chỉnh sửa phụ đề.',
        'menu_system': '⚙️ Hệ Thống',
        'menu_system_info': 'Thông tin cấu hình',
        'menu_clear_cache': 'Xóa Cache cấu hình',
        'menu_test_config': 'Test cấu hình',
        'menu_language': 'Ngôn Ngữ',
        'menu_tools': 'Công Cụ',
        'menu_update': 'Cập Nhập',
        'menu_guide': 'Hướng Dẫn Sử Dụng',
        'menu_config': 'Cấu Hình',
        'lang_vi': 'Tiếng Việt',
        'lang_en': 'Tiếng Anh',
        'video_input': 'Video Gốc:',
        'srt_input': 'Tệp Phụ Đề (*.srt):',
        'btn_extract': '✂ TÁCH PHỤ ĐỀ',
        'btn_start_translate': '▶ Tiến Hành Dịch Phụ Đề',
        'btn_start_video': '▶ BẮT ĐẦU TẠO VIDEO',
        'translate_from': 'Dịch phụ đề từ:',
        'model_label': 'Mô Hình Dịch:',
        'style_label': 'Phong Cách Dịch:',
    },
    'en': {
        'ready': 'Ready to Translate',
        'config': '⚙️ Configuration',
        'progress': 'Progress:',
        'system': 'System',
        'translate_tab': 'Subtitle Translation',
        'voiceover_tab': 'Voice-Over',
        'merge_tab': 'Merge Video',
        'create_tab': 'Create Video',
        'voiceover_hint': 'ℹ️ Subtitles for voice-over will be taken from the subtitle edit tab.',
        'menu_system': '⚙️ System',
        'menu_system_info': 'Configuration Info',
        'menu_clear_cache': 'Clear Configuration Cache',
        'menu_test_config': 'Test Configuration',
        'menu_language': 'Language',
        'menu_tools': 'Tools',
        'menu_update': 'Update',
        'menu_guide': 'User Guide',
        'menu_config': 'Configuration',
        'lang_vi': 'Vietnamese',
        'lang_en': 'English',
        'video_input': 'Source Video:',
        'srt_input': 'Subtitle File (*.srt):',
        'btn_extract': '✂ EXTRACT SUBTITLES',
        'btn_start_translate': '▶ Start Translating Subtitles',
        'btn_start_video': '▶ START CREATING VIDEO',
        'translate_from': 'Translate subtitles from:',
        'model_label': 'Translation Model:',
        'style_label': 'Translation Style:',
    }
}


# ═══════════════════════════════════════════════════════════
# Config I/O
# ═══════════════════════════════════════════════════════════

def load_api_config() -> Dict:
    """Load API configuration from file."""
    try:
        if os.path.exists(API_CONFIG_FILE):
            with open(API_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_api_config(config: Dict):
    """Save API configuration to file."""
    try:
        with open(API_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] save_api_config: {e}")


def load_user_preferences() -> Dict:
    """Load user preferences from file."""
    try:
        if os.path.exists(USER_PREFERENCES_FILE):
            with open(USER_PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_user_preferences(prefs: Dict):
    """Save user preferences to file."""
    try:
        with open(USER_PREFERENCES_FILE, 'w', encoding='utf-8') as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] save_user_preferences: {e}")


def load_styles_config() -> List[str]:
    """Load translation styles from file."""
    try:
        if os.path.exists(STYLES_CONFIG_FILE):
            with open(STYLES_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return list(DEFAULT_STYLES)


def save_styles_config(styles: List[str]):
    """Save translation styles to file."""
    try:
        with open(STYLES_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(styles, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] save_styles_config: {e}")
