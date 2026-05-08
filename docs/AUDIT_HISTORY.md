# Audit History

A running log of code-audit findings and the PRs that addressed them. Newest
entries on top. Use this as a starting point for the next audit instead of
re-reading the whole codebase from scratch.

## Round 5 — devin/1778238023-audit-round5

Branch: `devin/1778238023-audit-round5`. Two P2 + two P3 fixes surfaced while
re-reading the rendering / overlay / TTS paths after PR #11. The first two
both produce visibly wrong output and the last two are silent quality issues.

### P2-A — `_escape_drawtext_text` doubled every backslash it inserted

`VideoCreatorThread._escape_drawtext_text` iterated
`["'", ":", "\\", "[", "]"]` and replaced each character with `\<ch>`. Order
matters: by the time the loop reached `"\\"`, all the backslashes the
*previous* iterations had just inserted (`\'`, `\:`, etc.) got *themselves*
escaped too, so a literal apostrophe ended up rendered as `\\\'` and a literal
`:` as `\\\:`.

User-visible: top-border / bottom-border text and any draggable text overlay
that contained `'`, `:`, `[`, or `]` rendered in the final video with a
spurious backslash in front of every special character — and apostrophes in
particular even risked terminating the single-quoted filtergraph value
prematurely (FFmpeg single-quoted strings *cannot* be backslash-escaped).

**Fix**: rewrite the helper to escape `\\` *first*, then `:` / `[` / `]`,
then convert `'` via the canonical FFmpeg close-escape-reopen trick
(`'\''`). The docstring now also explains why each rule exists.

File: `app/threads/video_creator.py`.

### P2-B — Overlay coordinates were scaled against the view, not the video

`VideoPlayerSection.get_all_overlays` reported `preview_width =
self.view.width()` and `preview_height = self.view.height()`. Overlay
coordinates returned by `DraggableTextItem.get_data` /
`DraggableBlurRegion.get_region_data` are *scene*-space, and
`_fit_video_to_view` sets `scene.sceneRect()` to `video_item.boundingRect()`
— so the scene is bounded by the *scaled video item*, not the surrounding
view (the view is bigger when aspect-ratio letterboxing leaves bars on the
sides or top/bottom).

`video_creator.run` then computed `scale_x = w / preview_w` and used that
to map preview pixels to video pixels. With the wrong denominator, every
text/blur overlay drifted toward the top-left of the rendered video by a
factor equal to the letterbox ratio — i.e. the more the video was
letterboxed, the more the overlay slipped away from where the user dragged
it.

**Fix**: take the size from `self.video_item.size()` instead of
`self.view.{width,height}()`. The result is bounded by `max(..., 1)` to
avoid `ZeroDivisionError` if a render is somehow triggered before
`_fit_video_to_view` runs (the default `QGraphicsVideoItem` size is
non-zero, so this is purely defensive).

File: `app/video_player.py`.

### P3-A — ChatGPT batch path ignored every API key after the first

`TranslateThread._run_batch_llm._run_one` rotated keys for `Gemini`
(`api_keys[batch_index % key_count]`) but pinned `ChatGPT` to
`self.api_keys[0]` regardless of how many keys the user configured. A user
who plugged two OpenAI keys in for redundancy would silently exhaust the
first one (and never benefit from the second when it 429ed). The Gemini
side already had the rotation; ChatGPT was the asymmetry.

**Fix**: hoist the rotation out so both backends share it. ChatGPT now
gets `api_keys[batch_index % key_count]`; Gemini still receives the full
list + start index because `_translate_batch_gemini` rotates *internally*
on transient failures.

File: `app/threads/translate_thread.py`.

### P3-B — Voice-over WAV intermediates piled up across runs

At the start of every `VoiceOverThread.run`, the worker globbed
`output/voice_temp/*.mp3` to clear leftovers from the previous render — but
`_apply_atempo_to_wav` writes a `voice_NNNN.wav` next to each MP3 whenever
fit-to-subtitle stretches a segment. Those WAVs were never cleaned, so a
heavy user who renders many videos in one session would see `voice_temp/`
grow indefinitely, eventually tripping the disk-space guard in
`VideoCreatorThread._check_disk_space`.

