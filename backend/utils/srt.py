"""SRT 파싱/직렬화 유틸 — 타임코드 보존이 핵심.

세그먼트 표현: {"index": int, "start": float(초), "end": float(초), "text": str}
"""
from __future__ import annotations

import re
from pathlib import Path

_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def sec_to_ts(sec: float) -> str:
    """초 → 'HH:MM:SS,mmm'."""
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_to_sec(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def segments_to_srt(segments: list[dict]) -> str:
    """세그먼트 리스트 → SRT 문자열."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        blocks.append(
            f"{i}\n"
            f"{sec_to_ts(seg['start'])} --> {sec_to_ts(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )
    return "\n".join(blocks)


def parse_srt(text: str) -> list[dict]:
    """SRT 문자열 → 세그먼트 리스트."""
    segments: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip() != ""]
        if not lines:
            continue
        # 인덱스 줄(숫자) 건너뛰기
        idx_line = 0
        if lines[0].strip().isdigit():
            idx_line = 1
        if idx_line >= len(lines):
            continue
        m = _TS.search(lines[idx_line])
        if not m:
            continue
        start = ts_to_sec(*m.groups()[0:4])
        end = ts_to_sec(*m.groups()[4:8])
        body = " ".join(lines[idx_line + 1:]).strip()
        segments.append({
            "index": len(segments) + 1,
            "start": start,
            "end": end,
            "text": body,
        })
    return segments


def write_srt(segments: list[dict], path: Path) -> Path:
    path.write_text(segments_to_srt(segments), encoding="utf-8")
    return path
