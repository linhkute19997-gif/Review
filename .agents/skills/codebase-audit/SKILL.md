---
name: codebase-audit
description: Reference for auditing and bug-fixing the Review Phim Pro PyQt6 desktop app. Use when asked to audit, find bugs, or fix issues in this repository. Captures the layout, lint loop, prior audit findings, and the headless-VM testing constraint.
---

# Review Phim Pro — Audit Skill

This is a PyQt6 desktop video-production app (~9.2 K LOC). The codebase has
been through three audit rounds (PRs #7, #8, #9). Don't re-discover what
those rounds already fixed — start from the docs.

## First read these

1. [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) — module map, layer
   split, pipeline DAG, threading model.
2. [`docs/AUDIT_HISTORY.md`](../../../docs/AUDIT_HISTORY.md) — every fix from
   the previous rounds, with file references and root-cause notes.
3. [`docs/DEVELOPMENT.md`](../../../docs/DEVELOPMENT.md) — lint commands,
   sanity-check imports, runtime file map.

## Lint loop (must pass before commit)

```bash
ruff check main.py app/
pyflakes main.py app/*.py app/threads/*.py app/utils/*.py app/domain/*.py
python -m py_compile main.py
python -c "from app.utils import logger, atomic_io, key_vault, config; print('OK')"
```

Two pyflakes warnings are pre-existing (intentional `try/except ImportError`
verify-import patterns); see AUDIT_HISTORY.md for details. Do NOT "fix" them.

## Branch convention

`devin/$(date +%s)-{descriptive-slug}` off `main`. Always create a PR, never
push to `main`.

## Headless testing constraint

The agent VM has no display, so the PyQt6 GUI cannot be exercised here. The
audit loop is:

1. Static reasoning + lint.
2. PR with a clear "Review & Testing Checklist for Human" so the user
   knows exactly what to click on.

Don't promise behavioural validation that you can't actually run.

## Where bugs tend to live (heuristics from rounds 1–3)

- **Data-shape mismatch between `parse_srt()` and downstream consumers**.
  `parse_srt` writes `start` / `end` strings + `translated_text`. Many
  consumers expect `start_time` / `end_time` ms + `translated`. Anywhere a
  caller passes raw `parse_srt()` output to a non-translation consumer is a
  candidate for a P1 bug.
- **Method names that don't exist**. The voiceover thread has caught
  `_apply_atempo_inplace` (round 3) and a similar mismatch in round 2.
  When you see a private method call, grep for the definition before trusting
  it.
- **Geometry vs. position confusion in overlays**. `DraggableTextItem` and
  `DraggableBlurRegion` use scene-space `pos()` plus a local `rect()`. Set
  one without the other and the overlay snaps to (0, 0) on load.
- **Render-queue / project-file persistence**. Anything that needs to
  survive a restart (`render_queue.db`, `.rpp` files) needs round-tripping
  verified.

## What's NOT in scope here

- Adding unit tests (no test infra yet — that's a separate Phase 4 PR).
- Setting up CI (the repo has none; lint locally is the gate).
- Modifying the GUI (cannot test on this VM).

## When you finish a round

Add a "Round N" section to the **top** of `docs/AUDIT_HISTORY.md`. Record
**what was wrong**, **why** (root cause, not symptom), and the file. Do not
list trivial typo / comment fixes.
