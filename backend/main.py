"""FastAPI 앱 — 업로드, 단계 실행, 상태/결과 제공, 프론트 서빙."""
import shutil
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import FRONTEND_DIR, WORK_DIR
from pipeline import orchestrator

app = FastAPI(title="영상 자동 분석 파이프라인")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/steps")
def api_steps():
    return {"steps": orchestrator.steps_meta(), "presets": orchestrator.presets_meta()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "파일명이 없습니다.")
    job_id_dir = WORK_DIR / "tmp"
    # 먼저 job 생성을 위해 임시 저장 후 job 디렉터리로 이동
    # job_id를 먼저 만들기 위해 파일 경로를 확정
    # -> create_job이 input_path.parent를 dir로 쓰므로 디렉터리 먼저 준비
    import uuid

    job_id = uuid.uuid4().hex[:12]
    jdir = WORK_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix or ".mp4"
    input_path = jdir / f"input{ext}"
    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # orchestrator의 create_job은 새 id를 만들므로, 여기서는 직접 등록
    orchestrator.JOBS[job_id] = {
        "id": job_id,
        "filename": file.filename,
        "input_path": str(input_path),
        "dir": str(jdir),
        "current_step": 0,
        "status": "ready",
        "error": None,
        "results": {},
        "done_steps": [],
        "options": {},
        "trim": None,
    }
    return {"job_id": job_id, "filename": file.filename}


@app.get("/api/video/{job_id}")
def get_video(job_id: str):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    return FileResponse(job["input_path"])


@app.post("/api/range/{job_id}")
def set_range(job_id: str, body: dict = Body(default=None)):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    body = body or {}
    start, end = body.get("start"), body.get("end")
    if start is None or end is None or float(end) <= float(start):
        job["trim"] = None
    else:
        job["trim"] = {"start": float(start), "end": float(end)}

    # 구간이 바뀌면 기존 산출물/진행상태를 초기화 (다시 ①부터 실행)
    work = Path(job["dir"])
    for name in ("clip.mp4", "audio.wav", "original.srt", "corrected.srt"):
        p = work / name
        if p.exists():
            p.unlink()
    job["results"] = {}
    job["done_steps"] = []
    job["current_step"] = 0
    job["status"] = "ready"
    return _job_view(job)


@app.post("/api/step/{job_id}")
def run_step(job_id: str):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    job = orchestrator.run_next_step(job_id)
    return _job_view(job)


@app.post("/api/step/{job_id}/{step_id}")
def run_step_id(job_id: str, step_id: str, options: dict | None = Body(default=None)):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    if options:
        job.setdefault("options", {})[step_id] = options
    job = orchestrator.run_step_by_id(job_id, step_id)
    return _job_view(job)


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    return _job_view(job)


@app.get("/api/result/{job_id}/{name}")
def result_file(job_id: str, name: str):
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, "job을 찾을 수 없습니다.")
    path = Path(job["dir"]) / name
    if not path.exists() or path.name != Path(name).name:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    return FileResponse(path)


def _job_view(job: dict) -> JSONResponse:
    steps = orchestrator.steps_meta()
    return JSONResponse({
        "job_id": job["id"],
        "filename": job["filename"],
        "status": job["status"],
        "error": job["error"],
        "current_step": job["current_step"],
        "total_steps": len(steps),
        "steps": steps,
        "done_steps": job.get("done_steps", []),
        "results": job["results"],
        "trim": job.get("trim"),
    })
