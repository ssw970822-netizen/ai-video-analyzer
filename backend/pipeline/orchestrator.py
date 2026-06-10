"""파이프라인 오케스트레이터 — job 상태 관리 + 단계 실행.

단계별 확인(사용자 OK 후 다음 단계) 방식을 지원하기 위해,
각 단계를 STEPS 레지스트리에 등록하고 한 번에 하나씩 실행한다.
"""
import uuid
from pathlib import Path
from typing import Any, Callable

from config import WORK_DIR, WHISPER_MODEL, GPT_MODEL
import json

from pipeline import (
    s1_audio, s2_whisper, s2_whisper_local, s3_correct,
    s4_scenes, s5_analyze, s6_merge, s7_quality,
)

# 단계 정의: (id, 표시이름, 실행함수)
# 실행함수(job) -> dict (해당 단계 결과, job["results"]에 병합됨)
STEPS: list[dict[str, Any]] = []

# 프리셋: 선택 시 각 단계의 모델/옵션을 일괄 변경
# (타임코드 안정성 때문에 전사는 모든 프리셋에서 whisper-1 유지)
PRESETS: list[dict] = [
    {
        "id": "recommended", "label": "추천", "icon": "👍",
        "desc": "품질과 비용의 균형",
        "options": {
            "whisper": {"model": "faster-whisper:small"},
            "correct": {"model": "gpt-4o-mini"},
            "analyze": {"model": "gpt-4o"},
        },
    },
    {
        "id": "performance", "label": "성능", "icon": "🚀",
        "desc": "최고 품질 우선 (비용↑)",
        "options": {
            "whisper": {"model": "faster-whisper:medium"},
            "correct": {"model": "gpt-4.1"},
            "analyze": {"model": "gpt-4o"},
        },
    },
    {
        "id": "max", "label": "최고 성능", "icon": "🏆",
        "desc": "모든 단계 최상위 모델 + 정밀 장면 감지 (비용 최대)",
        "options": {
            "whisper": {"model": "faster-whisper:large-v3"},
            "correct": {"model": "gpt-4.1"},
            "analyze": {"model": "gpt-4.1"},
            "scenes": {"detector": "AdaptiveDetector"},
        },
    },
    {
        "id": "value", "label": "가성비", "icon": "💰",
        "desc": "저렴·빠름 우선 (품질 약간↓)",
        "options": {
            "whisper": {"model": "faster-whisper:base"},
            "correct": {"model": "gpt-4o-mini"},
            "analyze": {"model": "gpt-4o-mini"},
        },
    },
]


def presets_meta() -> list[dict]:
    return PRESETS


def step(step_id: str, title: str, tool: str = "",
         deps: list[str] | None = None, options: list[dict] | None = None):
    def deco(fn: Callable[[dict], dict]):
        STEPS.append({
            "id": step_id, "title": title, "tool": tool,
            "deps": deps or [], "options": options or [], "run": fn,
        })
        return fn
    return deco


def get_opt(job: dict, step_id: str, key: str):
    """job에 저장된 단계 옵션 값 (없으면 정의된 기본값)."""
    spec = _find_step(step_id)
    default = None
    if spec:
        for o in spec["options"]:
            if o["key"] == key:
                default = o.get("default")
                break
    return job.get("options", {}).get(step_id, {}).get(key, default)


# 메모리 내 job 저장소
JOBS: dict[str, dict] = {}


