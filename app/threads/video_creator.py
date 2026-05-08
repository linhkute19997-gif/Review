"""
Video Creator Thread — FFmpeg rendering pipeline
"""
import os
import shutil
import subprocess
import tempfile
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app.utils.config import FFMPEG_PATH, FFPROBE_PATH
from app.utils.logger import get_logger

logger = get_logger('video_creator')

# Output is always ≤ input duration so 1× input size is plenty for the
# encoded result; 50% buffer covers temp ASS, indices and any inline
# transcoding that decodes the original container.
_DISK_SAFETY_FACTOR = 1.5
_DISK_MIN_FREE_BYTES = 256 * 1024 * 1024  # 256 MB minimum cushion


class VideoCreatorThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    finished_video = pyqtSignal(str)

    def __init__(self, input_video, output_video, config, subtitles=None,
                 output_dir=None, overlays=None):
        super().__init__()
        self.input_video = input_video
        self.output_video = output_video
        self.config = config
        self.subtitles = subtitles or []
        self.output_dir = output_dir or os.path.dirname(output_video)
        self.overlays = overlays or {}
        self._running = True

    def _build_universal_encoder_list(self, gpu_device='auto'):
        """Build encoder fallback chain: GPU → CPU."""
        encoders = []
        if gpu_device in ('auto', 'nvidia'):
            encoders.extend([('h264_nvenc', 'NVIDIA H.264'), ('hevc_nvenc', 'NVIDIA H.265')])
        if gpu_device in ('auto', 'amd'):
            encoders.extend([('h264_amf', 'AMD H.264'), ('hevc_amf', 'AMD H.265')])
        if gpu_device in ('auto', 'intel'):
            encoders.extend([('h264_qsv', 'Intel H.264'), ('hevc_qsv', 'Intel H.265')])
        encoders.extend([('libx264', 'CPU H.264'), ('libx265', 'CPU H.265'),
                         ('libvpx-vp9', 'CPU VP9')])
        return encoders

    def _probe_video_dimensions(self, video_path):
        """Return (width, height) for a video file."""
        ffprobe = FFPROBE_PATH if os.path.exists(FFPROBE_PATH) else 'ffprobe'
        try:
            cmd = [ffprobe, '-v', 'error', '-select_streams', 'v:0',
                   '-show_entries', 'stream=width,height',
                   '-of', 'default=noprint_wrappers=1:nokey=1:noprint_section_header=1',
                   str(video_path)]
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            lines = result.stdout.strip().split('\n')
            return int(lines[0]), int(lines[1])
        except Exception:
            return 1920, 1080

    def _probe_video_duration_us(self, video_path):
        """Return container duration in microseconds (or 0 if probe fails)."""
        ffprobe = FFPROBE_PATH if os.path.exists(FFPROBE_PATH) else 'ffprobe'
        try:
            cmd = [ffprobe, '-v', 'error', '-show_entries',
                   'format=duration', '-of',
                   'default=noprint_wrappers=1:nokey=1', str(video_path)]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            seconds = float(result.stdout.strip() or 0)
            return int(seconds * 1_000_000)
        except Exception:
            return 0

    def _run_ffmpeg_with_progress(self, cmd, total_us, progress_lo=20,
                                   progress_hi=90, timeout=1800):
        """Run an FFmpeg command and emit real progress percentages.

        FFmpeg's ``-progress pipe:1`` emits ``key=value`` lines on stdout
        every second; ``out_time_us`` lets us compute real %
        completion. We map [0..total_us] linearly into
        [progress_lo..progress_hi] so this slot integrates with the
        existing 0/5/20/90/100 milestones used elsewhere in run().

        Returns ``(returncode, stderr_bytes)``.
        """
        # Inject -progress + -nostats right after the program name.
        cmd = list(cmd)
        # Insert just before the input list (after global flags) — placing
        # it at index 1 (right after the binary path) is safe.
        cmd[1:1] = ['-progress', 'pipe:1', '-nostats']

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        stderr_chunks = []

        def drain_stderr():
            try:
                for chunk in iter(lambda: proc.stderr.read(4096), b''):
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
            except Exception:
                pass

        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        last_pct = progress_lo
        try:
            for raw in iter(proc.stdout.readline, b''):
                if not self._running:
                    proc.kill()
                    break
                line = raw.decode('utf-8', errors='ignore').strip()
                if not line or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                if key == 'out_time_us' and total_us > 0:
                    try:
                        cur_us = max(0, int(value))
                    except ValueError:
                        continue
                    pct = progress_lo + int(
                        (cur_us / total_us) * (progress_hi - progress_lo))
                    pct = max(progress_lo, min(progress_hi, pct))
                    if pct != last_pct:
                        last_pct = pct
                        self.progress.emit(pct)
                elif key == 'progress' and value == 'end':
                    self.progress.emit(progress_hi)
        except Exception as exc:
            print(f"[DEBUG] progress reader error: {exc}")

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            t.join(timeout=2)
            return -1, b''.join(stderr_chunks)
        t.join(timeout=2)
        return proc.returncode, b''.join(stderr_chunks)

    def _escape_drawtext_text(self, text):
        """Escape text for FFmpeg drawtext filter."""
        for ch in ["'", ":", "\\", "[", "]"]:
            text = str(text).replace(ch, f"\\{ch}")
        return text

    def _generate_ass_subtitle(self, subtitles, config, output_path):
        """Generate .ASS subtitle file from subtitle entries."""
        font_size = config.get('text_subtitle_size', 20)
        font_color_hex = config.get('text_subtitle_color', '#ffffff').lstrip('#')
        # ASS color format: &HAABBGGRR (alpha, BGR reversed)
        r, g, b = font_color_hex[:2], font_color_hex[2:4], font_color_hex[4:6]
        opacity = config.get('text_subtitle_opacity', 100)
        alpha = int((100 - opacity) * 255 / 100)  # ASS: 0=opaque, 255=transparent
        ass_color = f"&H{alpha:02X}{b}{g}{r}"

        bg_enabled = config.get('text_subtitle_bg_enabled', False)
        bg_opacity = config.get('text_subtitle_bg_opacity', 80)
        border_style = 3 if bg_enabled else 1  # 3 = opaque box

        y_percent = config.get('text_subtitle_y', 90)
        margin_v = max(10, int((100 - y_percent) * 10.8))

        ass_content = f"""[Script Info]
Title: ReviewPhimPro Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{ass_color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,{border_style},2,0,2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        for sub in subtitles:
            text = sub.get('translated_text', '') or sub.get('text', '')
            if not text.strip():
                continue
            start = self._srt_time_to_ass(sub.get('start', '00:00:00,000'))
            end = self._srt_time_to_ass(sub.get('end', '00:00:00,000'))
            # Escape ASS special chars
            text = text.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
            text = text.replace('\n', '\\N')
            ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_content)
        return output_path

    def _srt_time_to_ass(self, srt_time):
        """Convert SRT time (00:01:23,456) to ASS time (0:01:23.46)."""
        srt_time = srt_time.strip().replace('.', ',')
        parts = srt_time.split(',')
        hms = parts[0]
        ms = parts[1] if len(parts) > 1 else '000'
        cs = ms[:2]  # centiseconds
        h, m, s = hms.split(':')
        return f"{int(h)}:{m}:{s}.{cs}"

    def _check_disk_space(self) -> bool:
        """Validate that enough free space exists in the output directory.

        Emits ``self.error`` and returns ``False`` when the available
        free space is below ``input_size * _DISK_SAFETY_FACTOR`` or
        ``_DISK_MIN_FREE_BYTES``, whichever is larger.
        """
        try:
            input_size = os.path.getsize(self.input_video)
        except OSError as exc:
            logger.warning("Could not stat input video %s: %s",
                           self.input_video, exc)
            input_size = 0
        required = max(int(input_size * _DISK_SAFETY_FACTOR),
                       _DISK_MIN_FREE_BYTES)
        try:
            usage = shutil.disk_usage(self.output_dir or '.')
        except OSError as exc:
            logger.warning("Could not check disk usage: %s", exc)
            return True  # fail-open rather than blocking the user
        if usage.free < required:
            free_mb = usage.free / (1024 * 1024)
            need_mb = required / (1024 * 1024)
            self.error.emit(
                "❌ Không đủ dung lượng đĩa: cần ~%d MB, còn %d MB. "
                "Hãy giải phóng dung lượng và thử lại."
                % (int(need_mb), int(free_mb)))
            return False
        return True

    def run(self):
        # Per-job scratch dir keeps ASS / temp files unique even when
        # multiple renders share an output_dir (batch mode).
        temp_dir = tempfile.mkdtemp(prefix='rpp-render-')
        logger.debug("Render scratch dir: %s", temp_dir)
        try:
            if not self._check_disk_space():
                return

            self.status.emit("Đang chuẩn bị...")
            self.progress.emit(5)

            ffmpeg = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else 'ffmpeg'
            filters = []
            audio_filters = []
            config = self.config
            extra_inputs = []
            complex_filter_parts = []
            use_complex = False

            # ── Resolution scaling ──
            w, h = self._probe_video_dimensions(self.input_video)
            if config.get('resolution_4k'):
                if w < 3840:
                    orient = 'portrait' if h > w else 'landscape'
                    scale = '3840:-2' if orient == 'landscape' else '-2:2160'
                    filters.append(f'scale={scale}:force_original_aspect_ratio=decrease')
                    self.status.emit(f"🎬 Xuất video 4K ({w}x{h})")
            elif config.get('resolution_1080p'):
                if w < 1920:
                    orient = 'portrait' if h > w else 'landscape'
                    scale = '1920:-2' if orient == 'landscape' else '-2:1080'
                    filters.append(f'scale={scale}:force_original_aspect_ratio=decrease')
                    self.status.emit(f"🎬 Xuất video 1080P ({w}x{h})")

            # ── Video effects ──
            if config.get('flip_horizontal'):
                filters.append('hflip')
                self.status.emit("Áp dụng lật ngang...")

            if config.get('zoom_enabled'):
                filters.append('scale=iw*1.1:ih*1.1,crop=iw/1.1:ih/1.1')

            if config.get('dynamic_zoom_enabled'):
                zv = config.get('zoom_value', 5) / 100
                zi = config.get('zoom_interval', 10)
                filters.append(f"zoompan=z='1+{zv}*sin(2*PI*t/{zi})':d=1:s={w}x{h}")

            # ── Scan lines ──
            line_mode = config.get('line_mode', 0)
            if line_mode == 1:
                filters.append('drawbox=x=0:y=ih/2:w=iw:h=2:c=white@0.3:t=fill')
            elif line_mode == 2:
                filters.append('drawbox=x=iw/2:y=0:w=2:h=ih:c=white@0.3:t=fill')
            elif line_mode == 3:  # Random
                filters.append('drawbox=x=0:y=mod(t*50\\,ih):w=iw:h=1:c=white@0.2:t=fill')

            # ── Borders ──
            if config.get('top_border_enabled'):
                bh = config.get('top_border_height', 40)
                bc = config.get('top_border_color', '#ffff00').lstrip('#')
                filters.append(f"drawbox=x=0:y=0:w=iw:h={bh}:c=0x{bc}:t=fill")
                bt = config.get('top_border_text', '')
                if bt:
                    tc = config.get('top_text_color', '#000000').lstrip('#')
                    bt_esc = self._escape_drawtext_text(bt)
                    filters.append(f"drawtext=text='{bt_esc}':x=(w-text_w)/2:y={bh//4}:fontcolor=0x{tc}:fontsize=16")

            if config.get('bot_border_enabled'):
                bh = config.get('bot_border_height', 40)
                bc = config.get('bot_border_color', '#000000').lstrip('#')
                filters.append(f"drawbox=x=0:y=ih-{bh}:w=iw:h={bh}:c=0x{bc}:t=fill")
                bt = config.get('bot_border_text', '')
                if bt:
                    tc = config.get('bot_text_color', '#ffffff').lstrip('#')
                    bt_esc = self._escape_drawtext_text(bt)
                    filters.append(f"drawtext=text='{bt_esc}':x=(w-text_w)/2:y=h-{bh*3//4}:fontcolor=0x{tc}:fontsize=16")

            # ── ASS Subtitle burn ──
            ass_path = None
            if self.subtitles and config.get('text_subtitle_enabled', True):
                self.status.emit("Tạo phụ đề ASS...")
                ass_path = os.path.join(temp_dir, 'subtitle.ass')
                self._generate_ass_subtitle(self.subtitles, config, ass_path)
                # Escape path for FFmpeg (Windows backslash → forward slash, escape colon)
                ass_escaped = ass_path.replace('\\', '/').replace(':', '\\:')
                filters.append(f"ass='{ass_escaped}'")

            # ── Blur overlays (requires complex filter) ──
            blur_regions = self.overlays.get('blurs', [])
            if blur_regions:
                use_complex = True
                # Build sequential blur chain
                prev_label = '[0:v]'
                vf_pre = ','.join(filters) if filters else 'null'
                complex_filter_parts.append(f"{prev_label}{vf_pre}[vbase]")
                prev_label = '[vbase]'
                for i, blur in enumerate(blur_regions):
                    bx, by = int(blur.get('x', 0)), int(blur.get('y', 0))
                    bw, bh_v = int(blur.get('width', 100)), int(blur.get('height', 80))
                    bs = int(blur.get('strength', 15))
                    out_label = f'[vblur{i}]'
                    complex_filter_parts.append(
                        f"{prev_label}split[main{i}][blur{i}];"
                        f"[blur{i}]crop={bw}:{bh_v}:{bx}:{by},boxblur={bs}[blurred{i}];"
                        f"[main{i}][blurred{i}]overlay={bx}:{by}{out_label}")
                    prev_label = out_label
                # Rename final label to [vout]
                complex_filter_parts[-1] = complex_filter_parts[-1].replace(
                    out_label, '[vout]')
                filters = []  # Already in complex filter

            # ── Text overlays (drawtext from scene) — scaled to video resolution ──
            text_overlays = self.overlays.get('texts', [])
            preview_w = max(self.overlays.get('preview_width', 1), 1)
            preview_h = max(self.overlays.get('preview_height', 1), 1)
            scale_x = w / preview_w
            scale_y = h / preview_h

            text_drawtext_filters = []
            for txt_ov in text_overlays:
                ov_text = self._escape_drawtext_text(txt_ov.get('text', ''))
                ov_x = int(txt_ov.get('x', 0) * scale_x)
                ov_y = int(txt_ov.get('y', 0) * scale_y)
                ov_size = int(txt_ov.get('font_size', 20) * min(scale_x, scale_y))
                ov_color = txt_ov.get('color', '#ffffff').lstrip('#')
                text_drawtext_filters.append(
                    f"drawtext=text='{ov_text}':x={ov_x}:y={ov_y}"
                    f":fontcolor=0x{ov_color}:fontsize={ov_size}"
                    f":borderw=2:bordercolor=black@0.5")

            if use_complex and text_drawtext_filters:
                # Append text drawtext to the complex filter chain (after blur)
                has_vout = any('[vout]' in p for p in complex_filter_parts)
                if has_vout:
                    for pi in range(len(complex_filter_parts)):
                        complex_filter_parts[pi] = complex_filter_parts[pi].replace('[vout]', '[vtextpre]')
                    complex_filter_parts.append(
                        f"[vtextpre]{','.join(text_drawtext_filters)}[vout]")
                else:
                    # No blur chain yet, build from [0:v]
                    vf_pre = ','.join(filters) if filters else 'null'
                    complex_filter_parts.append(
                        f"[0:v]{vf_pre},{','.join(text_drawtext_filters)}[vout]")
                    filters = []
            elif text_drawtext_filters:
                # Simple mode — just add to filters
                filters.extend(text_drawtext_filters)

            # ── Logo overlay ──
            logo_path = config.get('logo_path', '')
            if logo_path and os.path.exists(logo_path):
                extra_inputs.extend(['-i', logo_path])
                use_complex = True

            self.progress.emit(20)
            self.status.emit("Render video...")

            # Probe input duration once — we'll use it to translate
            # FFmpeg's -progress out_time_us into real %.
            total_us = self._probe_video_duration_us(self.input_video)

            # ── Build FFmpeg command ──
            gpu_device = config.get('gpu_device', 'auto')
            encoders = self._build_universal_encoder_list(gpu_device)

            # ── Audio configuration ──
            audio_mode = config.get('audio_mode', 1)
            inputs_list = ['-i', self.input_video]
            audio_args = []
            audio_input_count = 1  # index 0 = main video

            # Voice file
            voice_path = config.get('voice_file_path', '')
            has_voice = config.get('voice_file_enabled') and voice_path and os.path.exists(voice_path)
            if has_voice:
                inputs_list.extend(['-i', voice_path])
                audio_input_count += 1

            # BG Music
            bg_music_path = config.get('bg_music_path', '')
            has_bg_music = config.get('bg_music_enabled') and bg_music_path and os.path.exists(bg_music_path)
            if has_bg_music:
                inputs_list.extend(['-i', bg_music_path])
                audio_input_count += 1

            # Logo input
            inputs_list.extend(extra_inputs)

            if audio_mode == 0:  # Mute
                audio_args = ['-an']
            elif audio_mode == 2:  # Conditional: keep original ONLY when no voice
                if has_voice:
                    # Mix voice + bg_music (NO original audio)
                    use_complex = True
                    amix_parts = []
                    mix_labels = []
                    # Voice is always input 1 when present
                    amix_parts.append("[1:a]volume=1.0[av]")
                    mix_labels.append("[av]")
                    if has_bg_music:
                        bg_idx = 2
                        bg_vol = config.get('bg_music_volume', 30) / 100.0
                        amix_parts.append(
                            f"[{bg_idx}:a]volume={bg_vol}[abg]")
                        mix_labels.append("[abg]")
                    if len(mix_labels) > 1:
                        amix_parts.append(
                            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
                            f":duration=first[aout]")
                    else:
                        # Only voice: passthrough to [aout] without amix
                        amix_parts.append(f"{mix_labels[0]}anull[aout]")
                    complex_filter_parts.extend(amix_parts)
                else:
                    # No voice → keep original audio (same as mode 1)
                    vol = config.get('orig_volume', 100) / 100.0
                    if vol != 1.0:
                        audio_filters.append(f"volume={vol}")
            elif audio_input_count > 1:
                # Complex audio mixing (mode 1 with multiple inputs)
                use_complex = True
                vol = config.get('orig_volume', 100) / 100.0
                amix_parts = []
                idx = 0
                amix_parts.append(f"[0:a]volume={vol}[a0]")
                mix_labels = ["[a0]"]

                if has_voice:
                    idx += 1
                    amix_parts.append(f"[{idx}:a]volume=1.0[av]")
                    mix_labels.append("[av]")

                if has_bg_music:
                    idx += 1
                    bg_vol = config.get('bg_music_volume', 30) / 100.0
                    amix_parts.append(f"[{idx}:a]volume={bg_vol}[abg]")
                    mix_labels.append("[abg]")

                amix_parts.append(
                    f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
                    f":duration=first[aout]")
                complex_filter_parts.extend(amix_parts)
            else:
                vol = config.get('orig_volume', 100) / 100.0
                if vol != 1.0:
                    audio_filters.append(f"volume={vol}")

            vf = ','.join(filters) if filters else None
            af = ','.join(audio_filters) if audio_filters else None

            # ── Logo overlay (complex filter) ──
            logo_idx = audio_input_count if (logo_path and os.path.exists(logo_path)) else -1
            if logo_idx > 0:
                use_complex = True
                has_blur_vout = any('[vout]' in p for p in complex_filter_parts)
                if has_blur_vout:
                    # Blur chain already produced [vout], rename it and chain logo
                    for pi in range(len(complex_filter_parts)):
                        complex_filter_parts[pi] = complex_filter_parts[pi].replace('[vout]', '[vblurfinal]')
                    complex_filter_parts.append(
                        f"[{logo_idx}:v]scale=100:-1[logo];"
                        f"[vblurfinal][logo]overlay=W-w-10:10[vout]")
                else:
                    complex_filter_parts.append(
                        f"[{logo_idx}:v]scale=100:-1[logo];"
                        f"[0:v]{','.join(filters) if filters else 'null'}[vbase];"
                        f"[vbase][logo]overlay=W-w-10:10[vout]")
                vf = None  # Already in complex filter

            # Try each encoder
            success = False
            for enc_name, enc_desc in encoders:
                if not self._running:
                    self.status.emit("Đã hủy")
                    return

                self.status.emit(f"Đang thử: {enc_desc}")
                cmd = [ffmpeg, '-y', '-loglevel', 'warning', '-hide_banner']
                cmd.extend(inputs_list)

                if use_complex and complex_filter_parts:
                    cmd.extend(['-filter_complex', ';'.join(complex_filter_parts)])
                    # Map video output if complex filter produced [vout]
                    has_vout = any('[vout]' in p for p in complex_filter_parts)
                    if has_vout:
                        cmd.extend(['-map', '[vout]'])
                    if audio_input_count > 1 and audio_mode != 0:
                        cmd.extend(['-map', '[aout]'])
                    elif audio_mode != 0:
                        cmd.extend(['-map', '0:a?'])
                else:
                    if vf:
                        cmd.extend(['-vf', vf])
                    if af:
                        cmd.extend(['-af', af])

                cmd.extend(['-c:v', enc_name])
                # Audio codec
                if audio_args:
                    cmd.extend(audio_args)
                elif not (use_complex and audio_input_count > 1 and audio_mode != 0):
                    cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
                cmd.extend(['-shortest', self.output_video])

                try:
                    rc, stderr = self._run_ffmpeg_with_progress(
                        cmd, total_us=total_us,
                        progress_lo=20, progress_hi=90, timeout=1800)
                    if rc == 0:
                        self.status.emit(f"✓ Encoder: {enc_desc}")
                        success = True
                        break
                    elif rc == -1:
                        logger.debug("%s timeout", enc_desc)
                    else:
                        err = stderr.decode('utf-8', errors='ignore').lower()
                        logger.debug("%s failed: %s", enc_desc, err[:300])
                except Exception as e:
                    logger.debug("%s exception: %s", enc_desc, e)

            if not success:
                self.error.emit("❌ Không thể render video với bất kỳ codec nào!")
                return

            self.progress.emit(90)

            # ── Signal completion ──
            if os.path.exists(self.output_video):
                self.progress.emit(100)
                self.status.emit("✅ Hoàn thành!")
                self.finished_video.emit(self.output_video)
            else:
                self.error.emit("Video output file not found!")

        except Exception as e:
            logger.exception("VideoCreator failed: %s", e)
            self.error.emit(f"Lỗi: {e}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def stop(self):
        self._running = False
