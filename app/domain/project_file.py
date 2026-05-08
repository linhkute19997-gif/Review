"""
.rpp project file
=================
On disk a Review Phim Pro project is a single ``.rpp`` archive — a
zip file with three guaranteed entries::

    manifest.json  ← ``Project.to_dict()`` payload
    subtitles.srt  ← convenience copy of the editable subtitle table
    config.json    ← snapshot of ``ConfigSection.get_config()``

Saving is atomic: the new archive is written to a sibling temp file
and ``os.replace``'d into place so a crash mid-write can never
corrupt the user's project.

Why zip and not SQLite? Two reasons:

1. The user can unzip a ``.rpp`` and inspect it (or extract the SRT)
   with any file manager — important for support / debugging.
2. The asset list in :class:`Project` is small: tens of paths, a
   subtitle table, a config dict. SQLite would be overkill for the
   project file itself; we *do* use SQLite for the persistent render
   queue (see :mod:`app.domain.render_queue`).
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from typing import Any, Dict, Iterable, List, Optional

from app.domain.models import Project
from app.utils.atomic_io import atomic_write_text
from app.utils.logger import get_logger

logger = get_logger('project_file')

PROJECT_EXT = '.rpp'
MANIFEST = 'manifest.json'
CONFIG = 'config.json'
SUBTITLES = 'subtitles.srt'

_DEFAULT_VERSION = Project.SCHEMA_VERSION


def save(project: Project, path: str) -> None:
    """Write ``project`` to ``path`` atomically.

    The output is a zip archive with the three entries documented in
    the module docstring. ``path`` is replaced atomically using a
    sibling tempfile.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix='.rpp-', suffix='.tmp', dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            payload = project.to_dict()
            zf.writestr(MANIFEST, json.dumps(payload, indent=2,
                                              ensure_ascii=False))
            zf.writestr(CONFIG, json.dumps(project.config, indent=2,
                                            ensure_ascii=False))
            zf.writestr(SUBTITLES, _subtitles_to_srt(project.subtitles))
        os.replace(tmp, path)
        logger.info("Saved project %s with %d job(s) to %s",
                    project.name, len(project.jobs), path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: str) -> Project:
    """Read ``path`` and return a :class:`Project`.

    Backwards-compatible with newer files: missing entries are
    tolerated and unknown manifest keys are ignored.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with zipfile.ZipFile(path, 'r') as zf:
        names = set(zf.namelist())
        if MANIFEST not in names:
            raise ValueError(
                f"{path} is not a valid .rpp archive (missing {MANIFEST})")

        manifest_blob = zf.read(MANIFEST)
        try:
            payload = json.loads(manifest_blob.decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} has corrupt manifest: {exc}") from exc

    project = Project.from_dict(_migrate(payload))
    logger.info("Loaded project %s (schema v%d, %d job(s)) from %s",
                project.name, project.schema_version,
                len(project.jobs), path)
    return project


# ── Helpers ────────────────────────────────────────────────────


def _migrate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Bring a manifest forward to the current schema version."""
    version = int(payload.get('schema_version') or _DEFAULT_VERSION)
    if version > _DEFAULT_VERSION:
        logger.warning(
            "Project schema v%d is newer than this app (v%d); "
            "loading on best-effort basis",
            version, _DEFAULT_VERSION)
    # Future migrations slot in here as ``if version < N: …``.
    payload['schema_version'] = _DEFAULT_VERSION
    return payload


def _subtitles_to_srt(subtitles: Iterable[Dict[str, Any]]) -> str:
    """Render the subtitle table back into SRT for the convenience copy."""
    out = io.StringIO()
    for idx, sub in enumerate(subtitles, start=1):
        start = sub.get('start') or sub.get('start_time') or '00:00:00,000'
        end = sub.get('end') or sub.get('end_time') or '00:00:00,000'
        text_lines: List[str] = []
        text = sub.get('translated') or sub.get('text') or ''
        if isinstance(text, list):
            text_lines = [str(t) for t in text]
        else:
            text_lines = [str(text)]
        out.write(f'{idx}\n{start} --> {end}\n')
        out.write('\n'.join(text_lines))
        out.write('\n\n')
    return out.getvalue()


def export_manifest_only(project: Project, path: str) -> None:
    """Write just the JSON manifest — used by snapshot / debug tooling."""
    atomic_write_text(path, json.dumps(project.to_dict(), indent=2,
                                        ensure_ascii=False))


def is_project_path(path: Optional[str]) -> bool:
    return bool(path) and path.lower().endswith(PROJECT_EXT)


__all__ = [
    'PROJECT_EXT', 'save', 'load', 'export_manifest_only', 'is_project_path',
]
