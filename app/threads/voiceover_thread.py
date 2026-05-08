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
# aggressive callers, so a small pool keeps things fast without 429s.
_EDGE_TTS_CONCURRENCY = 4


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
            # Clean old files
            for f in output_dir.glob('*.mp3'):
                f.unlink()

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
                if not self._running:
                    return
                try:
                    communicate = edge_tts.Communicate(
                        sub['text'], voice_choice['voice'],
                        pitch=pitch, rate=rate_str)
                    await communicate.save(out_path)
                    if os.path.exists(out_path):
                        results[i] = {
                            'audio_path': out_path,
                            'start_ms': sub['start_ms'],
                            'end_ms': sub['end_ms'],
                        }
                except Exception as exc:
                    logger.debug("Edge TTS error for segment %s: %s", i, exc)
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
                    self._apply_atempo_inplace(
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

    def _apply_atempo_inplace(self, mp3_path, factor):
        """Re-encode mp3_path with FFmpeg atempo (preserves pitch).

        Falls back to a no-op if FFmpeg isn't available.
        """
        chain = _atempo_chain(factor)
        if not chain:
            return
        ffmpeg = _ffmpeg_executable()
        tmp_out = mp3_path + '.atempo.mp3'
        cmd = [ffmpeg, '-y', '-loglevel', 'error', '-i', mp3_path,
               '-filter:a', chain, '-c:a', 'libmp3lame', '-q:a', '4', tmp_out]
        try:
            subprocess.run(
                cmd, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            os.replace(tmp_out, mp3_path)
        except Exception as exc:
            logger.debug("atempo failed for %s: %s", mp3_path, exc)
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass

    def _merge_segments(self, segments, output_dir):
        """Merge voice segments onto a pre-allocated silent timeline.

        Two upgrades over the previous implementation:

        1. ``combined += seg`` is O(n²) because pydub copies the whole
           buffer on every concat. We instead allocate one silent
           buffer of the final length and ``overlay`` each segment at
           its own ``start_ms`` — O(total audio length).
        2. If ``fit_to_subtitle`` is on (default), we squash/stretch any
           segment that exceeds its slot via FFmpeg ``atempo`` so the
           rendered voice stays aligned with the subtitle track.
        """
        try:
            from pydub import AudioSegment
        except ImportError:
            self.error.emit("pydub chưa được cài đặt!")
            return None

        if not segments:
            return None

        final_path = os.path.join(output_dir, 'voiceover_output.mp3')
        segments.sort(key=lambda s: s['start_ms'])

        # Optionally fit each segment to its subtitle slot before merge.
        if self.fit_to_subtitle:
            for seg in segments:
                slot_ms = max(seg['end_ms'] - seg['start_ms'], 1)
                try:
                    audio = AudioSegment.from_mp3(seg['audio_path'])
                except Exception as exc:
                    logger.debug("cannot probe %s: %s", seg['audio_path'], exc)
                    continue
                # Only compress overflow — don't pad short clips with atempo
                # since silence at the end keeps the voice natural.
                if len(audio) > slot_ms * 1.05:
                    factor = len(audio) / slot_ms
                    self._apply_atempo_inplace(seg['audio_path'], factor)

        # Determine final duration: max(end_ms) plus a small tail.
        loaded = []
        for seg in segments:
            try:
                audio = AudioSegment.from_mp3(seg['audio_path'])
            except Exception as exc:
                logger.debug("Error loading segment: %s", exc)
                continue
            loaded.append((seg['start_ms'], audio))

        if not loaded:
            return None

        timeline_ms = max(start + len(a) for start, a in loaded)
        # Add a 200ms tail so the last word isn't clipped.
        timeline_ms += 200
        combined = AudioSegment.silent(duration=timeline_ms)
        for start_ms, audio in loaded:
            combined = combined.overlay(audio, position=max(0, start_ms))

        combined.export(final_path, format='mp3')
        logger.debug("Merged voice: %s (%dms)", final_path, len(combined))
        return final_path

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
