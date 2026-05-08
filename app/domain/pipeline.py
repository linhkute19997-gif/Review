"""
Pipeline runner
===============
Tiny DAG executor for the four canonical stages of a Review Phim Pro
job:

    EXTRACT → TRANSLATE → VOICEOVER → RENDER

The runner is intentionally synchronous and Qt-free: it walks the
stage DAG, calls a *stage callable* for each requested stage, and
records the resulting status on the :class:`Job`. Callers run the
runner from a worker thread (``QThread`` in the UI, or a plain
``threading.Thread`` in CLI / tests).

Stage callables receive ``(job, ctx)`` and return ``None`` on success
or raise ``StageError`` on failure. ``ctx`` is a free-form dict the
runner threads through unchanged so adapters can pass FFmpeg paths,
loggers, cancellation events, etc.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.domain.models import Job, Stage, StageStatus
from app.utils.logger import get_logger

logger = get_logger('pipeline')


# ── DAG declaration ────────────────────────────────────────────
#
# Each stage depends on every earlier stage in the canonical order.
# We keep this as data so future plugin stages (e.g. an "Upscale"
# step between RENDER and a hypothetical ``EXPORT``) can extend the
# DAG without rewriting the runner.

STAGE_ORDER: List[Stage] = [
    Stage.EXTRACT,
    Stage.TRANSLATE,
    Stage.VOICEOVER,
    Stage.RENDER,
]

STAGE_DEPS: Dict[Stage, List[Stage]] = {
    Stage.EXTRACT: [],
    Stage.TRANSLATE: [Stage.EXTRACT],
    Stage.VOICEOVER: [Stage.EXTRACT, Stage.TRANSLATE],
    Stage.RENDER: [Stage.EXTRACT, Stage.TRANSLATE, Stage.VOICEOVER],
}


class StageError(RuntimeError):
    """Raised by a stage callable to signal a hard failure."""


# ── Stage callable type alias ──────────────────────────────────
StageCallable = Callable[[Job, Dict[str, Any]], None]


@dataclass
class PipelineEvent:
    """Status update emitted on every stage transition.

    The runner itself does not own a Qt signal — UI code converts
    these events into ``pyqtSignal`` payloads.
    """
    job_id: str
    stage: Stage
    status: StageStatus
    message: str = ''
    progress: int = 0


EventListener = Callable[[PipelineEvent], None]


# ── Runner ─────────────────────────────────────────────────────


class PipelineRunner:
    """Walk the stage DAG for a sequence of jobs.

    The runner is single-threaded by design: the heavy work happens
    inside the stage callables (which already use ``QThread`` or
    asyncio internally). Cancellation is cooperative — set
    :attr:`cancel_event` and the runner will skip remaining stages
    once the current one returns.
    """

    def __init__(self,
                 stages: Dict[Stage, StageCallable],
                 listener: Optional[EventListener] = None,
                 cancel_event: Optional[threading.Event] = None):
        # Allow registering a subset; missing stages are auto-skipped.
        self._stages: Dict[Stage, StageCallable] = dict(stages)
        self._listener = listener or (lambda _e: None)
        self.cancel_event = cancel_event or threading.Event()

    # ── Public API ───────────────────────────────────────────
    def run_job(self, job: Job, ctx: Optional[Dict[str, Any]] = None,
                stages: Optional[Iterable[Stage]] = None) -> bool:
        """Run all *requested* stages for one job.

        ``stages`` defaults to every stage that has a registered
        callable. Returns True if the job ended in :attr:`Stage.RENDER`
        being ``DONE`` (or terminal-success for the last requested
        stage when RENDER isn't requested).
        """
        ctx = dict(ctx or {})
        ordered = self._select_stages(stages)

        for stage in ordered:
            if self.cancel_event.is_set():
                self._update(job, stage, StageStatus.CANCELLED,
                             message='cancelled')
                continue

            if not self._dependencies_satisfied(job, stage):
                self._update(job, stage, StageStatus.SKIPPED,
                             message='unmet dependency')
                continue

            self._update(job, stage, StageStatus.RUNNING)
            try:
                self._stages[stage](job, ctx)
            except StageError as exc:
                logger.error("Stage %s failed for job %s: %s",
                             stage.value, job.id, exc)
                self._update(job, stage, StageStatus.FAILED, message=str(exc))
                # Hard-fail: skip downstream stages.
                for downstream in ordered[ordered.index(stage) + 1:]:
                    self._update(job, downstream, StageStatus.SKIPPED,
                                 message='blocked by upstream failure')
                return False
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled error in stage %s: %s",
                                 stage.value, exc)
                self._update(job, stage, StageStatus.FAILED, message=str(exc))
                for downstream in ordered[ordered.index(stage) + 1:]:
                    self._update(job, downstream, StageStatus.SKIPPED,
                                 message='blocked by upstream failure')
                return False
            else:
                self._update(job, stage, StageStatus.DONE)
        return job.is_complete()

    def run_all(self, jobs: Iterable[Job],
                ctx: Optional[Dict[str, Any]] = None,
                stages: Optional[Iterable[Stage]] = None) -> List[Job]:
        completed: List[Job] = []
        for job in jobs:
            if self.cancel_event.is_set():
                break
            if self.run_job(job, ctx=ctx, stages=stages):
                completed.append(job)
        return completed

    # ── Internals ────────────────────────────────────────────
    def _select_stages(self,
                       stages: Optional[Iterable[Stage]]) -> List[Stage]:
        if stages is None:
            requested = set(self._stages.keys())
        else:
            requested = set(stages) & set(self._stages.keys())
        return [s for s in STAGE_ORDER if s in requested]

    def _dependencies_satisfied(self, job: Job, stage: Stage) -> bool:
        for dep in STAGE_DEPS.get(stage, []):
            if dep not in self._stages:
                # Dep was never requested → treat as satisfied so we
                # don't deadlock when the user re-runs a single stage.
                continue
            if job.status_of(dep) != StageStatus.DONE:
                return False
        return True

    def _update(self, job: Job, stage: Stage, status: StageStatus,
                message: str = '', progress: Optional[int] = None) -> None:
        job.set_status(stage, status, error=message if status == StageStatus.FAILED else None)
        job.updated_at = time.time()
        if progress is not None:
            job.progress = max(0, min(100, int(progress)))
        try:
            self._listener(PipelineEvent(
                job_id=job.id, stage=stage, status=status,
                message=message, progress=job.progress))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pipeline listener raised %s — ignored", exc)


__all__ = [
    'PipelineRunner', 'PipelineEvent', 'EventListener',
    'StageError', 'StageCallable',
    'STAGE_ORDER', 'STAGE_DEPS',
]
