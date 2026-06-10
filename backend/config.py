"""환경 설정 로드 (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트 (= backend의 부모)
ROOT_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT_DIR / "work"
FRONTEND_DIR = ROOT_DIR / "frontend"

# .env 로드
load_dotenv(ROOT_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o")
SCENE_THRESHOLD = float(os.getenv("SCENE_THRESHOLD", "27.0"))

WORK_DIR.mkdir(exist_ok=True)
