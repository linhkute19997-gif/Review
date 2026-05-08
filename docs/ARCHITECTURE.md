# Review Phim Pro — Architecture

PyQt6 desktop application for video content production: pulls a source clip,
extracts subtitles, translates them, generates voice-over, and renders a final
MP4 with overlays / BGM. About **9.2 K LOC** of Python total.

```
main.py
└── app/
    ├── __init__.py
    ├── main_window.py            (1422)  Central QMainWindow orchestrator
    ├── config_section.py         ( 935)  8-tab configuration panel
    ├── video_player.py           ( 359)  QMediaPlayer + QGraphicsView preview
    ├── subtitle_edit.py          ( 504)  SRT table editor (undo/redo, S&R)
    ├── subtitle_extract.py       ( 620)  Whisper / PaddleOCR backends
    ├── render_section.py         ( 184)  Progress / GPU / batch table panel
    ├── render_queue_dialog.py    ( 235)  Queue browser dialog
    ├── shutdown_dialog.py        (  90)  "Shut down after render" picker
    ├── dialogs.py                ( 487)  API / Style / Output / Douyin
    ├── overlays.py               ( 300)  DraggableTextItem / DraggableBlurRegion
    ├── snow_overlay.py           (  69)  Decorative snowfall layer
    ├── styles/                   ( QSS dark theme, no Python code)
    │
    ├── domain/                   Pure-Python core — no PyQt, no FFmpeg
    │   ├── models.py             ( 231)  Stage / StageStatus / MediaAsset / Job / Project
    │   ├── pipeline.py           ( 198)  Synchronous DAG runner (Qt-free)
    │   ├── prewarm.py            ( 162)  Whisper / PaddleOCR background warmup
    │   ├── project_file.py       ( 160)  .rpp save / load (zip + JSON + SRT)
    │   └── render_queue.py       ( 168)  SQLite WAL persistent queue
    │
    ├── threads/                  Qt worker threads (long-running tasks)
    │   ├── translate_thread.py   ( 477)  Google / Gemini / Baidu / ChatGPT
    │   ├── voiceover_thread.py   ( 515)  Edge TTS / gTTS / pydub fallback
    │   └── video_creator.py      ( 667)  FFmpeg pipeline + hwaccel probe
    │
    └── utils/                    Cross-cutting helpers
        ├── config.py             ( 385)  Constants + load/save JSON configs
        ├── theme.py              ( 112)  Dark / Light / System QSS switcher
        ├── srt_parser.py         ( 144)  Robust SRT parser (BOM, blank entries…)
        ├── atomic_io.py          ( 120)  Cross-process atomic JSON writer
        ├── key_vault.py          ( 334)  keyring + Fernet/AES-CBC fallback
        ├── encoder_detector.py   ( 102)  Cached `ffmpeg -encoders` probe
        ├── ffmpeg_check.py       ( 101)  Boot-time FFmpeg ≥ 4.4 check
        └── logger.py             (  75)  Rotating file handler at logs/app.log
```

## Three-layer split

```
┌─────────────────────────────────────────────────────────────┐
│ Presentation  ────  PyQt6 widgets, QSS, signals             │
│   main_window, config_section, video_player, render_section,│
│   subtitle_edit, dialogs, overlays, render_queue_dialog     │
├─────────────────────────────────────────────────────────────┤
│ Threads      ────  QThread workers (translate / TTS / FFmpeg) │
├─────────────────────────────────────────────────────────────┤
│ Domain       ────  Plain dataclasses + pipeline runner      │
│   models, pipeline, project_file, render_queue, prewarm     │
└─────────────────────────────────────────────────────────────┘
              │
        Utils (logging, atomic JSON, keychain, …)
```

`app/domain/` is intentionally Qt-free and FFmpeg-free so it stays unit-testable
and reusable from a future CLI front-end. The threads layer wraps the domain
runner in `QThread` so progress can be `pyqtSignal`-emitted to the UI.

## Pipeline (domain/pipeline.py)

```
EXTRACT  →  TRANSLATE  →  VOICEOVER  →  RENDER
```

`PipelineRunner` is a synchronous DAG executor. Each stage callable receives
`(job, ctx)` and either returns or raises `StageError`. Cancellation is
cooperative via `threading.Event`. The runner does **not** own any Qt signal —
UI code subscribes via the `EventListener` callback and converts events into
`pyqtSignal` payloads.

`STAGE_DEPS` declares the dependency graph as data so adding stages later
(e.g. an upscale step) is a one-line change.

## Project file format (.rpp)

Zip archive containing:

| Member             | Producer                          |
|--------------------|-----------------------------------|
| `manifest.json`    | top-level metadata + `Project`    |
| `config.json`      | snapshot of `ConfigSection.get_config()` |
| `subtitles.srt`    | convenience copy of the table     |

Schema versioning lives on `Project.schema_version`. `_migrate()` in
`domain/project_file.py` is the single migration hook.

## Render queue (domain/render_queue.py)

SQLite database at the application root (`render_queue.db`), opened in **WAL**
mode. Stores enqueued / running / finished `Job`s for crash recovery so a
power-loss mid-render still surfaces the original FFmpeg invocation on the
next boot. `MainWindow._maybe_resume_pending_jobs()` is the recovery entry
point (fires 500 ms after the window paints).

## Threading model

- **Main thread** — Qt event loop, all widget mutations.
- **`translate_thread.TranslateThread`** — translates subtitles in batches,
  emits `progress(int, str)` and `finished(list)`.
- **`voiceover_thread.VoiceOverThread`** — fans Edge TTS / gTTS calls out with
  a thread pool, then merges the segments via FFmpeg `concat` or `amix`.
- **`video_creator.VideoCreatorThread`** — builds the full FFmpeg argv,
  parses `-progress pipe:1` for real-time progress.
- **`prewarm.PrewarmService`** — daemon thread that imports Whisper /
  PaddleOCR in the background so the first extraction doesn't pay the
  ~10 s import cost.
- Encoder probe is a one-off `threading.Thread` started from `main.py`.

`MainWindow` holds at most one active worker per stage (`self.translate_thread`,
`self.render_thread`, `self.voiceover_thread`); finished workers are cleared
to `None` in their done / error handlers so re-clicking the button re-creates
a fresh thread.

## Concurrency-safe filesystem layer

- `utils/atomic_io.py` — `atomic_write_json()` writes to a tempfile and
  `os.replace`s it; uses `filelock` for cross-process coordination plus an
  in-process `threading.RLock` keyed by absolute path.
- `utils/key_vault.py` — secret storage. Tries OS keyring first, falls back
  to a Fernet-encrypted `.secrets.dat` (with an AES-CBC fallback if the
  `cryptography` package is missing).

## Boot sequence (main.py)

```python
1. apply_theme(app)                 # load saved QSS before any widget
2. check_ffmpeg()                   # block boot if FFmpeg < 4.4
3. PrewarmService().start()         # fire-and-forget Whisper / PaddleOCR
4. threading.Thread(_prewarm_encoders).start()  # cache `ffmpeg -encoders`
5. MainWindow(prewarm=...).show()
6. QTimer.singleShot(500, _maybe_resume_pending_jobs)
```

## Cross-references

- Per-bug audit history → [AUDIT_HISTORY.md](AUDIT_HISTORY.md).
- Lint / test commands → [DEVELOPMENT.md](DEVELOPMENT.md).
- Runtime dependencies → [`requirements.txt`](../requirements.txt) at repo root.
- Dev tools → [`requirements-dev.txt`](../requirements-dev.txt).
