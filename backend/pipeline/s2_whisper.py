"""② Whisper 자막 생성 — OpenAI Whisper API로 타임코드 포함 자막 생성.

- audio.wav 를 전사하여 세그먼트(start/end/text) 추출.
- 25MB 초과 시 ffmpeg로 구간 분할 후 각 조각 전사 → 오프셋 보정하여 병합.
- 출력: original.srt (+ 세그먼트 리스트 반환)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY, WHISPER_MODEL
from pipeline.s1_audio import get_ffmpeg_exe

# OpenAI 업로드 한도(25MB)보다 보수적으로 잡음
MAX_BYTES = 24 * 1024 * 1024
# 16kHz mono PCM s16 ≈ 32000 B/s → 분할 길이(초). 여유를 둬 600초(≈19MB)로.
CHUNK_SEC = 600


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _transcribe_file(client: OpenAI, path: Path, model: str) -> list[dict]:
    """단일 오디오 파일 전사 → 세그먼트 리스트(상대 타임)."""
    with path.open("rb") as f:
        resp = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="verbose_json",
        )
    segs = []
    for s in getattr(resp, "segments", []) or []:
        # SDK 객체/딕셔너리 모두 대응
        start = getattr(s, "start", None)
        end = getattr(s, "end", None)
        txt = getattr(s, "text", None)
        if start is None and isinstance(s, dict):
            start, end, txt = s.get("start"), s.get("end"), s.get("text")
        segs.append({"start": float(start), "end": float(end), "text": (txt or "").strip()})
    return segs


def _split_audio(audio_path: Path, work_dir: Path) -> list[tuple[Path, float]]:
    """오디오를 CHUNK_SEC 단위로 분할. [(조각경로, 시작오프셋초)] 반환."""
    ffmpeg = get_ffmpeg_exe()
    out_pattern = work_dir / "chunk_%03d.wav"
    cmd = [
        ffmpeg, "-y", "-i", str(audio_path),
        "-f", "segment", "-segment_time", str(CHUNK_SEC),
        "-c", "copy", str(out_pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"오디오 분할 실패:\n{result.stderr[-1500:]}")
    chunks = sorted(work_dir.glob("chunk_*.wav"))
    return [(c, i * CHUNK_SEC) for i, c in enumerate(chunks)]


def transcribe(audio_path: Path, srt_out: Path, model: str = WHISPER_MODEL) -> list[dict]:
    """audio.wav 전사 → original.srt 저장, 세그먼트 리스트 반환."""
    from utils import srt as srtutil

    client = _client()
    work_dir = audio_path.parent

    if audio_path.stat().st_size <= MAX_BYTES:
        segments = _transcribe_file(client, audio_path, model)
    else:
        segments = []
        for chunk_path, offset in _split_audio(audio_path, work_dir):
            part = _transcribe_file(client, chunk_path, model)
            for s in part:
                s["start"] += offset
                s["end"] += offset
            segments.extend(part)
            chunk_path.unlink(missing_ok=True)

    if not segments:
        raise RuntimeError(
            f"'{model}' 모델이 구간 타임코드를 반환하지 않았습니다. "
            "타임코드가 필요하면 whisper-1 을 선택하세요."
        )

    # 인덱스 부여
    for i, s in enumerate(segments, start=1):
        s["index"] = i

    srtutil.write_srt(segments, srt_out)
    return segments
