"""
SRT Parser Utilities
====================
Parse, group, and manipulate .srt subtitle files.
"""

import re
from typing import List, Dict


def parse_srt(srt_text: str) -> List[Dict]:
    """
    Parse SRT text into a list of subtitle entries.
    
    Each entry: {
        'index': int,
        'timeline': '00:00:01,000 --> 00:00:03,500',
        'start': '00:00:01,000',
        'end': '00:00:03,500',
        'text': 'Subtitle text here',
        'translated_text': ''
    }
    """
    entries = []
    # Normalise BOM and mixed line-endings (\r\n, \r) so the regex
    # split works reliably on Windows-edited SRT files.
    srt_text = srt_text.lstrip('\ufeff')
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue
        
        # Find timeline line (contains -->)
        timeline_idx = -1
        for i, line in enumerate(lines):
            if '-->' in line:
                timeline_idx = i
                break
        
        if timeline_idx < 0:
            continue
        
        # Parse index
        try:
            index = int(lines[0].strip()) if timeline_idx > 0 else len(entries) + 1
        except ValueError:
            index = len(entries) + 1
        
        # Parse timeline
        timeline = lines[timeline_idx].strip()
        parts = timeline.split('-->')
        start = parts[0].strip() if len(parts) > 0 else '00:00:00,000'
        end = parts[1].strip() if len(parts) > 1 else '00:00:00,000'
        
        # Parse text (everything after timeline)
        text = '\n'.join(lines[timeline_idx + 1:]).strip()
        
        entries.append({
            'index': index,
            'timeline': timeline,
            'start': start,
            'end': end,
            'text': text,
            'translated_text': ''
        })
    
    return entries


def group_srt_entries_by_chars(entries: List[Dict], max_chars: int = 500) -> List[List[Dict]]:
    """
    Group SRT entries into batches where total characters <= max_chars.
    Used for batch translation to respect API limits.
    """
    groups = []
    current_group = []
    current_chars = 0
    
    for entry in entries:
        text_len = len(entry.get('text', ''))
        if current_chars + text_len > max_chars and current_group:
            groups.append(current_group)
            current_group = []
            current_chars = 0
        current_group.append(entry)
        current_chars += text_len
    
    if current_group:
        groups.append(current_group)
    
    return groups


def parse_srt_time_to_ms(time_str: str) -> int:
    """Parse SRT time format (HH:MM:SS,mmm) to milliseconds."""
    time_str = time_str.strip().replace('.', ',')
    parts = time_str.split(',')
    hms = parts[0].split(':')
    hours = int(hms[0])
    minutes = int(hms[1])
    seconds = int(hms[2]) if len(hms) > 2 else 0
    millis = int(parts[1]) if len(parts) > 1 else 0
    return int(hours * 3600000 + minutes * 60000 + seconds * 1000 + millis)


def parse_srt_time_to_seconds(time_str: str) -> float:
    """Parse SRT time format (HH:MM:SS,mmm) to seconds (float)."""
    return parse_srt_time_to_ms(time_str) / 1000.0


def format_srt_time(ms: int) -> str:
    """Format milliseconds to SRT time format (HH:MM:SS,mmm)."""
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_time_display(ms: int) -> str:
    """Format milliseconds for display (MM:SS)."""
    total_seconds = ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def subtitles_to_srt(entries: List[Dict], use_translated: bool = False) -> str:
    """Convert subtitle entries back to SRT format string."""
    lines = []
    for i, entry in enumerate(entries, 1):
        text = entry.get('translated_text', '') if use_translated else entry.get('text', '')
        if not text:
            text = entry.get('text', '')
        lines.append(f"{i}")
        lines.append(entry['timeline'])
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)
