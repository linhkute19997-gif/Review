"""
Translation Engine — 4 backends: Google, Gemini, Baidu, ChatGPT
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt6.QtCore import QThread, pyqtSignal

from app.utils.logger import get_logger

logger = get_logger('translate')

# How many subtitle lines we batch into a single LLM prompt for the
# Gemini / ChatGPT backends. 20 keeps prompts well under the 8k input
# token budget of cheap models while paying the system-prompt cost
# only once per batch — typically 5–10x throughput vs per-line calls.
LLM_BATCH_SIZE = 20

# ── Network policy ────────────────────────────────────────────
# Tuple is ``(connect_timeout, read_timeout)``. We keep connect tight
# (5 s) so a dead host fails fast, and the read budget at 30 s so the
# longest LLM responses still complete on a slow uplink.
HTTP_TIMEOUT = (5, 30)

# Max retries for transient HTTP failures (5xx, ConnectionError, ReadTimeout).
HTTP_MAX_RETRIES = 3


# ── Token bucket rate limiter ─────────────────────────────────
# Google's free Translate endpoint will start returning 429 once you
# push past ~10 requests/sec sustained. We share a single token bucket
# across *all* worker threads in a TranslateThread so the 4-thread
# default cannot overshoot.
class _TokenBucket:
    """Thread-safe token bucket."""

    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(rate_per_sec, 1.0))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1.0) -> None:
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait = (cost - self._tokens) / self.rate
            time.sleep(min(wait, 1.0))


# Per-process rate limit for the public Google endpoint. Picked
# conservatively below the 10 RPS soft cap.
_GOOGLE_BUCKET = _TokenBucket(rate_per_sec=8.0, capacity=8.0)


def _post_with_retry(url, *, params=None, json=None, headers=None,
                     max_retries=HTTP_MAX_RETRIES):
    """POST with bounded retries on transient errors. Returns the parsed JSON.

    A non-JSON or non-2xx response with a ``5xx`` code is retried up to
    ``max_retries`` times with exponential backoff (0.5, 1.0, 2.0 s).
    Any other error bubbles up to the caller.
    """
    import requests

    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url, params=params, json=json, headers=headers,
                timeout=HTTP_TIMEOUT)
            if resp.status_code >= 500:
                logger.warning(
                    "%s returned %s (attempt %d)", url, resp.status_code, attempt + 1)
                last_exc = RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    return resp.json()
                except ValueError as exc:
                    last_exc = exc
                    logger.warning("%s returned non-JSON: %s", url, exc)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            logger.warning(
                "%s transient failure (attempt %d): %s", url, attempt + 1, exc)
        time.sleep(0.5 * (2 ** attempt))
    raise last_exc if last_exc else RuntimeError(f"POST {url} failed")


# Language name lookup — module-level so the batch helpers can reuse it.
_LANG_NAMES = {
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


def _build_batch_prompt(items, dest_name: str) -> str:
    """Render a JSON-mode prompt for a batch of subtitle lines."""
    body = json.dumps(
        [{'i': i, 't': t} for i, t in items],
        ensure_ascii=False)
    return (
        f"Dịch sang {dest_name}. Giữ nguyên thứ tự.\n"
        "Trả về JSON object duy nhất theo schema "
        '{"r":[{"i":<int>,"t":"<bản dịch>"},...]} '
        "— không kèm chú thích, không markdown.\n"
        f"Input:\n{body}\n"
    )


def _parse_batch_json(raw: str) -> dict:
    """Extract the ``{"r":[...]}`` payload from an LLM response.

    LLMs occasionally wrap JSON in markdown fences or add prose before
    the object — we look for the first balanced ``{...}`` and parse
    that. Raises ``ValueError`` on anything we can't read.
    """
    if not isinstance(raw, str):
        raise ValueError("response is not text")
    text = raw.strip()
    # Strip ```json ... ``` fences if present.
    text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)
    # Find the first balanced JSON object in the response.
    start = text.find('{')
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    for end in range(start, len(text)):
        ch = text[end]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                payload = text[start:end + 1]
                return json.loads(payload)
    raise ValueError("unterminated JSON object")


def _translate_batch_gemini(items, dest_name, api_keys, key_index):
    """Batch-translate via Gemini JSON-mode. Yields (index, text) pairs."""
    import google.generativeai as genai
    if not api_keys:
        for i, _ in items:
            yield i, '[Lỗi: Không còn API key khả dụng]'
        return

    keys = api_keys if isinstance(api_keys, list) else [api_keys]
    prompt = _build_batch_prompt(items, dest_name)
    last_err = None
    for offset in range(len(keys)):
        idx = (key_index + offset) % len(keys)
        try:
            genai.configure(api_key=keys[idx])
            gen_model = genai.GenerativeModel(
                'gemma-3-27b-it',
                generation_config={'response_mime_type': 'application/json'})
            response = gen_model.generate_content(prompt)
            data = _parse_batch_json(response.text)
            results = {int(r['i']): str(r.get('t', ''))
                       for r in data.get('r', [])}
            missing = []
            for i, src in items:
                if i in results and results[i].strip():
                    yield i, results[i].strip()
                else:
                    missing.append((i, src))
            # Retry the missing ones one-by-one — this is the
            # ``resilient`` clause from the design doc.
            for i, src in missing:
                args = (i, src, '', dest_name_to_code(dest_name),
                        'Gemini', keys, idx)
                yield translate_single(args)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            msg = str(exc).lower()
            if '429' in msg or 'quota' in msg or 'exceeded' in msg:
                logger.debug("Gemini batch key %s blocked: %s", idx, exc)
                continue
            logger.warning("Gemini batch failed on key %s: %s", idx, exc)
            break
    # All keys exhausted or hard failure → return error markers.
    for i, _ in items:
        yield i, f'[Lỗi Gemini batch: {last_err}]'


def _translate_batch_chatgpt(items, dest_name, api_key):
    """Batch-translate via OpenAI ChatGPT JSON-mode."""
    if not api_key:
        for i, _ in items:
            yield i, '[Lỗi: Không có API key]'
        return

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    user_prompt = _build_batch_prompt(items, dest_name)
    data = {
        'model': 'gpt-3.5-turbo-1106',  # Supports response_format=json_object
        'messages': [
            {'role': 'system',
             'content': f'Bạn là dịch giả chuyên nghiệp. Dịch sang {dest_name}. '
                        f'Trả lời bằng JSON object duy nhất.'},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0.3,
        'response_format': {'type': 'json_object'},
    }
    try:
        result = _post_with_retry(
            'https://api.openai.com/v1/chat/completions',
            headers=headers, json=data)
    except Exception as exc:
        for i, _ in items:
            yield i, f'[Lỗi ChatGPT batch mạng: {exc}]'
        return

    if not isinstance(result, dict) or 'error' in result:
        err = result.get('error', {}).get('message', 'unknown') if isinstance(result, dict) else 'unknown'
        for i, _ in items:
            yield i, f'[Lỗi ChatGPT batch: {err}]'
        return
    try:
        content = result['choices'][0]['message']['content']
        parsed = _parse_batch_json(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ChatGPT batch parse failed: %s", exc)
        for i, src in items:
            # Fall back to per-line retry rather than silently dropping.
            yield translate_single(
                (i, src, '', dest_name_to_code(dest_name), 'ChatGPT', api_key, 0))
        return

    results = {int(r['i']): str(r.get('t', '')) for r in parsed.get('r', [])}
    for i, src in items:
        if i in results and results[i].strip():
            yield i, results[i].strip()
        else:
            # Per-line retry for the gaps.
            yield translate_single(
                (i, src, '', dest_name_to_code(dest_name), 'ChatGPT', api_key, 0))


def dest_name_to_code(name: str) -> str:
    """Reverse lookup ``_LANG_NAMES`` so per-line fallback can re-target."""
    for code, label in _LANG_NAMES.items():
        if label == name:
            return code
    return 'vi'


def translate_single(args):
    """Translate a single subtitle — for use with ThreadPoolExecutor."""
    index, text, src_lang, dest_lang, model, api_keys, key_index = args

    if not isinstance(text, str) or not text.strip():
        return index, text

    dest_name = _LANG_NAMES.get(dest_lang, dest_lang)

    try:
        if model == 'Google (miễn phí)':
            from deep_translator import GoogleTranslator
            lang_map = {'zh-cn': 'zh-CN', 'vi': 'vi', 'en': 'en', 'ja': 'ja',
                        'ko': 'ko', 'fr': 'fr', 'de': 'de', 'th': 'th',
                        'es': 'es', 'pt': 'pt', 'id': 'id'}
            final_dest = lang_map.get(dest_lang, dest_lang)
            final_src = lang_map.get(src_lang, 'auto')
            _GOOGLE_BUCKET.acquire()
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
                        logger.debug("API key %s blocked (429): %s", idx, e)
                        continue
                    return index, f'[Lỗi Gemini: {e}]'
            return index, '[Lỗi: Tất cả API key Gemini đều bị block]'

        elif model == 'Baidu':
            import hashlib
            import random
            if not api_keys or '|' not in str(api_keys):
                return index, '[Lỗi: API key Baidu cần format: appid|secretkey]'
            parts = str(api_keys).split('|')
            appid, secret_key = parts[0], parts[1]
            salt = str(random.randint(32768, 65536))
            sign = hashlib.md5(
                (appid + text + salt + secret_key).encode()).hexdigest()
            final_src = 'auto' if src_lang == 'auto' else src_lang.replace('-', '')
            final_dest = 'vie' if dest_lang == 'vi' else dest_lang.replace('-', '')
            params = {'q': text, 'from': final_src, 'to': final_dest,
                      'appid': appid, 'salt': salt, 'sign': sign}
            try:
                result = _post_with_retry(
                    'https://api.fanyi.baidu.com/api/trans/vip/translate',
                    params=params)
            except Exception as exc:
                return index, f'[Lỗi Baidu mạng: {exc}]'
            if not isinstance(result, dict):
                return index, '[Lỗi Baidu: phản hồi không hợp lệ]'
            if 'error_code' in result:
                return index, f"[Lỗi Baidu: {result.get('error_msg', 'Unknown')}]"
            return index, result.get('trans_result', [{}])[0].get('dst', text)

        elif model == 'ChatGPT':
            if not api_keys:
                return index, '[Lỗi: Không có API key]'
            headers = {
                'Authorization': f'Bearer {api_keys}',
                'Content-Type': 'application/json',
            }
            data = {
                'model': 'gpt-3.5-turbo',
                'messages': [
                    {'role': 'system', 'content': f'Bạn là dịch giả. Dịch sang {dest_name}. Chỉ trả về bản dịch.'},
                    {'role': 'user', 'content': text},
                ],
                'temperature': 0.3,
            }
            try:
                result = _post_with_retry(
                    'https://api.openai.com/v1/chat/completions',
                    headers=headers, json=data)
            except Exception as exc:
                return index, f'[Lỗi ChatGPT mạng: {exc}]'
            if not isinstance(result, dict):
                return index, '[Lỗi ChatGPT: phản hồi không hợp lệ]'
            if 'error' in result:
                return index, f"[Lỗi ChatGPT: {result['error'].get('message', 'Unknown')}]"
            try:
                return index, result['choices'][0]['message']['content'].strip()
            except (KeyError, IndexError, TypeError):
                return index, '[Lỗi ChatGPT: phản hồi không có choices]'

        else:
            return index, f'[Mô hình không hỗ trợ: {model}]'

    except Exception as e:
        logger.exception("translate_single failed for index=%s: %s", index, e)
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
        # Gemini and ChatGPT support a JSON-mode batch path: one prompt
        # per ``LLM_BATCH_SIZE`` lines instead of one network round-trip
        # per line. Other backends (Google free, Baidu) stay on the
        # single-line ThreadPoolExecutor path.
        if self.model in ('Gemini', 'ChatGPT'):
            self._run_batch_llm()
        else:
            self._run_per_line()
        self.finished_signal.emit()

    def _run_per_line(self):
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

    def _run_batch_llm(self):
        """Batch-translate via Gemini / ChatGPT JSON mode."""
        dest_name = _LANG_NAMES.get(self.dest_lang, self.dest_lang)
        # Prepare items, skipping empty lines.
        all_items: list[tuple[int, str]] = []
        for i, sub in enumerate(self.subtitles):
            text = sub.get('text', '')
            if isinstance(text, str) and text.strip():
                all_items.append((i, text))
            else:
                # Pass through verbatim; nothing to translate.
                self.progress.emit(i, text or '')

        # Process in batches. Run batches in parallel via ThreadPoolExecutor
        # so a slow LLM response doesn't block the next batch — bounded by
        # max_workers to avoid rate-limit storms.
        batches = [all_items[i:i + LLM_BATCH_SIZE]
                   for i in range(0, len(all_items), LLM_BATCH_SIZE)]
        if not batches:
            return

        # Pre-compute key rotation per batch. Looking the index up via
        # ``batches.index(batch)`` is O(n²) and — worse — returns the
        # first match when two batches contain identical content, which
        # collapses key rotation onto the same key.
        key_count = max(len(self.api_keys), 1)

        def _run_one(batch_index: int, batch):
            if not self._running:
                return []
            if self.model == 'Gemini':
                key_index = batch_index % key_count if self.api_keys else 0
                return list(_translate_batch_gemini(
                    batch, dest_name, self.api_keys, key_index))
            api_key = self.api_keys[0] if self.api_keys else ''
            return list(_translate_batch_chatgpt(batch, dest_name, api_key))

        with ThreadPoolExecutor(
                max_workers=max(1, min(self.max_workers, 4))) as executor:
            futures = {
                executor.submit(_run_one, idx, b): b
                for idx, b in enumerate(batches)
            }
            for future in as_completed(futures):
                if not self._running:
                    break
                try:
                    for index, result in future.result():
                        self.progress.emit(index, result)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("LLM batch failed: %s", exc)
                    for i, _src in futures[future]:
                        self.progress.emit(i, f'[Lỗi batch: {exc}]')

    def stop(self):
        self._running = False
