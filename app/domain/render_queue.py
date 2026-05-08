"""
Persistent render queue
=======================
A tiny SQLite-backed store for :class:`Job`s so a render that was
running when the app crashed (or the user hit Quit) can be resumed
on the next launch.

The schema is a single ``jobs`` table with the JSON payload of
:meth:`Job.to_dict` plus the columns the UI actually queries on
(``id``, ``status``, ``updated_at``). Migrations slot in at the top
of :meth:`RenderQueue._migrate`.

This module owns its connection, takes a per-call lock, and never
exposes the connection — callers manipulate :class:`Job` objects.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Iterable, List, Optional

from app.domain.models import Job, Stage, StageStatus
from app.utils.logger import get_logger

logger = get_logger('render_queue')

_SCHEMA_VERSION = 1


class RenderQueue:
    """Persistent queue of :class:`Job` records.

    Use it as a thin durability layer:

    >>> queue = RenderQueue('/tmp/render-queue.db')
    >>> queue.enqueue(job)
    >>> for j in queue.pending(): runner.run_job(j)
    >>> queue.update(job)  # persist new status / progress
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        self._migrate()

    # ── Schema ───────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    def _migrate(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                'CREATE TABLE IF NOT EXISTS schema_meta '
                '(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute(
                'CREATE TABLE IF NOT EXISTS jobs ('
                'id TEXT PRIMARY KEY,'
                ' name TEXT NOT NULL,'
                ' render_status TEXT NOT NULL,'
                ' progress INTEGER NOT NULL DEFAULT 0,'
                ' updated_at REAL NOT NULL,'
                ' payload TEXT NOT NULL'
                ')')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_status '
                'ON jobs(render_status, updated_at)')
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
                    (str(_SCHEMA_VERSION),))
            # Future migrations: ``elif int(row['value']) < N: …``.
            conn.commit()

    # ── Mutation ─────────────────────────────────────────────
    def enqueue(self, job: Job) -> None:
        """Insert a job, marking the render stage ``PENDING``."""
        if Stage.RENDER.value not in job.stages:
            job.stages[Stage.RENDER.value] = StageStatus.PENDING.value
        if not job.created_at:
            job.created_at = time.time()
        job.updated_at = time.time()
        self._upsert(job)

    def update(self, job: Job) -> None:
        """Persist a status change."""
        job.updated_at = time.time()
        self._upsert(job)

    def remove(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
            conn.commit()

    def clear_completed(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM jobs WHERE render_status = ?',
                (StageStatus.DONE.value,))
            conn.commit()
            return cur.rowcount

    # ── Read ────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                'SELECT payload FROM jobs WHERE id = ?', (job_id,)
            ).fetchone()
        return Job.from_dict(json.loads(row['payload'])) if row else None

    def pending(self) -> List[Job]:
        """Jobs whose render stage is *not yet* DONE / FAILED.

        These are the ones the resume dialog should offer.
        """
        terminal = (StageStatus.DONE.value, StageStatus.FAILED.value,
                    StageStatus.CANCELLED.value)
        return self._select(
            'SELECT payload FROM jobs WHERE render_status NOT IN ('
            + ','.join('?' * len(terminal)) + ') ORDER BY updated_at ASC',
            terminal)

    def all(self) -> List[Job]:
        return self._select('SELECT payload FROM jobs ORDER BY updated_at ASC')

    def by_status(self, status: StageStatus) -> List[Job]:
        return self._select(
            'SELECT payload FROM jobs WHERE render_status = ? '
            'ORDER BY updated_at ASC',
            (status.value,))

    # ── Internals ───────────────────────────────────────────
    def _upsert(self, job: Job) -> None:
        render_status = job.stages.get(
            Stage.RENDER.value, StageStatus.PENDING.value)
        payload = json.dumps(job.to_dict(), ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                'INSERT INTO jobs (id, name, render_status, progress, '
                'updated_at, payload) VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET '
                ' name=excluded.name,'
                ' render_status=excluded.render_status,'
                ' progress=excluded.progress,'
                ' updated_at=excluded.updated_at,'
                ' payload=excluded.payload',
                (job.id, job.name, render_status, int(job.progress),
                 job.updated_at, payload))
            conn.commit()

    def _select(self, sql: str,
                params: Iterable[object] = ()) -> List[Job]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [Job.from_dict(json.loads(r['payload'])) for r in rows]


__all__ = ['RenderQueue']
