"""
Voice-Over Thread — Edge TTS, Google TTS, ElevenLabs
"""
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.utils.config import BASE_DIR, FFMPEG_PATH, VOICE_CONFIGS_EDGE_VI
from app.utils.logger import get_logger

logger = get_logger('voiceover')

# Cap concurrent Edge TTS requests — the public service rate-limits
# aggressive callers, but 8 is well within budget for the typical
# burst (and matches Phase 2 target throughput). Per-segment retry
# below absorbs the occasional 429.
_EDGE_TTS_CONCURRENCY = 8

# Per-segment retry policy for transient Edge TTS failures (429,
# connection drops, etc). Backoff doubles each attempt.
_EDGE_TTS_MAX_ATTEMPTS = 3
_EDGE_TTS_BASE_BACKOFF_S = 0.5

# Beyond this many segments a single FFmpeg amix invocation gets
# unwieldy (long command line, slow filtergraph init). We chunk into
# groups of this size and recursively amix the chunk outputs.
_AMIX_CHUNK_SIZE = 32


def _ffmpeg_executable():
    """Return the bundled ffmpeg(.exe) if present, else 'ffmpeg' on $PATH."""
    if os.path.exists(FFMPEG_PATH):
        return FFMPEG_PATH
    return shutil.which('ffmpeg') or 'ffmpeg'


def _atempo_chain(factor):
    """FFmpeg atempo accepts 0.5–2.0 per filter; chain for outside that range.

    `atempo` preserves pitch while changing tempo — unlike pydub's
    frame_rate trick which shifts pitch like a chipmunk.
    """
    if factor <= 0:
        return None
    parts = []
    remaining = float(factor)
    while remaining > 2.0:
        parts.append('atempo=2.0')
        remaining /= 2.0
    while remaining < 0.5:
        parts.append('atempo=0.5')
        remaining /= 0.5
    parts.append(f'atempo={remaining:.6f}')
    return ','.join(parts)


