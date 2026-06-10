"""② Whisper 자막 — 로컬 faster-whisper 전사 (API 불필요, 무료).

- ctranslate2 기반 로컬 추론. 첫 실행 시 모델을 자동 다운로드.
- 세그먼트(start/end/text) 추출 → 타임코드 보존.
출력: original.srt
"""
from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel

# 로드한 모델 캐시 (size -> WhisperModel)
_MODELS: dict[str, WhisperModel] = {}


def _get_model(size: str) -> WhisperModel:
    if size not in _MODELS:
        # CPU + int8: GPU 없이도 동작. (GPU 환경이면 device="cuda", compute_type="float16")
        _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODELS[size]


def transcribe_local(audio_path: Path, srt_out: Path, size: str = "small",
                     language: str | None = None) -> list[dict]:
    """로컬 faster-whisper 로 전사 → original.srt 저장, 세그먼트 반환."""
    from utils import srt as srtutil

    model = _get_model(size)
    seg_iter, _info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,          # 무음 구간 제거로 환각 감소
        language=language,        # None=자동 감지
    )

    segments: list[dict] = []
    for i, seg in enumerate(seg_iter, start=1):
        segments.append({
            "index": i,
            "start": float(seg.start),
            "end": float(seg.end),
            "text": (seg.text or "").strip(),
        })

    if not segments:
        raise RuntimeError("로컬 Whisper 전사 결과가 비었습니다 (무음이거나 인식 실패).")

    srtutil.write_srt(segments, srt_out)
    return segments