**Fix**: extend the cleanup loop to sweep both `*.mp3` and `*.wav`, with
per-file `OSError` swallowing so a stuck handle on one file doesn't stop
us from clearing the rest.

File: `app/threads/voiceover_thread.py`.

## Round 4 — devin/1778237716-audit-round4

Branch: `devin/1778237716-audit-round4`. Two P1 + two P2 fixes surfaced while
re-reading the persistence + translation + voice paths after PR #9.

### P1-A — `_retry_queued_job` didn't mark the render as RUNNING

`RenderQueueDialog._retry_selected` only flips the persisted status back to
`PENDING`. The launcher (`MainWindow._retry_queued_job`) then started a fresh
`VideoCreatorThread` *without* persisting a transition to `RUNNING` — only the
non-retry path (`_enqueue_active_render`) did that. Two consequences:

1. The queue dialog kept showing "Đang chờ" while the retry was actively
   rendering, so the user could enqueue / retry the same job twice.
2. If the app crashed mid-retry, `_maybe_resume_pending_jobs` had no way to
   tell the row apart from a brand-new pending job — it stayed at `PENDING`
   forever instead of being recovered alongside other `RUNNING` rows.

**Fix**: in `_retry_queued_job`, call `job.set_status(Stage.RENDER, RUNNING)`
and `self.render_queue.update(job)` before launching the worker, mirroring
`_enqueue_active_render`. Failures to persist are logged but don't abort the
retry (the in-memory job state still drives the live render).

File: `app/main_window.py`.

### P1-B — `subtitles_to_srt` raised `KeyError` on `'timeline'`

`utils/srt_parser.subtitles_to_srt` indexed `entry['timeline']` directly. That
key is written by `parse_srt`, but other producers don't always set it:

* `project_file._subtitles_to_srt` already had a defensive fallback (`start` /
  `end` → reconstructed timeline → `'00:00:00,000'`), but the public helper in
  `srt_parser` did not.
* Any future caller that builds entries by hand (or any rpp written by a
  third-party tool that strips the field) would crash on the first save.

**Fix**: mirror the project-file fallback — prefer `entry['timeline']` when
present, otherwise rebuild from `start` / `end`, defaulting to
`'00:00:00,000'` so saving never KeyErrors. Also added a docstring noting the
canonical timeline shape.

File: `app/utils/srt_parser.py`.

### P2-A — LLM batch translation rotated keys via `list.index`

`TranslateThread._run_batch_llm._run_one` looked up the current batch's index
via `batches.index(batch)` for Gemini key rotation. Two problems:

1. `list.index` is O(n) — repeated on every batch this is O(n²) work for a
   purely cosmetic lookup.
2. `list.index` returns the *first* match by equality. If two batches happen
   to contain identical entries (duplicate dialogue, repeated jingles), they
   collapse onto the same API key and lose rotation entirely.

**Fix**: pre-compute the batch index via `enumerate(batches)` when submitting
to the executor and pass it into `_run_one` as an explicit argument. Also
hoisted `key_count` outside the closure so we don't re-take `len()` per call.

File: `app/threads/translate_thread.py`.

### P2-B — `_preview_voice` silently ignored the selected provider

The preview pipeline only knows how to drive Edge TTS, but `_preview_voice`
unconditionally consulted `VOICE_CONFIGS_EDGE_VI`. When the user picked
Google TTS or ElevenLabs, the `voice_type` lookup missed (different label
set) and fell through to `VOICE_CONFIGS_EDGE_VI[0]` — i.e. the first
Vietnamese Edge voice. Users would hear a Vietnamese Edge sample and assume
their actual provider produced it.

**Fix**: gate `_preview_voice` on the active provider (`combo_voice_provider`).
Non-Edge providers now show a clear `QMessageBox.information` explaining the
limitation and pointing at the full voiceover button instead. Edge previews
are unchanged.

File: `app/main_window.py`.

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
| `app/main_window.py`  | 940  | `import edge_tts` (`# noqa: F401`) verifies availability.  |

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
