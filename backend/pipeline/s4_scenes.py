"""④ 장면 감지 — PySceneDetect(ContentDetector)로 컷 감지 + 대표 프레임 추출.

- 각 장면의 시작/끝 타임코드(초) 추출.
- 장면 중간 지점 프레임 1장을 ffmpeg로 추출 → scene_XXX.jpg.
- 출력: 장면 리스트 [{scene_no, start, end, start_tc, end_tc, thumbnail}]
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scenedetect import AdaptiveDetector, ContentDetector, ThresholdDetector, detect

from config import SCENE_THRESHOLD
from pipeline.s1_audio import get_ffmpeg_exe
from utils.srt import sec_to_ts


def _make_detector(name: str, threshold: float):
    """이름으로 PySceneDetect 감지기 생성."""
    if name == "AdaptiveDetector":
        return AdaptiveDetector()
    if name == "ThresholdDetector":
        return ThresholdDetector()
    return ContentDetector(threshold=threshold)   # 기본


def _extract_frame(video: Path, time_sec: float, out: Path) -> bool:
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-ss", f"{time_sec:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "3",
        "-vf", "scale=320:-1",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and out.exists()


def detect_scenes(video: Path, work_dir: Path, detector: str = "ContentDetector") -> list[dict]:
    """장면 감지 + 썸네일 추출 → 장면 리스트 반환."""
    scene_list = detect(str(video), _make_detector(detector, SCENE_THRESHOLD))

    scenes: list[dict] = []
    # 장면이 하나도 안 잡히면 전체를 1개 장면으로 처리
    if not scene_list:
        from pipeline.s1_audio import probe_duration
        dur = probe_duration(video)
        scene_list = [(0.0, dur)]
        pairs = [(0.0, dur)]
    else:
        pairs = [(s.get_seconds(), e.get_seconds()) for s, e in scene_list]

    for i, (start, end) in enumerate(pairs, start=1):
        mid = start + (end - start) / 2 if end > start else start
        thumb = work_dir / f"scene_{i:03d}.jpg"
        ok = _extract_frame(video, mid, thumb)
        scenes.append({
            "scene_no": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_tc": sec_to_ts(start),
            "end_tc": sec_to_ts(end),
            "duration": round(end - start, 3),
            "thumbnail": thumb.name if ok else None,
        })
    return scenes
