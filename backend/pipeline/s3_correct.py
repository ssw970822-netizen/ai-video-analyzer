"""③ GPT 자막 교정 — 텍스트만 교정, 타임코드는 절대 변경하지 않음.

전략(타임코드 보존):
  1) original.srt 를 세그먼트로 파싱 (start/end/text).
  2) 텍스트만 번호를 매겨 GPT 에 전달, "같은 번호로 교정 텍스트만" 반환 요청.
  3) 반환 텍스트를 원본 세그먼트의 start/end 에 그대로 재결합.
  4) 세그먼트 개수/번호가 어긋나면 해당 항목은 원문 유지 (안전 폴백).
출력: corrected.srt
"""
from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

from config import GPT_MODEL, OPENAI_API_KEY

# 한 요청에 보낼 세그먼트 수 (매핑 신뢰성 + 토큰 한도 고려)
BATCH_SIZE = 40

SYSTEM_PROMPT = (
    "너는 자막 교정 전문가다. 입력으로 번호가 매겨진 자막 조각들이 JSON 으로 주어진다. "
    "각 조각의 오탈자, 띄어쓰기, 맞춤법만 자연스럽게 교정하라. "
    "규칙: (1) 의미를 바꾸거나 내용을 추가/삭제하지 마라. "
    "(2) 조각을 합치거나 나누지 마라. "
    "(3) 구두점(마침표, 쉼표, 물음표, 느낌표, 말줄임표 등)은 절대 추가/삭제/변경하지 마라. "
    "원문에 마침표가 없으면 넣지 말고, 있으면 그대로 두어라. "
    "(4) 입력의 모든 번호를 그대로 출력하라. "
    "(5) 반드시 {\"items\":[{\"id\":번호,\"text\":\"교정문장\"}, ...]} 형식의 JSON 만 출력하라."
)


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _correct_batch(client: OpenAI, batch: list[dict], model: str) -> dict[int, str]:
    """batch: [{id, text}] → {id: corrected_text}. 실패 시 빈 dict."""
    payload = {"items": [{"id": b["id"], "text": b["text"]} for b in batch]}
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    out: dict[int, str] = {}
    for item in data.get("items", []):
        try:
            out[int(item["id"])] = str(item["text"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
    return out


# 끝맺음 구두점 후보
_TRAIL_PUNCT = ".。,，?!？！…~·"


def _trailing_punct(s: str) -> str:
    s = s.rstrip()
    i = len(s)
    while i > 0 and s[i - 1] in _TRAIL_PUNCT:
        i -= 1
    return s[i:]


def _keep_original_trailing(original: str, corrected: str) -> str:
    """교정문이 원문에 없던 끝맺음 구두점을 추가했다면 원문 것으로 되돌림."""
    o_tail = _trailing_punct(original)
    c_tail = _trailing_punct(corrected)
    if o_tail == c_tail:
        return corrected
    c = corrected.rstrip()
    core = c[: len(c) - len(c_tail)]
    return core + o_tail


def correct(original_srt: Path, corrected_out: Path, model: str = GPT_MODEL) -> dict:
    """original.srt 교정 → corrected.srt. 통계 dict 반환."""
    from utils import srt as srtutil

    client = _client()
    segments = srtutil.parse_srt(original_srt.read_text(encoding="utf-8"))

    changed = 0
    corrected_segs: list[dict] = []

    # 1-based id = 세그먼트 순번
    for start_i in range(0, len(segments), BATCH_SIZE):
        chunk = segments[start_i:start_i + BATCH_SIZE]
        batch = [{"id": start_i + j + 1, "text": s["text"]} for j, s in enumerate(chunk)]
        try:
            mapping = _correct_batch(client, batch, model)
        except Exception:
            mapping = {}  # 배치 실패 → 전부 원문 유지

        for j, seg in enumerate(chunk):
            sid = start_i + j + 1
            new_text = mapping.get(sid, seg["text"])
            # 끝맺음 구두점은 원문 그대로 유지 (마침표 추가 방지)
            new_text = _keep_original_trailing(seg["text"], new_text)
            if new_text != seg["text"]:
                changed += 1
            corrected_segs.append({
                "index": sid,
                "start": seg["start"],   # ⬅ 타임코드 원본 그대로
                "end": seg["end"],       # ⬅ 타임코드 원본 그대로
                "text": new_text,
            })

    srtutil.write_srt(corrected_segs, corrected_out)

    return {
        "segment_count": len(corrected_segs),
        "changed_count": changed,
        "timecodes_preserved": len(corrected_segs) == len(segments),
    }