class VoiceOverThread(QThread):
    progress = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, subtitles, voice_type='Alex (Nam chuẩn)',
                 speech_rate=100, volume=100, provider='Edge TTS (miễn phí)',
                 target_lang='vi', fit_to_subtitle=True):
        super().__init__()
        self.subtitles = subtitles
        self.voice_type = voice_type
        self.speech_rate = speech_rate
        self.volume = volume
        self.provider = provider
        self.target_lang = target_lang
        # When True, each segment is squeezed/stretched (pitch-preserving) to
        # match its subtitle slot duration so it doesn't bleed into the next.
        self.fit_to_subtitle = fit_to_subtitle
        self._running = True

    def run(self):
        try:
            output_dir = Path(BASE_DIR) / 'output' / 'voice_temp'
            output_dir.mkdir(parents=True, exist_ok=True)
            # Clean leftovers from previous runs. We sweep BOTH ``*.mp3``
            # (Edge / Google TTS output, plus the final ``voiceover_output``)
            # and ``*.wav`` because :meth:`_apply_atempo_to_wav` writes a
            # ``voice_NNNN.wav`` next to each MP3 when fit-to-subtitle
            # speeds it up. The old cleanup only matched ``*.mp3`` so
            # those WAVs piled up across runs and quietly leaked disk.
            for pattern in ('*.mp3', '*.wav'):
                for f in output_dir.glob(pattern):
                    try:
                        f.unlink()
                    except OSError:
                        pass

            self.progress.emit("Chuẩn bị phụ đề...")
            processed_subs = []
            for sub in self.subtitles:
                text = sub.get('translated_text', '').strip() or sub.get('text', '').strip()
                if text:
                    processed_subs.append({
                        'text': text,
                        'start_ms': self._parse_srt_time(sub.get('start', '00:00:00,000')),
                        'end_ms': self._parse_srt_time(sub.get('end', '00:00:00,000')),
                    })

            if not processed_subs:
                self.error.emit("Không có phụ đề để lồng tiếng!")
                return

            logger.debug("Processed %d subtitles", len(processed_subs))

            if 'Edge' in self.provider:
                segments = self._generate_edge_tts(processed_subs, str(output_dir))
            elif 'Google' in self.provider:
                segments = self._generate_google_tts(processed_subs, str(output_dir))
            else:
                self.error.emit(f"Provider {self.provider} chưa được hỗ trợ!")
                return

            if segments:
                final_path = self._merge_segments(segments, str(output_dir))
                if final_path:
                    self.finished_signal.emit(final_path)
                else:
                    self.error.emit("Không thể merge audio segments!")
            else:
                self.error.emit("Không tạo được voice segments!")

        except Exception as e:
            self.error.emit(f"Lỗi VoiceOver: {e}")

    def _generate_edge_tts(self, processed_subs, voice_work_dir):
        """Generate voice using Edge TTS."""
        try:
            import edge_tts
        except ImportError:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'edge-tts'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            import edge_tts

        # Find voice config
        voice_choice = None
        for vc in VOICE_CONFIGS_EDGE_VI:
            if vc['label'] == self.voice_type:
                voice_choice = vc
                break
        if not voice_choice:
            voice_choice = VOICE_CONFIGS_EDGE_VI[0]

        # Compute rate once — same for every segment
        user_rate_pct = self.speech_rate - 100  # -50..+100
        rate_str = f"{'+' if user_rate_pct >= 0 else ''}{user_rate_pct}%"
        pitch = voice_choice.get('pitch', '+0Hz')

        results = [None] * len(processed_subs)
        total = len(processed_subs)
        completed = [0]
        sem = asyncio.Semaphore(_EDGE_TTS_CONCURRENCY)

        async def gen_one(i, sub):
            if not self._running:
                return
            out_path = os.path.join(voice_work_dir, f"voice_{i:04d}.mp3")
            async with sem:
                last_exc = None
                for attempt in range(_EDGE_TTS_MAX_ATTEMPTS):
                    if not self._running:
                        return
                    try:
                        communicate = edge_tts.Communicate(
                            sub['text'], voice_choice['voice'],
                            pitch=pitch, rate=rate_str)
                        await communicate.save(out_path)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                            results[i] = {
                                'audio_path': out_path,
                                'start_ms': sub['start_ms'],
                                'end_ms': sub['end_ms'],
                            }
                            last_exc = None
                            break
                    except Exception as exc:
                        last_exc = exc
                        # Edge TTS surfaces 429s as ``ClientResponseError``
                        # but the message also helps us identify them.
                        msg = str(exc).lower()
                        is_transient = (
                            '429' in msg or 'timeout' in msg
                            or 'connection' in msg or 'reset' in msg)
                        if not is_transient and attempt == 0:
                            # Permanent failure (auth, bad voice id, …).
                            break
                        if attempt < _EDGE_TTS_MAX_ATTEMPTS - 1:
                            await asyncio.sleep(
                                _EDGE_TTS_BASE_BACKOFF_S * (2 ** attempt))
                if last_exc is not None:
                    logger.debug(
                        "Edge TTS error for segment %s after %d attempts: %s",
                        i, _EDGE_TTS_MAX_ATTEMPTS, last_exc)
            completed[0] += 1
            self.progress.emit(f"Edge TTS: {completed[0]}/{total}")

        async def generate_all():
            await asyncio.gather(
                *(gen_one(i, s) for i, s in enumerate(processed_subs)),
                return_exceptions=False,
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(generate_all())
        finally:
            loop.close()

        return [r for r in results if r is not None]

    def _generate_google_tts(self, processed_subs, voice_work_dir):
        """Generate voice using Google TTS (gTTS)."""
        try:
            from gtts import gTTS
        except ImportError:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'gtts'],
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            from gtts import gTTS

        segments = []
        total = len(processed_subs)

        for i, sub in enumerate(processed_subs):
            if not self._running:
                return segments
            self.progress.emit(f"Google TTS: {i+1}/{total}")
            out_path = os.path.join(voice_work_dir, f"voice_{i:04d}.mp3")

            try:
                tts = gTTS(text=sub['text'], lang=self.target_lang)
                tts.save(out_path)

                # Pitch-preserving speed change via FFmpeg atempo
                # (the previous frame_rate hack pitched voices like a
                # chipmunk).
                if self.speech_rate != 100 and os.path.exists(out_path):
                    self._apply_atempo_inplace_mp3(
                        out_path, self.speech_rate / 100.0)

                if os.path.exists(out_path):
                    segments.append({
                        'audio_path': out_path,
                        'start_ms': sub['start_ms'],
                        'end_ms': sub['end_ms'],
                    })
            except Exception as e:
                logger.debug("Google TTS error for segment %s: %s", i, e)

        return segments

    def _apply_atempo_inplace_mp3(self, src_path, factor):
        """Apply FFmpeg ``atempo`` to ``src_path`` and overwrite it.

        ``_apply_atempo_to_wav`` produces a separate WAV intermediate
        for the merge path; this in-place variant keeps the file as
        MP3 so the rest of ``_generate_google_tts`` does not need to
        learn about a new path. Failures are logged and the original
        file is left untouched (caller continues with unmodified MP3).
        """
        chain = _atempo_chain(factor)
        if not chain:
            return
        ffmpeg = _ffmpeg_executable()
        tmp_path = src_path + '.tmp.mp3'
        cmd = [
            ffmpeg, '-y', '-loglevel', 'error', '-i', src_path,
            '-filter:a', chain,
            '-c:a', 'libmp3lame', '-q:a', '4', tmp_path,
        ]
        try:
            subprocess.run(
                cmd, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as exc:
            logger.debug("atempo in-place failed for %s: %s", src_path, exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return
        try:
            shutil.move(tmp_path, src_path)
        except OSError as exc:
            logger.debug("atempo move failed for %s: %s", src_path, exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _apply_atempo_to_wav(self, src_path, factor):
        """Apply FFmpeg ``atempo`` and write a 16-bit PCM WAV next to src.

        WAV intermediate (P2-8) avoids the double MP3 encode that the
        previous implementation paid: it decoded MP3 → applied atempo
        → re-encoded MP3, only for the merge step to decode it again.
        WAV output here is a single decode-and-resample, then the
        amix step writes one final MP3 at the end.

        Returns the new path on success, or None if FFmpeg isn't
        available / the conversion fails (caller falls back to src).
        """
        chain = _atempo_chain(factor)
        if not chain:
            return None
        ffmpeg = _ffmpeg_executable()
        wav_path = os.path.splitext(src_path)[0] + '.wav'
        cmd = [
            ffmpeg, '-y', '-loglevel', 'error', '-i', src_path,
            '-filter:a', chain,
            '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', wav_path,
        ]
        try:
            subprocess.run(
                cmd, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return wav_path
        except Exception as exc:
            logger.debug("atempo failed for %s: %s", src_path, exc)
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
            return None

    def _probe_duration_ms(self, path):
        """Return media duration in ms via ffprobe, or None on failure."""
        ffmpeg = _ffmpeg_executable()
        # ffprobe lives next to ffmpeg.
        ffprobe = ffmpeg
        if ffmpeg.lower().endswith('ffmpeg.exe'):
            ffprobe = ffmpeg[:-10] + 'ffprobe.exe'
        elif ffmpeg.lower().endswith('ffmpeg'):
            ffprobe = ffmpeg[:-6] + 'ffprobe'
        cmd = [ffprobe, '-v', 'error', '-show_entries',
               'format=duration', '-of', 'default=nw=1:nk=1', path]
        try:
            res = subprocess.run(
                cmd, check=True, capture_output=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return int(float(res.stdout.decode('utf-8', 'ignore').strip()) * 1000)
        except Exception as exc:
            logger.debug("ffprobe failed for %s: %s", path, exc)
            return None

    def _merge_segments(self, segments, output_dir):
        """Merge voice segments via FFmpeg ``adelay``+``amix``.

        Phase 2 upgrade: replaces pydub's Python-level mixing (which
        loads every clip into memory and overlays in user-space) with
        a single FFmpeg invocation that applies per-segment
        ``adelay`` and mixes all streams. This is O(total audio) at
        FFmpeg speed instead of O(n × clip-length) at Python speed.

        For very large segment counts we chunk to keep the filtergraph
        manageable: groups of ``_AMIX_CHUNK_SIZE`` are mixed to
        intermediate WAVs, then those WAVs are mixed to the final MP3.
        """
        if not segments:
            return None

        ffmpeg = _ffmpeg_executable()
        final_path = os.path.join(output_dir, 'voiceover_output.mp3')
        segments.sort(key=lambda s: s['start_ms'])

        # Optionally fit each segment to its subtitle slot before merge.
        # Using WAV intermediate avoids re-encoding to MP3 only to
        # decode again in the next step.
        prepared: list[tuple[int, str]] = []  # (start_ms, audio_path)
        for seg in segments:
            audio_path = seg['audio_path']
            slot_ms = max(seg['end_ms'] - seg['start_ms'], 1)
            if self.fit_to_subtitle:
                dur_ms = self._probe_duration_ms(audio_path)
                if dur_ms is not None and dur_ms > slot_ms * 1.05:
                    factor = dur_ms / slot_ms
                    wav = self._apply_atempo_to_wav(audio_path, factor)
                    if wav:
                        audio_path = wav
            prepared.append((seg['start_ms'], audio_path))

        if not prepared:
            return None

        # If the segment count is small enough, mix in one shot.
        if len(prepared) <= _AMIX_CHUNK_SIZE:
            return self._amix_to_file(
                prepared, final_path, output_format='mp3', is_final=True,
                ffmpeg=ffmpeg)

        # Otherwise mix chunks to intermediate WAVs, then mix those.
        chunk_dir = os.path.join(output_dir, '_voice_chunks')
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_outputs: list[tuple[int, str]] = []
        for i in range(0, len(prepared), _AMIX_CHUNK_SIZE):
            chunk = prepared[i:i + _AMIX_CHUNK_SIZE]
            chunk_path = os.path.join(chunk_dir, f"chunk_{i:05d}.wav")
            ok = self._amix_to_file(
                chunk, chunk_path, output_format='wav', is_final=False,
                ffmpeg=ffmpeg)
            if not ok:
                logger.warning("amix chunk %d failed, skipping", i)
                continue
            # Each chunk preserves the first segment's absolute offset.
            chunk_outputs.append((chunk[0][0], chunk_path))
        if not chunk_outputs:
            return None
        # Recompute relative offsets so each chunk lands at its own
        # absolute timestamp on the final timeline.
        result = self._amix_to_file(
            chunk_outputs, final_path, output_format='mp3',
            is_final=True, ffmpeg=ffmpeg)
        # Best-effort cleanup of intermediate chunks.
        try:
            for _, p in chunk_outputs:
                if os.path.exists(p):
                    os.remove(p)
            os.rmdir(chunk_dir)
        except OSError:
            pass
        return result

    def _amix_to_file(self, items, out_path, output_format,
                      is_final, ffmpeg):
        """Build an FFmpeg amix command for ``items`` and run it.

        ``items`` is a list of ``(start_ms, audio_path)``. The earliest
        start is rebased to 0 so amix only sees relative offsets.
        """
        if not items:
            return None
        base = items[0][0]
        cmd = [ffmpeg, '-y', '-loglevel', 'error']
        for _, path in items:
            cmd.extend(['-i', path])
        # Build filtergraph: [0:a]adelay=...|...[a0]; ... ; amix → out.
        filter_parts = []
        labels = []
        for idx, (start_ms, _) in enumerate(items):
            delay_ms = max(0, start_ms - base)
            label = f"a{idx}"
            # Apply delay to both channels so stereo doesn't desync.
            filter_parts.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms}[{label}]")
            labels.append(f"[{label}]")
        filter_parts.append(
            f"{''.join(labels)}amix=inputs={len(items)}:"
            "duration=longest:dropout_transition=0,"
            "volume=1.0[out]")
        cmd.extend(['-filter_complex', ';'.join(filter_parts),
                    '-map', '[out]'])
        if output_format == 'mp3':
            cmd.extend(['-c:a', 'libmp3lame', '-q:a', '4'])
        else:
            cmd.extend(['-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le'])
        cmd.append(out_path)
        try:
            subprocess.run(
                cmd, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("FFmpeg amix failed (%s) — falling back to pydub", exc)
            if is_final:
                return self._merge_with_pydub_fallback(items, out_path)
            return None
        if is_final:
            logger.debug("Merged voice via amix: %s (%d segments)",
                         out_path, len(items))
        return out_path

    def _merge_with_pydub_fallback(self, items, out_path):
        """Last-resort merge if FFmpeg amix fails on this system.

        Kept as a safety net so a broken FFmpeg filter chain doesn't
        block the user — they get the slower pydub path instead.
        """
        try:
            from pydub import AudioSegment
        except ImportError:
            self.error.emit("pydub chưa được cài đặt!")
            return None
        loaded = []
        for start_ms, path in items:
            try:
                audio = AudioSegment.from_file(path)
            except Exception as exc:  # noqa: BLE001
                logger.debug("pydub fallback skip %s: %s", path, exc)
                continue
            loaded.append((start_ms, audio))
        if not loaded:
            return None
        timeline_ms = max(s + len(a) for s, a in loaded) + 200
        combined = AudioSegment.silent(duration=timeline_ms)
        for s, a in loaded:
            combined = combined.overlay(a, position=max(0, s))
        combined.export(out_path, format='mp3')
        return out_path

    def _parse_srt_time(self, time_str):
        """Parse SRT time format (HH:MM:SS,mmm) to milliseconds."""
        time_str = time_str.strip().replace('.', ',')
        parts = time_str.split(',')
        hms = parts[0].split(':')
        hours = int(hms[0])
        minutes = int(hms[1])
        seconds = int(hms[2]) if len(hms) > 2 else 0
        millis = int(parts[1]) if len(parts) > 1 else 0
        return int(hours * 3600000 + minutes * 60000 + seconds * 1000 + millis)

    def stop(self):
        self._running = False
