"""⑥ 메타데이터 통합 — 장면 타임코드 + GPT 분석 + 구간 자막을 scenes.json 으로 결합.

타임코드는 ④/③에서 온 값을 그대로 사용한다 (재계산·수정 없음).
출력: scenes.json
"""
from __future__ import annotations

import json
from pathlib import Path

from utils.srt import sec_to_ts


def _subtitles_in_range(segments: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for s in segments:
        if s["start"] < end and s["end"] > start:
            out.append({
                "start": sec_to_ts(s["start"]),  # ⬅ 원본 타임코드 그대로
                "end": sec_to_ts(s["end"]),
                "text": s["text"],
            })
    return out


def merge(
    analyzed_scenes: list[dict],
    corrected_srt: Path,
    video_name: str,
    duration_sec: float,
    out_path: Path,
) -> dict:
    from utils import srt as srtutil

    segments = (
        srtutil.parse_srt(corrected_srt.read_text(encoding="utf-8"))
        if corrected_srt.exists() else []
    )

    scenes_out = []
    for sc in analyzed_scenes:
        analysis = sc.get("analysis", {})
        scenes_out.append({
            "scene_no": sc["scene_no"],
            "start": sc["start_tc"],          # ④ 장면 타임코드 그대로
            "end": sc["end_tc"],
            "summary": analysis.get("summary", ""),
            "keywords": analysis.get("keywords", []),
            "mood": analysis.get("mood", ""),
            "subtitles": _subtitles_in_range(segments, sc["start"], sc["end"]),
        })

    metadata = {
        "video": video_name,
        "duration": sec_to_ts(duration_sec),
        "scene_count": len(scenes_out),
        "subtitle_count": len(segments),
        "scenes": scenes_out,
    }
    out_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata
