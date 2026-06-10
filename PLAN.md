# 영상 자동 분석 파이프라인 — 구현 계획

> 상태: **계획 단계** (코드 미구현). 본 문서는 단계별 구현 계획만 정의합니다.

## 1. 개요 (Context)

영상 파일 1개를 업로드하면 자동으로 분석하여 **자막(SRT)** 과 **장면 메타데이터(JSON)** 를 생성하는 웹 페이지를 만든다.

처리 순서는 다음 6단계다:

1. 오디오 추출
2. Whisper 자막 생성
3. GPT 자막 교정
4. 장면 감지
5. 장면별 GPT 분석
6. 메타데이터 통합

### 최종 산출물
- `original.srt` — Whisper가 만든 원본 자막
- `corrected.srt` — GPT가 텍스트만 교정한 자막 (**타임코드 동일**)
- `scenes.json` — 장면별 메타데이터 (시작/끝 타임코드 + GPT 분석 + 해당 구간 자막)

### 핵심 제약
- **타임코드 수정 금지**: GPT 교정·통합 단계에서 타임스탬프는 절대 변경하지 않는다. GPT에는 텍스트만 보내고 텍스트만 받아 원본 타임코드에 다시 매핑한다.
- **API 키는 `.env`**: 코드/저장소에 키를 하드코딩하지 않는다. `.env`는 `.gitignore`로 제외하고 `.env.example`만 커밋한다.

---

## 2. 기술 스택 (결정)

| 영역 | 선택 | 이유 |
|------|------|------|
| 백엔드 | **Python + FastAPI** | ffmpeg/PySceneDetect/openai SDK가 모두 Python에 강함 |
| 프론트엔드 | **단일 HTML/JS 페이지** | 업로드 + 진행 표시 + 결과 다운로드만 필요 |
| 오디오 추출 | **ffmpeg** (CLI 호출) | 표준 도구 |
| 자막 생성 | **OpenAI Whisper API** (`whisper-1` / `gpt-4o-transcribe`) | 설치 간단, GPU 불필요 |
| 자막 교정·장면 분석 | **OpenAI GPT** (`gpt-4o` 계열) | 텍스트 교정 및 멀티모달 분석 |
| 장면 감지 | **PySceneDetect** (ContentDetector) | 장면별 시작/끝 타임코드 추출이 깔끔 |

> 대안: Whisper를 로컬 `faster-whisper`로, 프론트를 Next.js로 바꿀 수 있으나 초기 구현은 위 조합으로 진행.

---

## 3. 디렉터리 구조 (예정)

```
ai 영상분석/
├─ .env                 # 실제 키 (gitignore)
├─ .env.example         # 키 템플릿
├─ .gitignore
├─ requirements.txt
├─ README.md
├─ PLAN.md              # (본 문서)
├─ backend/
│  ├─ main.py           # FastAPI 앱 (라우트, 업로드, 결과 제공)
│  ├─ config.py         # .env 로드, 설정
│  ├─ pipeline/
│  │  ├─ orchestrator.py   # 6단계 순서 제어 + 진행 상태
│  │  ├─ s1_audio.py       # ① 오디오 추출 (ffmpeg)
│  │  ├─ s2_whisper.py     # ② Whisper 자막 → original.srt
│  │  ├─ s3_correct.py     # ③ GPT 교정 → corrected.srt
│  │  ├─ s4_scenes.py      # ④ 장면 감지 (PySceneDetect)
│  │  ├─ s5_analyze.py     # ⑤ 장면별 GPT 분석
│  │  └─ s6_merge.py       # ⑥ 메타데이터 통합 → scenes.json
│  └─ utils/
│     └─ srt.py            # SRT 파싱/직렬화 (타임코드 보존)
├─ frontend/
│  └─ index.html        # 업로드 UI + 진행률 + 결과 다운로드
└─ work/                 # 작업/출력 디렉터리 (job별 폴더, gitignore)
   └─ {job_id}/
      ├─ input.mp4
      ├─ audio.wav
      ├─ original.srt
      ├─ corrected.srt
      └─ scenes.json
```

---

## 4. 단계별 처리 계획

### ① 오디오 추출 (`s1_audio.py`)
- ffmpeg로 영상에서 오디오만 추출: `ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 audio.wav`
- 16kHz mono WAV — Whisper 권장 포맷.
- 산출물: `audio.wav`

### ② Whisper 자막 생성 (`s2_whisper.py`)
- OpenAI Whisper API에 오디오 전송, `response_format=srt`(또는 `verbose_json`)로 타임코드 포함 자막 수신.
- 파일 크기 제한(25MB) 초과 시 ffmpeg로 구간 분할 후 각 조각 전사 → 오프셋을 더해 타임코드 병합.
- 산출물: **`original.srt`** (자막 세그먼트 = 인덱스 + 시작/끝 타임코드 + 텍스트)

