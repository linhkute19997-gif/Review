# Development

How to run lint, sanity-checks, and the app itself.

## Setup

```bash
pip install -r requirements.txt        # runtime deps (PyQt6, torch, whisper…)
pip install -r requirements-dev.txt    # dev tools (ruff, pyflakes)
```

`requirements.txt` is large (~1 GB once `torch` + `whisper` land). For audit
work that doesn't run the GUI, only the dev tools are needed:

```bash
pip install ruff pyflakes
```

FFmpeg ≥ 4.4 must be on `PATH` at runtime. The boot check in
`main.py:check_ffmpeg()` blocks startup with a `QMessageBox` if it isn't.

## Lint

```bash
ruff check main.py app/
pyflakes main.py app/*.py app/threads/*.py app/utils/*.py app/domain/*.py
```

`ruff` is the source of truth for the audit loop; `pyflakes` runs alongside
because it occasionally catches things ruff doesn't (and vice versa). Two
pyflakes warnings are pre-existing and intentional — see
[AUDIT_HISTORY.md § Pre-existing pyflakes warnings](AUDIT_HISTORY.md#pre-existing-pyflakes-warnings-intentionally-left).

## Syntax / import sanity

There are no unit tests yet. Two cheap checks act as a smoke test:

```bash
python -m py_compile main.py
python -c "from app.utils import logger, atomic_io, key_vault, config; print('OK')"
```

If you've touched threads or domain modules, extend the second line:

```bash
python -c "
from app.utils import logger, atomic_io, key_vault, config
from app.utils.srt_parser import parse_srt, parse_srt_time_to_ms
from app.domain.project_file import load, save
from app.domain.render_queue import RenderQueue
print('OK')
"
```

## Running the app

```bash
python main.py
```

Requires a real display — the headless CI VM cannot exercise PyQt6. End-to-end
behaviour has to be verified on a developer machine.

## What the app reads / writes at runtime

All paths are relative to the application root and listed in `.gitignore` so
nothing accidentally lands in a commit.

| Path                       | Owner / Purpose                                |
|----------------------------|------------------------------------------------|
| `user_preferences.json`    | UI state, theme choice, last-used config       |
| `api_config.json`          | Per-backend defaults (model / temperature)     |
| `styles_config.json`       | User-saved subtitle / overlay style presets    |
| `encoder_cache.json`       | Cached `ffmpeg -encoders` result               |
| `render_queue.db`          | SQLite (WAL) persistent render queue           |
| `.secrets.dat`             | Encrypted API-key vault (Fernet or AES-CBC)    |
| `logs/app.log`             | Rotating log handler (5 × 1 MB)                |
| `output/`                  | Default render output folder                   |

## Branch conventions

Default Devin convention used in this repo: `devin/{unix-timestamp}-{slug}`.
Branches that survived a merge so far:

- `devin/…-cleanup` → PR #7
- `devin/…-deep-audit-round2` → PR #8
- `devin/…-deep-audit-round3` → PR #9
- `devin/…-audit-docs` → PR #10 (this one)

## Audit loop (TL;DR for the next session)

```bash
git checkout main && git pull
git checkout -b devin/$(date +%s)-{slug}
# ... edits ...
ruff check main.py app/
python -m py_compile main.py
git add -A && git commit -m "..."
git push -u origin HEAD
# create PR via the git_create_pr tool
```

There is **no CI** configured for this repo. Lint + py_compile locally is the
gate.