def create_job(input_path: Path, filename: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id,
        "filename": filename,
        "input_path": str(input_path),
        "dir": str(input_path.parent),
        "current_step": 0,          # 다음에 실행할 단계 인덱스
        "status": "ready",          # ready | running | error | done
        "error": None,
        "results": {},              # step_id -> 결과 dict
        "done_steps": [],           # 완료된 단계 id 목록
        "options": {},              # step_id -> {key: value} 선택 옵션
        "trim": None,               # {start, end} 구간 선택 (None=전체)
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def steps_meta() -> list[dict]:
    return [
        {"id": s["id"], "title": s["title"], "tool": s["tool"],
         "deps": s["deps"], "options": s["options"]}
        for s in STEPS
    ]


def _find_step(step_id: str) -> dict | None:
    for s in STEPS:
        if s["id"] == step_id:
            return s
    return None


def run_next_step(job_id: str) -> dict:
    """job의 다음 단계를 1개 실행 (순차 모드, 호환용)."""
    job = JOBS[job_id]
    idx = job["current_step"]
    if idx >= len(STEPS):
        job["status"] = "done"
        return job
    return run_step_by_id(job_id, STEPS[idx]["id"])


def run_step_by_id(job_id: str, step_id: str) -> dict:
    """id로 지정한 단계 1개 실행."""
    job = JOBS[job_id]
    spec = _find_step(step_id)
    if spec is None:
        job["status"] = "error"
        job["error"] = f"알 수 없는 단계: {step_id}"
        return job
    job["status"] = "running"
    job["error"] = None
    try:
        result = spec["run"](job)
        job["results"][spec["id"]] = result
        if step_id not in job["done_steps"]:
            job["done_steps"].append(step_id)
        # current_step = 완료된 마지막 단계의 다음으로 갱신 (순차 호환)
        idx = [s["id"] for s in STEPS].index(step_id)
        job["current_step"] = max(job["current_step"], idx + 1)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
    return job


# ---------------------------------------------------------------------------
# 단계 ① 오디오 추출
# ---------------------------------------------------------------------------
@step("audio", "① 오디오 추출", tool="ffmpeg (imageio-ffmpeg)")
def _step_audio(job: dict) -> dict:
    video = s1_audio.get_work_video(job)   # 구간 선택 시 클립 사용
    out = Path(job["dir"]) / "audio.wav"
    duration = s1_audio.probe_duration(video)
    s1_audio.extract_audio(video, out)
    return {
        "audio_file": out.name,
        "size_bytes": out.stat().st_size,
        "duration_sec": round(duration, 2),
    }


# ---------------------------------------------------------------------------
# 단계 ② Whisper 자막 생성
# ---------------------------------------------------------------------------
@step(
    "whisper", "② Whisper 자막",
    tool="로컬 faster-whisper", deps=["audio"],
    options=[{
        "key": "model", "label": "전사 모델",
        "default": "faster-whisper:small",
        "hint": "로컬 모델은 무료/오프라인. 첫 실행 시 모델을 자동 다운로드합니다.",
        "tiers": [
            {"id": "value", "label": "가성비", "icon": "💰", "value": "faster-whisper:base"},
            {"id": "balanced", "label": "추천", "icon": "👍", "value": "faster-whisper:small"},
            {"id": "performance", "label": "성능", "icon": "🚀", "value": "faster-whisper:medium"},
            {"id": "max", "label": "최고", "icon": "🏆", "value": "faster-whisper:large-v3"},
        ],
        "choices": [
            {"value": "faster-whisper:base", "speed": "빠름", "cost": "무료 (로컬)", "quality": "보통", "desc": "가장 빠름·가벼움 (~150MB)"},
            {"value": "faster-whisper:small", "speed": "보통", "cost": "무료 (로컬)", "quality": "좋음", "desc": "균형 (권장, ~500MB)"},
            {"value": "faster-whisper:medium", "speed": "느림", "cost": "무료 (로컬)", "quality": "매우 좋음", "desc": "정확도↑ (~1.5GB)"},
            {"value": "faster-whisper:large-v3", "speed": "매우 느림", "cost": "무료 (로컬)", "quality": "최상", "desc": "최고 정확도 (~3GB)"},
            {"value": "whisper-1", "speed": "보통", "cost": "API ($0.006/분)", "quality": "좋음", "desc": "OpenAI 서버 전사"},
        ],
    }],
)
def _step_whisper(job: dict) -> dict:
    work = Path(job["dir"])
    audio = work / "audio.wav"
    if not audio.exists():
        raise RuntimeError("audio.wav 가 없습니다. ① 오디오 추출을 먼저 실행하세요.")
    model = get_opt(job, "whisper", "model")
    srt_out = work / "original.srt"
    if model.startswith("faster-whisper:"):
        size = model.split(":", 1)[1]
        segments = s2_whisper_local.transcribe_local(audio, srt_out, size=size)
        tool_used = f"로컬 faster-whisper ({size})"
    else:
        segments = s2_whisper.transcribe(audio, srt_out, model=model)
        tool_used = f"OpenAI {model}"
    from utils.srt import sec_to_ts
    items = [
        {
            "index": s["index"],
            "start_tc": sec_to_ts(s["start"])[:8],
            "end_tc": sec_to_ts(s["end"])[:8],
            "text": s["text"],
        }
        for s in segments
    ]
    return {
        "srt_file": srt_out.name,
        "segment_count": len(segments),
        "segments": items,
        "tool_used": tool_used,
    }


# ---------------------------------------------------------------------------
# 단계 ③ GPT 자막 교정 (타임코드 보존)
# ---------------------------------------------------------------------------
@step(
    "correct", "③ GPT 자막 교정",
    tool=f"OpenAI GPT ({GPT_MODEL})", deps=["whisper"],
    options=[{
        "key": "model", "label": "교정 모델",
        "default": GPT_MODEL,
        "tiers": [
            {"id": "value", "label": "가성비", "icon": "💰", "value": "gpt-4o-mini"},
            {"id": "balanced", "label": "추천", "icon": "👍", "value": "gpt-4.1-mini"},
            {"id": "performance", "label": "성능", "icon": "🚀", "value": "gpt-4o"},
            {"id": "max", "label": "최고", "icon": "🏆", "value": "gpt-4.1"},
        ],
        "choices": [
            {"value": "gpt-4o", "speed": "보통", "cost": "높음", "quality": "최상", "desc": "가장 정확"},
            {"value": "gpt-4o-mini", "speed": "빠름", "cost": "매우 저렴", "quality": "좋음", "desc": "가성비 최고 (권장)"},
            {"value": "gpt-4.1", "speed": "보통", "cost": "높음", "quality": "최상", "desc": "최신 고품질"},
            {"value": "gpt-4.1-mini", "speed": "빠름", "cost": "저렴", "quality": "좋음", "desc": "가성비"},
        ],
    }],
)
def _step_correct(job: dict) -> dict:
    work = Path(job["dir"])
    original = work / "original.srt"
    if not original.exists():
        raise RuntimeError("original.srt 가 없습니다. ② Whisper 자막을 먼저 실행하세요.")
    model = get_opt(job, "correct", "model")
    corrected_out = work / "corrected.srt"
    stats = s3_correct.correct(original, corrected_out, model=model)

    from utils import srt as srtutil
    from utils.srt import sec_to_ts
    orig_segs = srtutil.parse_srt(original.read_text(encoding="utf-8"))
    corr_segs = srtutil.parse_srt(corrected_out.read_text(encoding="utf-8"))
    items = []
    for o, c in zip(orig_segs, corr_segs):
        items.append({
            "index": c["index"],
            "start_tc": sec_to_ts(c["start"])[:8],
            "end_tc": sec_to_ts(c["end"])[:8],
            "original": o["text"],
            "corrected": c["text"],
            "changed": o["text"] != c["text"],
        })

    return {
        "srt_file": corrected_out.name,
        "original_srt": original.name,
        "segment_count": stats["segment_count"],
        "changed_count": stats["changed_count"],
        "timecodes_preserved": stats["timecodes_preserved"],
        "segments": items,
        "tool_used": f"OpenAI {model}",
    }


# ---------------------------------------------------------------------------
# 단계 ④ 장면 감지
# ---------------------------------------------------------------------------
@step(
    "scenes", "④ 장면 감지",
    tool="PySceneDetect + ffmpeg",
    options=[{
        "key": "detector", "label": "감지기",
        "default": "ContentDetector",
        "hint": "로컬 처리라 비용은 없고 정확도/속도만 다릅니다.",
        "tiers": [
            {"id": "value", "label": "가성비", "icon": "💰", "value": "ThresholdDetector"},
            {"id": "balanced", "label": "추천", "icon": "👍", "value": "ContentDetector"},
            {"id": "max", "label": "최고", "icon": "🏆", "value": "AdaptiveDetector"},
        ],
        "choices": [
            {"value": "ContentDetector", "speed": "보통", "cost": "무료 (로컬)", "quality": "일반 컷 정확", "desc": "색/움직임 변화 컷 (권장)"},
            {"value": "AdaptiveDetector", "speed": "느림", "cost": "무료 (로컬)", "quality": "완만한 전환에 강함", "desc": "카메라 이동/디졸브"},
            {"value": "ThresholdDetector", "speed": "빠름", "cost": "무료 (로컬)", "quality": "페이드/블랙 전용", "desc": "페이드인·아웃"},
        ],
    }],
)
def _step_scenes(job: dict) -> dict:
    work = Path(job["dir"])
    video = s1_audio.get_work_video(job)   # 구간 선택 시 클립 사용
    detector = get_opt(job, "scenes", "detector")
    scenes = s4_scenes.detect_scenes(video, work, detector=detector)
    (work / "scenes_raw.json").write_text(
        json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "scene_count": len(scenes),
        "scenes": scenes,
        "tool_used": f"PySceneDetect ({detector})",
    }


# ---------------------------------------------------------------------------
# 단계 ⑤ 장면별 GPT 분석
# ---------------------------------------------------------------------------
@step(
    "analyze", "⑤ 장면별 GPT 분석",
    tool=f"OpenAI GPT Vision ({GPT_MODEL})", deps=["scenes"],
    options=[{
        "key": "model", "label": "분석 모델",
        "default": GPT_MODEL,
        "hint": "이미지를 분석하므로 단계 중 비용이 가장 큽니다.",
        "tiers": [
            {"id": "value", "label": "가성비", "icon": "💰", "value": "gpt-4o-mini"},
            {"id": "balanced", "label": "추천", "icon": "👍", "value": "gpt-4o"},
            {"id": "max", "label": "최고", "icon": "🏆", "value": "gpt-4.1"},
        ],
        "choices": [
            {"value": "gpt-4o", "speed": "보통", "cost": "높음", "quality": "최상", "desc": "가장 정확한 장면 이해"},
            {"value": "gpt-4o-mini", "speed": "빠름", "cost": "저렴", "quality": "좋음", "desc": "가성비 (권장)"},
            {"value": "gpt-4.1", "speed": "보통", "cost": "보통", "quality": "최상", "desc": "최신 고품질"},
        ],
    }],
)
def _step_analyze(job: dict) -> dict:
    work = Path(job["dir"])
    scenes_result = job["results"].get("scenes")
    if not scenes_result:
        raise RuntimeError("장면 데이터가 없습니다. ④ 장면 감지를 먼저 실행하세요.")
    model = get_opt(job, "analyze", "model")
    scenes = scenes_result["scenes"]
    corrected = work / "corrected.srt"
    analyzed = s5_analyze.analyze_scenes(scenes, corrected, work, model=model)
    (work / "scenes_analyzed.json").write_text(
        json.dumps(analyzed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "scene_count": len(analyzed),
        "scenes": analyzed,
        "tool_used": f"OpenAI {model} (vision)",
    }


# ---------------------------------------------------------------------------
# 단계 ⑥ 메타데이터 통합
# ---------------------------------------------------------------------------
@step("merge", "⑥ 메타데이터 통합", tool="Python (자체 통합 로직)", deps=["analyze"])
def _step_merge(job: dict) -> dict:
    work = Path(job["dir"])
    analyzed_result = job["results"].get("analyze")
    if not analyzed_result:
        raise RuntimeError("분석 데이터가 없습니다. ⑤ 장면별 GPT 분석을 먼저 실행하세요.")
    analyzed = analyzed_result["scenes"]
    corrected = work / "corrected.srt"
    duration = job["results"].get("audio", {}).get("duration_sec", 0.0)

    out = work / "scenes.json"
    metadata = s6_merge.merge(
        analyzed, corrected, job["filename"], duration, out
    )

    outputs = [
        {"name": "original.srt", "label": "원본 자막"},
        {"name": "corrected.srt", "label": "교정 자막"},
        {"name": "scenes.json", "label": "장면 메타데이터"},
    ]
    outputs = [o for o in outputs if (work / o["name"]).exists()]
    return {
        "video": metadata["video"],
        "duration": metadata["duration"],
        "scene_count": metadata["scene_count"],
        "subtitle_count": metadata["subtitle_count"],
        "outputs": outputs,
    }


# ---------------------------------------------------------------------------
# 단계 ⑦ 품질 이슈 감지 (블랙 화면 / 음성 튐)
# ---------------------------------------------------------------------------
@step("quality", "⑦ 품질 이슈 감지", tool="ffmpeg blackdetect + ebur128 라우드니스 (LKFS)", deps=["audio"])
def _step_quality(job: dict) -> dict:
    work = Path(job["dir"])
    video = s1_audio.get_work_video(job)   # 구간 선택 시 클립 사용
    audio = work / "audio.wav"
    result = s7_quality.analyze_quality(video, audio)
    (work / "quality_issues.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
