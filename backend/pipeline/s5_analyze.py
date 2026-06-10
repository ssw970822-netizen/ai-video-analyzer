"""⑤ 장면별 GPT 분석 — 각 장면의 썸네일 + 구간 자막을 GPT 멀티모달로 분석.

- 장면 구간에 겹치는 교정 자막을 모음.
- 썸네일이 있으면 이미지+자막, 없으면 자막만으로 분석.
- 각 장면: {summary, keywords, mood} 구조화 결과.
출력: 분석이 포함된 장면 리스트.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from openai import OpenAI

from config import GPT_MODEL, OPENAI_API_KEY

SYSTEM_PROMPT = (
    "너는 영상 장면 분석가다. 한 장면의 대표 이미지(있을 수 있음)와 그 구간의 자막이 주어진다. "
    "장면을 분석해 반드시 아래 JSON 만 출력하라: "
    "{\"summary\":\"이 장면을 1~2문장으로 요약\", "
    "\"keywords\":[\"핵심키워드\", ...최대5개], "
    "\"mood\":\"장면의 분위기를 한 단어로\"}. "
    "자막이 비어 있으면 이미지만으로 판단하라."
)


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _subtitles_in_range(segments: list[dict], start: float, end: float) -> list[dict]:
    """[start, end] 구간에 겹치는 자막 세그먼트."""
    return [
        s for s in segments
        if s["start"] < end and s["end"] > start
    ]


def _img_data_uri(path: Path) -> str:
    b = path.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")


def _analyze_scene(client: OpenAI, scene: dict, subs_text: str, work_dir: Path, model: str) -> dict:
    content: list[dict] = [{
        "type": "text",
        "text": f"장면 #{scene['scene_no']} ({scene['start_tc']} ~ {scene['end_tc']})\n"
                f"자막:\n{subs_text or '(자막 없음)'}",
    }]
    thumb = scene.get("thumbnail")
    if thumb:
        tpath = work_dir / thumb
        if tpath.exists():
            content.append({
                "type": "image_url",
                "image_url": {"url": _img_data_uri(tpath)},
            })

    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {
        "summary": str(data.get("summary", "")).strip(),
        "keywords": [str(k) for k in (data.get("keywords") or [])][:5],
        "mood": str(data.get("mood", "")).strip(),
    }


def analyze_scenes(scenes: list[dict], corrected_srt: Path, work_dir: Path,
                   model: str = GPT_MODEL) -> list[dict]:
    from utils import srt as srtutil

    client = _client()
    segments = (
        srtutil.parse_srt(corrected_srt.read_text(encoding="utf-8"))
        if corrected_srt.exists() else []
    )

    out: list[dict] = []
    for scene in scenes:
        subs = _subtitles_in_range(segments, scene["start"], scene["end"])
        subs_text = "\n".join(s["text"] for s in subs)
        try:
            analysis = _analyze_scene(client, scene, subs_text, work_dir, model)
        except Exception as e:  # noqa: BLE001
            analysis = {"summary": f"(분석 실패: {e})", "keywords": [], "mood": ""}
        out.append({
            **scene,
            "subtitle_text": subs_text,
            "subtitle_count": len(subs),
            "analysis": analysis,
        })
    return out
