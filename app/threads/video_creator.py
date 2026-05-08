"""
Video Creator Thread — FFmpeg rendering pipeline
"""
import os
import subprocess
import traceback
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from app.utils.config import FFMPEG_PATH, FFPROBE_PATH


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

    def run(self):
        try:
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
                ass_path = os.path.join(self.output_dir, '_temp_subtitle.ass')
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
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    _, stderr = proc.communicate(timeout=1800)
                    if proc.returncode == 0:
                        self.status.emit(f"✓ Encoder: {enc_desc}")
                        success = True
                        break
                    else:
                        err = stderr.decode('utf-8', errors='ignore').lower()
                        print(f"[DEBUG] {enc_desc} lỗi: {err[:300]}")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"[DEBUG] {enc_desc} timeout")
                except Exception as e:
                    print(f"[DEBUG] {enc_desc} exception: {e}")

            if not success:
                self.error.emit("❌ Không thể render video với bất kỳ codec nào!")
                return

            self.progress.emit(90)

            # ── Cleanup temp files ──
            if ass_path and os.path.exists(ass_path):
                try:
                    os.remove(ass_path)
                except Exception:
                    pass

            # ── Signal completion ──
            if os.path.exists(self.output_video):
                self.progress.emit(100)
                self.status.emit("✅ Hoàn thành!")
                self.finished_video.emit(self.output_video)
            else:
                self.error.emit("Video output file not found!")

        except Exception as e:
            traceback.print_exc()
            self.error.emit(f"Lỗi: {e}")

    def stop(self):
        self._running = False
