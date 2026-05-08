# Audit History

A running log of code-audit findings and the PRs that addressed them. Newest
entries on top. Use this as a starting point for the next audit instead of
re-reading the whole codebase from scratch.

## Round 3 — PR #9 (merged)

Branch: `devin/1778234515-deep-audit-round3`. Four narrowly scoped P1 fixes,
all surfaced while re-reading the code paths touched by PR #8.

### P1-A — `video_player.set_subtitle_entries` ignored `parse_srt` shape
`set_subtitle_entries` only accepted entries with `start_time` / `end_time`
in milliseconds, but `main_window` was passing the raw `parse_srt()` output
(which uses `start` / `end` as `HH:MM:SS,mmm` strings). Result: the live
overlay never fired during scrub even though the table looked fine.

**Fix**: normalise both shapes into `start_time` / `end_time` ms before
storage. Original `start` / `end` keys are kept intact (other consumers
still need them).

File: `app/video_player.py`.

### P1-B — `_load_overlay_preset` lost the saved (x, y)
Constructor was called with `(x, y, …)` and then `setRect(0, 0, w, h)` was
called immediately afterwards, overwriting the position the constructor had
just set. Loaded presets always snapped back to the origin.

**Fix**: pass `(0, 0, …)` to the constructor, set the local rect to
`(0, 0, w, h)`, then call `setPos(x, y)` to restore the scene-space offset.
Applied to both `DraggableTextItem` and `DraggableBlurRegion`.

File: `app/main_window.py`.

### P1-C — `voiceover_thread` called a method that didn't exist
`_generate_google_tts` referenced `self._apply_atempo_inplace(...)` which was
never defined; only `_apply_atempo_to_wav` existed (wrong path — the merge
helper, not the in-place MP3 helper). Google TTS with a `speech_rate ≠ 100`
raised `AttributeError` mid-render.

**Fix**: renamed the call to `_apply_atempo_inplace_mp3` and added the
implementation. The new helper re-encodes through `libmp3lame` so the path on
disk stays MP3, with safe cleanup on FFmpeg failure (caller continues with
the original unmodified MP3).

File: `app/threads/voiceover_thread.py`.

### P2-A — `project_file._subtitles_to_srt` looked for the wrong key
The convenience SRT in `.rpp` was looking for `'translated'` but `parse_srt`
writes `'translated_text'`. Reopened projects always exported the original
text instead of the translation.

**Fix**: prefer `'translated_text'`, fall back to `'translated'` (legacy),
then `'text'`. Strips empty values so blank translations don't suppress the
original.

File: `app/domain/project_file.py`.

## Round 2 — PR #8 (merged)

Branch: `devin/1778233737-deep-audit-round2`. Five P1 + two P2 issues.

| ID    | What was wrong                                             | File                          |
|-------|------------------------------------------------------------|-------------------------------|
| P1-1  | `.rpp` open didn't restore config / translation settings   | `main_window.py`              |
| P1-2  | Batch render jobs not persisted to render queue            | `main_window.py`              |
| P1-3  | Theme preference not saved from combo box                  | `config_section.py`           |
| P1-4  | Live subtitle preview never wired (`set_subtitle_entries`) | `main_window.py`              |
| P1-5  | Overlay preset round-trip lost blur strength               | `main_window.py`              |
| P2-1  | SRT parser tripped on BOM / blank trailing entries          | `utils/srt_parser.py`         |
| P2-2  | Render queue retry skipped already-failed jobs             | `domain/render_queue.py`      |

These are the fixes verified-still-in-place during round 3.

## Round 1 — PR #7 (merged)

Branch: `devin/1778…-cleanup`. Cleanup + one substantive fix.

- Removed **26 unused imports** across the codebase. `ruff check` now passes
  clean from a previously red state.
- Fixed ASS subtitle background-opacity bug where the `text_subtitle_bg_opacity`
  slider was ignored — `BackColour` had been hard-coded to 50 % opacity. Now
  honours the user's actual opacity + colour choice.
- Set up the environment-config snapshot for future sessions (ruff + pyflakes
  + py_compile lint loop).

## Pre-existing pyflakes warnings (intentionally left)

Both are intentional "verify-import-works" patterns inside `try/except
ImportError` guards. They're flagged by pyflakes but not by ruff (which
respects the `noqa: F401` comment).

| File                  | Line | Why kept                                                   |
|-----------------------|-----:|------------------------------------------------------------|
| `app/dialogs.py`      | 380  | `import yt_dlp` after pip-install verifies the package.    |
| `app/main_window.py`  | 924  | `import edge_tts` (`# noqa: F401`) verifies availability.  |

If pyflakes ever gains `noqa` support these will disappear automatically;
adding our own ignore directive isn't worth the diff right now.

## How to keep this file useful

When you finish an audit / bug-fix PR:

1. Add a new section at the **top** of this file (Round N).
2. For each fix, record: **what was wrong**, **why** (root cause, not just
   symptom), and the file it lives in.
3. If you discovered the bug while re-reading something an earlier round
   touched, say so — "surfaced while re-reading PR #X" is gold for the next
   auditor.
4. Don't list trivial fixes (typo, comment) here. Round 1 is the floor: keep
   the bar at "user-visible bug" or "would crash on a real workflow".
