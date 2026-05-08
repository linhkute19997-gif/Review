"""
Configuration Management
========================
API keys, user preferences, and translation styles persistence.
"""

from pathlib import Path
from typing import Dict, List

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
#
# All four config files are read/written via the atomic helpers in
# ``app.utils.atomic_io`` so a crash mid-save can never produce an
# empty/corrupt JSON file. API keys are persisted through
# ``app.utils.key_vault`` instead of plaintext JSON; the JSON only
# contains opaque ``vault:<service>:<id>`` references.
# Existing plaintext ``api_config.json`` files are migrated on first
# load — see ``_migrate_api_config_inplace``.

def _normalise_api_keys(value) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int))]
    if isinstance(value, str):
        return [value] if value else []
    return []


def _migrate_api_config_inplace(config: Dict) -> bool:
    """Move any plaintext keys into the vault. Returns True when changed."""
    from app.utils import key_vault  # local import: vault lazily loads
    changed = False
    for model_name, entry in list(config.items()):
        if not isinstance(entry, dict):
            continue
        api_key = entry.get('api_key')
        keys = _normalise_api_keys(api_key)
        if not keys:
            continue
        # Already migrated → all entries are vault refs.
        if all(key_vault.is_ref(k) for k in keys):
            continue
        new_refs: List[str] = []
        for idx, key in enumerate(keys):
            if key_vault.is_ref(key):
                new_refs.append(key)
                continue
            ref = key_vault.make_ref(model_name, idx)
            try:
                key_vault.store(ref, key)
                new_refs.append(ref)
                changed = True
            except Exception:
                # Cannot store → leave plaintext to avoid losing it.
                new_refs.append(key)
        entry['api_key'] = new_refs if len(new_refs) != 1 else new_refs[0]
    return changed


def _materialise_api_keys(config: Dict) -> Dict:
    """Return a copy of ``config`` with vault refs resolved to plaintext."""
    from app.utils import key_vault
    out: Dict = {}
    for model_name, entry in config.items():
        if not isinstance(entry, dict):
            out[model_name] = entry
            continue
        copy = dict(entry)
        api_key = copy.get('api_key')
        keys = _normalise_api_keys(api_key)
        resolved = [key_vault.resolve(k) for k in keys]
        resolved = [str(k) for k in resolved if isinstance(k, (str, bytes)) and k]
        if not resolved:
            copy['api_key'] = ''
        elif len(resolved) == 1:
            copy['api_key'] = resolved[0]
        else:
            copy['api_key'] = resolved
        out[model_name] = copy
    return out


def load_api_config() -> Dict:
    """Load API configuration, migrating any plaintext keys to the vault.

    Returns a dict where ``api_key`` values are plaintext strings (or
    lists), matching the historical contract. Internally the JSON only
    stores vault references after the first save.
    """
    from app.utils.atomic_io import atomic_write_json, read_json
    from app.utils.logger import get_logger

    logger = get_logger('config')
    raw = read_json(API_CONFIG_FILE, {}) or {}
    if not isinstance(raw, dict):
        logger.warning("api_config.json malformed, ignoring")
        return {}

    if _migrate_api_config_inplace(raw):
        try:
            atomic_write_json(API_CONFIG_FILE, raw)
            logger.info("Migrated plaintext API keys into vault")
        except Exception as exc:
            logger.warning("Failed to persist migrated api_config: %s", exc)

    return _materialise_api_keys(raw)


def save_api_config(config: Dict):
    """Persist API config, storing API keys in the vault."""
    from app.utils.atomic_io import atomic_write_json, read_json
    from app.utils.logger import get_logger
    from app.utils import key_vault

    logger = get_logger('config')

    existing = read_json(API_CONFIG_FILE, {}) or {}
    if not isinstance(existing, dict):
        existing = {}

    # Build the JSON-safe view: vault refs only.
    on_disk: Dict = dict(existing)
    for model_name, entry in config.items():
        if not isinstance(entry, dict):
            on_disk[model_name] = entry
            continue
        api_key = entry.get('api_key')
        keys = _normalise_api_keys(api_key)

        # Determine new refs, reusing previous ref slots so unchanged
        # secrets stay at the same vault keys.
        prev = existing.get(model_name) or {}
        prev_refs = _normalise_api_keys(prev.get('api_key'))

        new_refs: List[str] = []
        for idx, plain in enumerate(keys):
            if key_vault.is_ref(plain):
                new_refs.append(plain)
                continue
            ref = key_vault.make_ref(model_name, idx)
            try:
                key_vault.store(ref, plain)
                new_refs.append(ref)
            except Exception as exc:
                logger.warning("Vault store failed (%s); keeping plaintext", exc)
                new_refs.append(plain)

        # Delete vault entries that no longer have a counterpart.
        for stale_ref in prev_refs[len(new_refs):]:
            if key_vault.is_ref(stale_ref):
                try:
                    key_vault.delete(stale_ref)
                except Exception:
                    pass

        new_entry = {k: v for k, v in entry.items() if k != 'api_key'}
        if not new_refs:
            new_entry['api_key'] = ''
        elif len(new_refs) == 1:
            new_entry['api_key'] = new_refs[0]
        else:
            new_entry['api_key'] = new_refs
        on_disk[model_name] = new_entry

    try:
        atomic_write_json(API_CONFIG_FILE, on_disk)
    except Exception as exc:
        logger.error("save_api_config failed: %s", exc)


def load_user_preferences() -> Dict:
    """Load user preferences from file."""
    from app.utils.atomic_io import read_json
    data = read_json(USER_PREFERENCES_FILE, {}) or {}
    return data if isinstance(data, dict) else {}


def save_user_preferences(prefs: Dict):
    """Save user preferences to file."""
    from app.utils.atomic_io import atomic_write_json
    from app.utils.logger import get_logger
    try:
        atomic_write_json(USER_PREFERENCES_FILE, prefs)
    except Exception as exc:
        get_logger('config').error("save_user_preferences failed: %s", exc)


def load_styles_config() -> List[str]:
    """Load translation styles from file."""
    from app.utils.atomic_io import read_json
    data = read_json(STYLES_CONFIG_FILE, None)
    if isinstance(data, list) and data:
        return [str(s) for s in data]
    return list(DEFAULT_STYLES)


def save_styles_config(styles: List[str]):
    """Save translation styles to file."""
    from app.utils.atomic_io import atomic_write_json
    from app.utils.logger import get_logger
    try:
        atomic_write_json(STYLES_CONFIG_FILE, list(styles))
    except Exception as exc:
        get_logger('config').error("save_styles_config failed: %s", exc)
