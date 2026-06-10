"""① 오디오 추출 — 영상에서 16kHz mono WAV 추출 (Whisper 권장 포맷)."""
import subprocess
from pathlib import Path

import imageio_ffmpeg


def get_ffmpeg_exe() -> str:
    """번들된 ffmpeg 실행 파일 경로 반환 (시스템 ffmpeg 불필요)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def make_clip(input_path: Path, out_path: Path, start: float, end: float) -> Path:
    """input 영상에서 [start, end] 구간만 잘라 out_path(mp4)로 저장 (재인코딩)."""
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(input_path),
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"구간 클립 생성 실패:\n{(r.stderr or '')[-1500:]}")
    return out_path


def get_work_video(job: dict) -> Path:
    """작업 대상 영상 경로. 구간 선택이 있으면 clip.mp4(없으면 생성), 없으면 원본."""
    inp = Path(job["input_path"])
    trim = job.get("trim")
    if not trim:
        return inp
    clip = Path(job["dir"]) / "clip.mp4"
    if not clip.exists():
        make_clip(inp, clip, trim["start"], trim["end"])
    return clip


def extract_audio(input_path: Path, output_path: Path) -> Path:
    """input 영상에서 오디오를 추출해 output_path(WAV)로 저장.

    - 16kHz, mono, PCM s16 → Whisper에 적합.
    - 타임코드 관련 처리 없음 (오디오만 추출).
    """
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",                  # 덮어쓰기
        "-i", str(input_path),
        "-vn",                 # 비디오 제거
        "-ac", "1",            # mono
        "-ar", "16000",        # 16kHz
        "-c:a", "pcm_s16le",   # WAV PCM
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패:\n{result.stderr[-2000:]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("오디오 추출 결과 파일이 비어 있습니다.")
    return output_path


def probe_duration(input_path: Path) -> float:
    """영상 길이(초)를 ffmpeg 로그에서 파싱. 실패 시 0.0."""
    ffmpeg = get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg, "-i", str(input_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # ffmpeg는 -i만 주면 stderr에 정보 출력 후 에러코드 반환
    for line in result.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            try:
                ts = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = ts.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                return 0.0
    return 0.0
