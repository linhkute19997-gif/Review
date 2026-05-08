"""
Domain models
=============
Plain dataclasses describing the *content* of a Review Phim Pro
session. They contain no behaviour beyond serialisation helpers so
that:

* the pipeline runner can hand them across threads without locking;
* the project file can write them to disk verbatim;
* the render queue can persist them in SQLite without a heavy ORM.

Nothing in this module imports PyQt or FFmpeg.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────
# Pipeline stages and statuses
# ──────────────────────────────────────────────────────────────────────


class Stage(str, Enum):
    """Logical pipeline stages, in topological order.

    Each stage has a fixed dependency on the previous stages — see
    ``app.domain.pipeline.STAGE_DEPS``. The string value is what gets
    persisted in JSON / SQLite, so order rearrangement is free but
    renames are a migration.
    """

    EXTRACT = 'extract'      # Whisper / OCR → SRT
    TRANSLATE = 'translate'  # Backend → translated SRT
    VOICEOVER = 'voiceover'  # SRT → audio file
    RENDER = 'render'        # Video + SRT + audio → final mp4


class StageStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    CANCELLED = 'cancelled'


# ──────────────────────────────────────────────────────────────────────
# Media assets
# ──────────────────────────────────────────────────────────────────────


@dataclass
class MediaAsset:
    """A file referenced by a job — input video, SRT, BGM, voice, logo."""

    kind: str           # 'video' | 'srt' | 'audio' | 'bgm' | 'logo' | 'voice'
    path: str           # absolute path on disk
    role: str = ''      # free-form tag ('original', 'translated', …)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'MediaAsset':
        return cls(
            kind=str(payload.get('kind', '')),
            path=str(payload.get('path', '')),
            role=str(payload.get('role', '')),
        )


# ──────────────────────────────────────────────────────────────────────
# Job — a single render unit (one input video → one output mp4)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Job:
    """One render unit. Owns its config snapshot so resume after a
    crash always reproduces the exact same FFmpeg invocation.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ''  # Human-readable label (defaults to video stem)
    assets: List[MediaAsset] = field(default_factory=list)

    # Snapshot of ``ConfigSection.get_config()`` at job creation time.
    config: Dict[str, Any] = field(default_factory=dict)

    # Stage → status map. ``Stage.RENDER`` is always present; the
    # earlier stages are added only if the user actually queued them.
    stages: Dict[str, str] = field(default_factory=dict)

    # Per-stage error message, populated when status is ``FAILED``.
    errors: Dict[str, str] = field(default_factory=dict)

    output_path: str = ''
    progress: int = 0       # 0..100, last reported render progress
    created_at: float = 0.0
    updated_at: float = 0.0

    # ── Helpers ──────────────────────────────────────────────────
    def asset_by_kind(self, kind: str, role: Optional[str] = None
                      ) -> Optional[MediaAsset]:
        for asset in self.assets:
            if asset.kind != kind:
                continue
            if role is None or asset.role == role:
                return asset
        return None

    def status_of(self, stage: Stage) -> StageStatus:
        raw = self.stages.get(stage.value, StageStatus.PENDING.value)
        try:
            return StageStatus(raw)
        except ValueError:
            return StageStatus.PENDING

    def set_status(self, stage: Stage, status: StageStatus,
                   error: Optional[str] = None) -> None:
        self.stages[stage.value] = status.value
        if error:
            self.errors[stage.value] = error
        elif status not in (StageStatus.FAILED, StageStatus.CANCELLED):
            self.errors.pop(stage.value, None)

    def is_complete(self) -> bool:
        return self.status_of(Stage.RENDER) == StageStatus.DONE

    def is_terminal(self) -> bool:
        """True once no further work can advance the job."""
        if self.is_complete():
            return True
        for raw in self.stages.values():
            if raw == StageStatus.FAILED.value:
                return True
        return False

    # ── Serialisation ────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'assets': [a.to_dict() for a in self.assets],
            'config': dict(self.config),
            'stages': dict(self.stages),
            'errors': dict(self.errors),
            'output_path': self.output_path,
            'progress': int(self.progress),
            'created_at': float(self.created_at),
            'updated_at': float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'Job':
        return cls(
            id=str(payload.get('id') or uuid.uuid4().hex),
            name=str(payload.get('name', '')),
            assets=[MediaAsset.from_dict(a)
                    for a in payload.get('assets') or []],
            config=dict(payload.get('config') or {}),
            stages=dict(payload.get('stages') or {}),
            errors=dict(payload.get('errors') or {}),
            output_path=str(payload.get('output_path', '')),
            progress=int(payload.get('progress') or 0),
            created_at=float(payload.get('created_at') or 0.0),
            updated_at=float(payload.get('updated_at') or 0.0),
        )


# ──────────────────────────────────────────────────────────────────────
# Project — what a .rpp file contains
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Project:
    """Top-level container persisted in a ``.rpp`` archive.

    A project bundles the editable subtitle table, the configuration
    snapshot and any number of jobs. ``schema_version`` lets future
    refactors migrate older project files in place.
    """

    SCHEMA_VERSION = 1

    name: str = 'Untitled Project'
    schema_version: int = SCHEMA_VERSION

    # Active subtitle table the user is editing in the main window.
    subtitles: List[Dict[str, Any]] = field(default_factory=list)

    # Translation source / target language and backend selection.
    translation: Dict[str, Any] = field(default_factory=dict)

    # Snapshot of ``ConfigSection.get_config()`` when the project was
    # last saved. Used as a default for new jobs.
    config: Dict[str, Any] = field(default_factory=dict)

    jobs: List[Job] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'name': self.name,
            'translation': dict(self.translation),
            'config': dict(self.config),
            'subtitles': list(self.subtitles),
            'jobs': [j.to_dict() for j in self.jobs],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> 'Project':
        return cls(
            name=str(payload.get('name', 'Untitled Project')),
            schema_version=int(
                payload.get('schema_version') or cls.SCHEMA_VERSION),
            subtitles=list(payload.get('subtitles') or []),
            translation=dict(payload.get('translation') or {}),
            config=dict(payload.get('config') or {}),
            jobs=[Job.from_dict(j) for j in payload.get('jobs') or []],
        )


__all__ = [
    'Stage', 'StageStatus', 'MediaAsset', 'Job', 'Project',
]