### ③ GPT 자막 교정 (`s3_correct.py`) — ⚠️ 타임코드 보존 핵심
- `utils/srt.py`로 `original.srt`를 세그먼트 리스트로 파싱.
- **텍스트만** 추출하여 GPT에 교정 요청 (오탈자/띄어쓰기/구두점 정리, 의미 변경 금지).
- 세그먼트 개수·순서를 1:1로 유지하도록 프롬프트 설계(번호 매김 입력 → 동일 번호 출력). 개수 불일치 시 폴백/재시도.
- 교정된 텍스트를 **원본 타임코드에 그대로 재결합**.
- 산출물: **`corrected.srt`** (타임코드는 `original.srt`와 100% 동일)

### ④ 장면 감지 (`s4_scenes.py`)
- PySceneDetect `ContentDetector`로 컷 감지.
- 각 장면의 `start`/`end` 타임코드, 장면 번호 추출.
- (선택) 각 장면 대표 프레임 1장을 ffmpeg로 추출하여 ⑤의 시각 분석 입력으로 사용.
- 산출물: 장면 리스트 `[{scene_no, start, end, thumbnail?}]`

### ⑤ 장면별 GPT 분석 (`s5_analyze.py`)
- 각 장면에 대해:
  - 해당 구간에 겹치는 교정 자막 텍스트를 모음.
  - (대표 프레임이 있으면) GPT 멀티모달로 장면 설명, 없으면 자막 기반 분석.
  - GPT가 장면 요약/키워드/주요 내용 등 구조화 결과 생성.
- 산출물: 장면별 분석 `{scene_no, summary, keywords, ...}`

### ⑥ 메타데이터 통합 (`s6_merge.py`)
- ④ 장면 타임코드 + ⑤ GPT 분석 + 구간별 자막을 하나로 결합.
- 타임코드는 ④/③에서 온 값을 그대로 사용 (재계산·수정 없음).
- 산출물: **`scenes.json`**

```jsonc
{
  "video": "input.mp4",
  "duration": "00:12:34",
  "scenes": [
    {
      "scene_no": 1,
      "start": "00:00:00,000",
      "end": "00:00:08,500",
      "summary": "장면 요약",
      "keywords": ["..."],
      "subtitles": [
        { "start": "00:00:01,000", "end": "00:00:03,200", "text": "교정된 자막" }
      ]
    }
  ]
}
```

---

## 5. 웹 페이지 흐름

1. 사용자가 영상 1개 업로드 → 서버가 `job_id` 발급, `work/{job_id}/`에 저장.
2. 백그라운드로 6단계 파이프라인 실행 (FastAPI BackgroundTask 또는 비동기 작업).
3. 프론트는 진행 상태를 폴링(`GET /status/{job_id}`)하여 단계별 진행률 표시.
4. 완료 시 `original.srt` / `corrected.srt` / `scenes.json` 다운로드 링크 제공.

### API 엔드포인트 (예정)
- `POST /upload` → 영상 수신, job 시작, `job_id` 반환
- `GET /status/{job_id}` → 현재 단계/진행률/오류
- `GET /result/{job_id}/{file}` → 산출물 다운로드

---

## 6. 환경 변수 (`.env`)

```env
OPENAI_API_KEY=sk-...
WHISPER_MODEL=whisper-1
GPT_MODEL=gpt-4o
SCENE_THRESHOLD=27.0   # PySceneDetect ContentDetector 임계값
```

- `python-dotenv`로 로드, `config.py`에서 일괄 관리.
- `.env`는 `.gitignore`, `.env.example`만 커밋.

---

## 7. 의존성 (`requirements.txt` 예정)

```
fastapi
uvicorn[standard]
python-multipart
python-dotenv
openai
scenedetect[opencv]
```

추가 시스템 요구: **ffmpeg** 설치 필요 (PATH 등록).

---

## 8. 오류 처리 / 주의사항

- 각 단계 산출물을 디스크에 남겨 재시작/디버깅 용이하게.
- GPT 교정 시 세그먼트 수 불일치 → 분할 재요청 또는 원문 유지 폴백.
- Whisper 25MB 제한 → 자동 분할.
- 타임코드는 어떤 단계에서도 재계산하지 않고 ②/④의 원본 값을 전파.
- 키 누락/ffmpeg 미설치 시 명확한 에러 메시지.

---

## 9. 구현 순서 (마일스톤)

1. 프로젝트 골격 + `.env`/config + ffmpeg 확인
2. `utils/srt.py` (파싱/직렬화, 타임코드 보존 단위 테스트)
3. ① 오디오 추출 → ② Whisper (`original.srt`)
4. ③ GPT 교정 (`corrected.srt`, 타임코드 동일성 검증)
5. ④ 장면 감지 + (선택)프레임 추출
6. ⑤ 장면별 GPT 분석 → ⑥ 통합 (`scenes.json`)
7. FastAPI 라우트 + 프론트 업로드/진행/다운로드
8. 통합 테스트(샘플 영상 1개로 6단계 end-to-end)

---

## 10. 검증 방법

- 샘플 영상으로 전체 파이프라인 실행 후:
  - `original.srt`와 `corrected.srt`의 **타임코드 라인이 완전히 동일**한지 diff로 확인 (텍스트만 차이).
  - `scenes.json`의 장면 타임코드 합이 영상 길이와 일치하는지 확인.
  - 세그먼트/장면 개수가 비어 있지 않은지 확인.
