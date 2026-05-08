"""
Background model pre-warming
============================
Loading the Whisper transcription model or PaddleOCR for the first
time costs 30–60 seconds while CUDA initialises and the weights are
deserialised. The user typically does *not* need them in the first
few seconds after launch, so we kick off a daemon thread at startup
that imports both packages and primes the module-level caches in
:mod:`app.subtitle_extract`.

The pre-warmer is purposefully forgiving: any exception (missing
package, no GPU, broken weights, …) is logged and swallowed — the
user can still trigger the dialog manually, which will trigger the
real install / load path.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

from app.utils.logger import get_logger

logger = get_logger('prewarm')


@dataclass
class PrewarmStatus:
    """Snapshot the UI can poll to render a status bar message."""
    whisper: str = 'pending'      # pending | loading | ready | error | skipped
    ocr: str = 'pending'
    whisper_error: str = ''
    ocr_error: str = ''
    notes: List[str] = field(default_factory=list)

    def is_done(self) -> bool:
        return (self.whisper in ('ready', 'error', 'skipped')
                and self.ocr in ('ready', 'error', 'skipped'))

    def summary(self) -> str:
        parts = []
        if self.whisper == 'ready':
            parts.append('Whisper sẵn sàng')
        elif self.whisper == 'error':
            parts.append('Whisper lỗi')
        elif self.whisper == 'loading':
            parts.append('đang nạp Whisper…')
        if self.ocr == 'ready':
            parts.append('PaddleOCR sẵn sàng')
        elif self.ocr == 'error':
            parts.append('PaddleOCR lỗi')
        elif self.ocr == 'loading':
            parts.append('đang nạp OCR…')
        return ' • '.join(parts) if parts else 'Pre-warm chưa chạy'


class PrewarmService:
    """Drive the background pre-warm thread.

    The service starts at most one worker thread. ``observers`` are
    plain callables called on the worker thread whenever the status
    snapshot changes — UI code wraps that in a ``QMetaObject``
    invocation to bounce back onto the GUI thread.
    """

    def __init__(self,
                 whisper_model: str = 'base',
                 ocr_langs: Iterable[str] = ('en',),
                 device: Optional[str] = None):
        self.whisper_model = whisper_model
        self.ocr_langs = list(ocr_langs)
        self.device = device
        self.status = PrewarmStatus()
        self._observers: List[Callable[[PrewarmStatus], None]] = []
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────
    def add_observer(self, fn: Callable[[PrewarmStatus], None]) -> None:
        with self._lock:
            self._observers.append(fn)
        # Push the current status to the new observer immediately so
        # late-attached UIs catch up.
        try:
            fn(self.status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pre-warm observer raised %s — ignored", exc)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name='prewarm', daemon=True)
            self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    # ── Worker ────────────────────────────────────────────────
    def _run(self) -> None:
        device = self.device or self._detect_device()
        self._warm_whisper(device)
        self._warm_paddle_ocr()

    def _warm_whisper(self, device: str) -> None:
        self._set_status(whisper='loading')
        try:
            from app.subtitle_extract import _get_whisper_model
            _get_whisper_model(self.whisper_model, device)
        except ImportError as exc:
            logger.info("Whisper not installed — skipping pre-warm (%s)", exc)
            self._set_status(whisper='skipped',
                             whisper_error='openai-whisper chưa cài')
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Whisper pre-warm failed: %s", exc)
            self._set_status(whisper='error', whisper_error=str(exc))
            return
        self._set_status(whisper='ready')

    def _warm_paddle_ocr(self) -> None:
        self._set_status(ocr='loading')
        try:
            from app.subtitle_extract import _get_paddle_ocr
            for lang in self.ocr_langs:
                _get_paddle_ocr(lang)
        except ImportError as exc:
            logger.info("PaddleOCR not installed — skipping pre-warm (%s)",
                         exc)
            self._set_status(ocr='skipped',
                             ocr_error='paddleocr chưa cài')
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("PaddleOCR pre-warm failed: %s", exc)
            self._set_status(ocr='error', ocr_error=str(exc))
            return
        self._set_status(ocr='ready')

    def _detect_device(self) -> str:
        try:
            import torch
            if torch.cuda.is_available():
                return 'cuda'
        except Exception:  # noqa: BLE001
            pass
        return 'cpu'

    # ── Observer fan-out ──────────────────────────────────────
    def _set_status(self, **changes: object) -> None:
        for key, value in changes.items():
            setattr(self.status, key, value)
        for obs in list(self._observers):
            try:
                obs(self.status)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pre-warm observer raised %s — ignored", exc)


__all__ = ['PrewarmService', 'PrewarmStatus']
