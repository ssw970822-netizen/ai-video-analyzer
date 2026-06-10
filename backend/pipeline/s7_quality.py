"""⑦ 품질 이슈 감지 — 블랙 화면 + 음성 튐(라우드니스 급변) 감지.

- 블랙 화면: ffmpeg blackdetect 필터.
- 음성 튐: ffmpeg ebur128(ITU-R BS.1770)의 순간 라우드니스(LKFS)를 분석해
  급격한 라우드니스 점프 / 과다 음량 구간 감지.
출력: {duration, black_segments[], audio_spikes[]}  (라우드니스 단위 = LKFS)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pipeline.s1_audio import get_ffmpeg_exe, probe_duration
from utils.srt import sec_to_ts

# 블랙 감지 파라미터
BLACK_MIN_DUR = 0.05    # 이 시간 이상 검은 화면만 보고
BLACK_PIX_TH = 0.10     # 픽셀이 검다고 보는 임계값

# 음성 튐 감지 파라미터 — 단위: LKFS (= LUFS, ITU-R BS.1770 라우드니스)
JUMP_LU = 6.0           # 직전(0.1s) 대비 순간 라우드니스가 이만큼(LU) 급증하면 '튐'
FLOOR_LKFS = -24.0      # 급증을 튐으로 보려면 도달 라우드니스가 이 이상
LOUD_LKFS = -6.0        # 이 이상이면 '과다 음량'(찢어짐 위험)으로 표시
SILENCE_LKFS = -70.0    # 이 이하는 무음으로 간주 (워밍업/공백)
WARMUP_SEC = 0.4        # 시작 워밍업 구간은 무시
MERGE_GAP = 0.20        # 이 간격 내 이벤트는 하나로 병합

_BLACK_RE = re.compile(
    r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)"
)
_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def detect_black(video: Path) -> list[dict]:
    """ffmpeg blackdetect 로 블랙 화면 구간 감지."""
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-i", str(video),
        "-vf", f"blackdetect=d={BLACK_MIN_DUR}:pix_th={BLACK_PIX_TH}",
        "-an", "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for m in _BLACK_RE.finditer(r.stderr or ""):
        start, end, dur = float(m.group(1)), float(m.group(2)), float(m.group(3))
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(dur, 3),
            "start_tc": sec_to_ts(start)[:8],
            "end_tc": sec_to_ts(end)[:8],
        })
    return out


def _measure_loudness(audio_wav: Path) -> list[tuple[float, float]]:
    """ffmpeg ebur128 로 순간 라우드니스(M, LKFS) 시계열을 측정. [(t, lkfs)]."""
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-i", str(audio_wav),
        "-af", "ebur128=metadata=1,ametadata=print:file=-",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    series: list[tuple[float, float]] = []
    cur_t: float | None = None
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            m = _PTS_RE.search(line)
            cur_t = float(m.group(1)) if m else None
        elif line.startswith("lavfi.r128.M=") and cur_t is not None:
            try:
                series.append((cur_t, float(line.split("=", 1)[1])))
            except ValueError:
                pass
    return series


def detect_audio_spikes(audio_wav: Path) -> list[dict]:
    """순간 라우드니스(LKFS) 급증 / 과다 음량 지점 감지."""
    if not audio_wav.exists():
        return []
    series = _measure_loudness(audio_wav)
    if len(series) < 2:
        return []

    raw_events: list[dict] = []
    for i in range(1, len(series)):
        t, m = series[i]
        _, prev = series[i - 1]
        if t < WARMUP_SEC:
            continue
        is_loud = m >= LOUD_LKFS
        is_jump = (prev > SILENCE_LKFS) and (m - prev >= JUMP_LU) and (m >= FLOOR_LKFS)
        if is_loud or is_jump:
            raw_events.append({
                "time": t,
                "lkfs": m,
                "jump_lu": (m - prev) if prev > SILENCE_LKFS else None,
                "loud": is_loud,
            })

    # 가까운 이벤트 병합 (가장 큰 라우드니스 유지)
    events: list[dict] = []
    last_t = -1e9
    for e in raw_events:
        if e["time"] - last_t <= MERGE_GAP and events:
            if e["lkfs"] > events[-1]["lkfs"]:
                events[-1]["lkfs"] = e["lkfs"]
                events[-1]["jump_lu"] = e["jump_lu"]
            events[-1]["loud"] = events[-1]["loud"] or e["loud"]
        else:
            events.append(dict(e))
        last_t = e["time"]

    for e in events:
        e["time"] = round(e["time"], 3)
        e["time_tc"] = sec_to_ts(e["time"])[:11]
        e["lkfs"] = round(e["lkfs"], 1)
        e["jump_lu"] = round(e["jump_lu"], 1) if e["jump_lu"] is not None else None
        e["type"] = "과다음량" if e["loud"] else "급증"
        e["clip"] = e["loud"]   # 프론트 호환(과다음량 강조)
    return events


def analyze_quality(video: Path, audio_wav: Path) -> dict:
    duration = probe_duration(video)
    black = detect_black(video)
    spikes = detect_audio_spikes(audio_wav)
    return {
        "duration": round(duration, 3),
        "duration_tc": sec_to_ts(duration)[:8],
        "black_segments": black,
        "black_count": len(black),
        "audio_spikes": spikes,
        "spike_count": len(spikes),
    }
