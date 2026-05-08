"""
Translation Engine — 4 backends: Google, Gemini, Baidu, ChatGPT
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt6.QtCore import QThread, pyqtSignal
from typing import List, Dict


def translate_single(args):
    """Translate a single subtitle — for use with ThreadPoolExecutor."""
    index, text, src_lang, dest_lang, model, api_keys, key_index = args

    if not isinstance(text, str) or not text.strip():
        return index, text

    # Language name lookup
    LANG_NAMES = {
        'vi': 'tiếng Việt', 'en': 'English', 'zh-cn': 'tiếng Trung',
        'ja': 'tiếng Nhật', 'ko': 'tiếng Hàn', 'th': 'tiếng Thái',
        'fr': 'tiếng Pháp', 'de': 'tiếng Đức', 'es': 'tiếng Tây Ban Nha',
        'pt': 'tiếng Bồ Đào Nha', 'id': 'tiếng Indonesia', 'ru': 'tiếng Nga',
        'it': 'tiếng Ý', 'ar': 'tiếng Ả Rập', 'hi': 'tiếng Hindi',
        'tr': 'tiếng Thổ Nhĩ Kỳ', 'pl': 'tiếng Ba Lan', 'nl': 'tiếng Hà Lan',
        'ms': 'tiếng Mã Lai', 'tl': 'tiếng Philippines', 'sv': 'tiếng Thụy Điển',
        'da': 'tiếng Đan Mạch', 'no': 'tiếng Na Uy', 'fi': 'tiếng Phần Lan',
        'el': 'tiếng Hy Lạp', 'he': 'tiếng Hebrew', 'cs': 'tiếng Séc',
        'ro': 'tiếng Romania', 'hu': 'tiếng Hungary', 'uk': 'tiếng Ukraine',
        'bn': 'tiếng Bengal', 'ta': 'tiếng Tamil', 'te': 'tiếng Telugu',
        'ur': 'tiếng Urdu', 'sw': 'tiếng Swahili', 'my': 'tiếng Myanmar',
        'km': 'Khmer', 'lo': 'tiếng Lào',
    }
    dest_name = LANG_NAMES.get(dest_lang, dest_lang)

    try:
        if model == 'Google (miễn phí)':
            from deep_translator import GoogleTranslator
            lang_map = {'zh-cn': 'zh-CN', 'vi': 'vi', 'en': 'en', 'ja': 'ja',
                        'ko': 'ko', 'fr': 'fr', 'de': 'de', 'th': 'th',
                        'es': 'es', 'pt': 'pt', 'id': 'id'}
            final_dest = lang_map.get(dest_lang, dest_lang)
            final_src = lang_map.get(src_lang, 'auto')
            translator = GoogleTranslator(source=final_src, target=final_dest)
            translated_text = translator.translate(text)
            return index, translated_text

        elif model == 'Gemini':
            import google.generativeai as genai
            if not api_keys:
                return index, '[Lỗi: Không còn API key khả dụng]'
            keys = api_keys if isinstance(api_keys, list) else [api_keys]
            for i in range(len(keys)):
                idx = (key_index + i) % len(keys)
                current_key = keys[idx]
                try:
                    genai.configure(api_key=current_key)
                    gen_model = genai.GenerativeModel('gemma-3-27b-it')
                    prompt = f"Dịch câu sau sang {dest_name}. Chỉ trả về bản dịch duy nhất, không giải thích:\n{text}"
                    response = gen_model.generate_content(prompt)
                    return index, response.text.strip()
                except Exception as e:
                    error_msg = str(e).lower()
                    if '429' in error_msg or 'quota' in error_msg or 'exceeded' in error_msg:
                        print(f"[DEBUG] API key {idx} bị block (429): {e}")
                        continue
                    return index, f'[Lỗi Gemini: {e}]'
            return index, '[Lỗi: Tất cả API key Gemini đều bị block]'

        elif model == 'Baidu':
            import requests, hashlib, random
            if not api_keys or '|' not in str(api_keys):
                return index, '[Lỗi: API key Baidu cần format: appid|secretkey]'
            parts = str(api_keys).split('|')
            appid, secret_key = parts[0], parts[1]
            salt = str(random.randint(32768, 65536))
            sign = hashlib.md5((appid + text + salt + secret_key).encode()).hexdigest()
            final_src = 'auto' if src_lang == 'auto' else src_lang.replace('-', '')
            final_dest = 'vie' if dest_lang == 'vi' else dest_lang.replace('-', '')
            params = {'q': text, 'from': final_src, 'to': final_dest,
                      'appid': appid, 'salt': salt, 'sign': sign}
            result = requests.post('https://api.fanyi.baidu.com/api/trans/vip/translate',
                                   params=params).json()
            if 'error_code' in result:
                return index, f"[Lỗi Baidu: {result.get('error_msg', 'Unknown')}]"
            return index, result.get('trans_result', [{}])[0].get('dst', text)

        elif model == 'ChatGPT':
            import requests
            if not api_keys:
                return index, '[Lỗi: Không có API key]'
            headers = {'Authorization': f'Bearer {api_keys}', 'Content-Type': 'application/json'}
            data = {
                'model': 'gpt-3.5-turbo',
                'messages': [
                    {'role': 'system', 'content': f'Bạn là dịch giả. Dịch sang {dest_name}. Chỉ trả về bản dịch.'},
                    {'role': 'user', 'content': text}
                ],
                'temperature': 0.3
            }
            result = requests.post('https://api.openai.com/v1/chat/completions',
                                   headers=headers, json=data).json()
            if 'error' in result:
                return index, f"[Lỗi ChatGPT: {result['error'].get('message', 'Unknown')}]"
            return index, result['choices'][0]['message']['content'].strip()

        else:
            return index, f'[Mô hình không hỗ trợ: {model}]'

    except Exception as e:
        return index, f'[Lỗi: {e}]'


class TranslateThread(QThread):
    progress = pyqtSignal(int, str)
    finished_signal = pyqtSignal()

    def __init__(self, subtitles, max_workers, src_lang, dest_lang, model, api_key):
        super().__init__()
        self.subtitles = subtitles
        self.max_workers = max_workers
        self.src_lang = src_lang
        self.dest_lang = dest_lang
        self.model = model
        self.api_keys = api_key if isinstance(api_key, list) else [api_key] if api_key else []
        self._running = True

    def run(self):
        tasks = []
        for i, sub in enumerate(self.subtitles):
            tasks.append((i, sub.get('text', ''), self.src_lang, self.dest_lang,
                          self.model, self.api_keys, i % max(len(self.api_keys), 1)))

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(translate_single, t): t for t in tasks}
            for future in as_completed(futures):
                if not self._running:
                    break
                try:
                    index, result = future.result()
                    self.progress.emit(index, result)
                except Exception as e:
                    task = futures[future]
                    self.progress.emit(task[0], f'[Lỗi: {e}]')

        self.finished_signal.emit()

    def stop(self):
        self._running = False
