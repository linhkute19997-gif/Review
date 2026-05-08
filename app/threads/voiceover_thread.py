"""
Voice-Over Thread — Edge TTS, Google TTS, ElevenLabs
"""
import os
import asyncio
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from app.utils.config import BASE_DIR, VOICE_CONFIGS_EDGE_VI


class VoiceOverThread(QThread):
    progress = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, subtitles, voice_type='Alex (Nam chuẩn)',
                 speech_rate=100, volume=100, provider='Edge TTS (miễn phí)',
                 target_lang='vi'):
        super().__init__()
        self.subtitles = subtitles
        self.voice_type = voice_type
        self.speech_rate = speech_rate
        self.volume = volume
        self.provider = provider
        self.target_lang = target_lang
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

            print(f"[DEBUG] Processed {len(processed_subs)} subtitles")

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
            import subprocess, sys
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

        segments = []
        total = len(processed_subs)

        # Use a single event loop for all segments
        async def generate_all():
            for i, sub in enumerate(processed_subs):
                if not self._running:
                    return
                self.progress.emit(f"Edge TTS: {i+1}/{total}")
                out_path = os.path.join(voice_work_dir, f"voice_{i:04d}.mp3")
                try:
                    # Compute rate from user speed (100=default, 50=slow, 200=fast)
                    user_rate_pct = self.speech_rate - 100  # -50 to +100
                    rate_str = f"{'+' if user_rate_pct >= 0 else ''}{user_rate_pct}%"
                    communicate = edge_tts.Communicate(
                        sub['text'], voice_choice['voice'],
                        pitch=voice_choice.get('pitch', '+0Hz'),
                        rate=rate_str
                    )
                    await communicate.save(out_path)
                    if os.path.exists(out_path):
                        segments.append({
                            'audio_path': out_path,
                            'start_ms': sub['start_ms'],
                            'end_ms': sub['end_ms'],
                        })
                except Exception as e:
                    print(f"[DEBUG] Edge TTS error for segment {i}: {e}")

        # Run all in single loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(generate_all())
        finally:
            loop.close()

        return segments

    def _generate_google_tts(self, processed_subs, voice_work_dir):
        """Generate voice using Google TTS (gTTS)."""
        try:
            from gtts import gTTS
        except ImportError:
            import subprocess, sys
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

                # Apply speed change if user requested non-default speed
                if self.speech_rate != 100 and os.path.exists(out_path):
                    try:
                        from pydub import AudioSegment
                        audio = AudioSegment.from_mp3(out_path)
                        speed_factor = self.speech_rate / 100.0
                        # Change speed by adjusting frame rate
                        new_audio = audio._spawn(audio.raw_data, overrides={
                            'frame_rate': int(audio.frame_rate * speed_factor)
                        }).set_frame_rate(audio.frame_rate)
                        new_audio.export(out_path, format='mp3')
                    except ImportError:
                        pass  # pydub not available, skip speed change

                if os.path.exists(out_path):
                    segments.append({
                        'audio_path': out_path,
                        'start_ms': sub['start_ms'],
                        'end_ms': sub['end_ms'],
                    })
            except Exception as e:
                print(f"[DEBUG] Google TTS error for segment {i}: {e}")

        return segments

    def _merge_segments(self, segments, output_dir):
        """Merge all voice segments with exact timeline sync."""
        try:
            from pydub import AudioSegment
        except ImportError:
            self.error.emit("pydub chưa được cài đặt!")
            return None

        final_path = os.path.join(output_dir, 'voiceover_output.mp3')
        segments.sort(key=lambda s: s['start_ms'])

        if not segments:
            return None

        # Build combined audio with silence gaps
        last_end = 0
        combined = AudioSegment.silent(duration=0)

        for seg in segments:
            start_ms = seg['start_ms']
            # Add silence gap
            if start_ms > last_end:
                gap = start_ms - last_end
                combined += AudioSegment.silent(duration=gap)
            elif start_ms < last_end:
                # Segment overlaps with previous; skip gap (truncate overlap)
                pass

            # Add voice segment
            try:
                audio = AudioSegment.from_mp3(seg['audio_path'])
                combined += audio
                last_end = start_ms + len(audio)
            except Exception as e:
                print(f"[DEBUG] Error loading segment: {e}")
                last_end = seg.get('end_ms', start_ms)

        combined.export(final_path, format='mp3')
        print(f"[DEBUG] Merged voice: {final_path} ({len(combined)}ms)")
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
