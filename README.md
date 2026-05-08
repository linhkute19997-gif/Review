# Review Phim Pro

PyQt6 desktop application for video content production. Pulls a source clip,
extracts subtitles (Whisper / PaddleOCR), translates them (Google / Gemini /
Baidu / ChatGPT), generates voice-over (Edge TTS / gTTS / ElevenLabs), and
renders a final MP4 with overlays + BGM via FFmpeg.

## Run

```bash
pip install -r requirements.txt
python main.py
```

FFmpeg ≥ 4.4 must be on `PATH` at runtime — the boot check in
`main.py:check_ffmpeg()` blocks startup with a `QMessageBox` otherwise.

## Repo layout

```
main.py
app/                 PyQt6 widgets, threads, domain core
  domain/            Pure-Python core (no Qt, no FFmpeg)
  threads/           QThread workers (translate / TTS / FFmpeg)
  utils/             Logging, atomic JSON, keychain, …
docs/                Architecture, development, audit history
.agents/skills/      Reference notes for Devin sessions
requirements.txt     Runtime deps
requirements-dev.txt Dev tools (ruff, pyflakes)
```

Detailed module map → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full module map, layer split,
  pipeline DAG, threading model.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — lint / sanity-check commands,
  runtime file paths, audit-loop TL;DR.
- [docs/AUDIT_HISTORY.md](docs/AUDIT_HISTORY.md) — log of every audit round
  with file references and root-cause notes.

## License

Internal project, no license declared yet.
