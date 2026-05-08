"""
Encoder Detector — Detect available GPU/CPU encoders for FFmpeg.
"""
import subprocess
import json
import os
import sys
from pathlib import Path
from app.utils.config import FFMPEG_PATH, BASE_DIR


class EncoderDetector:
    """Detect and cache available FFmpeg encoders."""

    CACHE_FILE = str(BASE_DIR / 'encoder_cache.json')

    ALL_ENCODERS = [
        # NVIDIA
        ('h264_nvenc', 'NVIDIA H.264 (NVENC)'),
        ('hevc_nvenc', 'NVIDIA H.265 (NVENC)'),
        # AMD
        ('h264_amf', 'AMD H.264 (AMF)'),
        ('hevc_amf', 'AMD H.265 (AMF)'),
        # Intel
        ('h264_qsv', 'Intel H.264 (QSV)'),
        ('hevc_qsv', 'Intel H.265 (QSV)'),
        # CPU
        ('libx264', 'CPU H.264'),
        ('libx265', 'CPU H.265'),
        ('libvpx-vp9', 'CPU VP9'),
    ]

    def __init__(self):
        self._cache = None

    def detect_available_encoders(self):
        """Test all encoders and return list of working ones."""
        if self._cache is not None:
            return self._cache

        # Try to load from cache file
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, 'r') as f:
                    self._cache = json.load(f)
                return self._cache
            except Exception:
                pass

        available = []
        for enc_name, enc_desc in self.ALL_ENCODERS:
            if self._test_encoder(enc_name):
                available.append({'name': enc_name, 'description': enc_desc})
                print(f"[DEBUG] ✓ Encoder available: {enc_desc}")
            else:
                print(f"[DEBUG] ✗ Encoder not available: {enc_desc}")

        self._cache = available
        self._save_cache(available)
        return available

    def _test_encoder(self, encoder_name):
        """Test if an encoder is available by trying a minimal encode."""
        ffmpeg = FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else 'ffmpeg'
        try:
            cmd = [
                ffmpeg, '-y', '-f', 'lavfi', '-i',
                'color=c=black:s=64x64:d=0.1',
                '-c:v', encoder_name, '-f', 'null', '-'
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return False

    def _save_cache(self, available):
        try:
            with open(self.CACHE_FILE, 'w') as f:
                json.dump(available, f, indent=2)
        except Exception:
            pass

    def clear_cache(self):
        self._cache = None
        if os.path.exists(self.CACHE_FILE):
            os.remove(self.CACHE_FILE)

    def get_recommended_encoder(self):
        """Get the best available encoder."""
        available = self.detect_available_encoders()
        if available:
            return available[0]
        return {'name': 'libx264', 'description': 'CPU H.264 (fallback)'}

    def get_system_info(self):
        """Get system info string."""
        available = self.detect_available_encoders()
        info_lines = [f"FFmpeg Encoders ({len(available)} available):"]
        for enc in available:
            info_lines.append(f"  ✓ {enc['description']}")
        rec = self.get_recommended_encoder()
        info_lines.append(f"\nRecommended: {rec['description']}")
        return '\n'.join(info_lines)
