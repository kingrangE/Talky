# Talky

LangGraph 기반 한·영 음성 회화 학습 AI 에이전트.

- **음성 루프**: STT (faster-whisper) → LangGraph 에이전트 → TTS (Piper)
- **이중 언어**: 한국어 입력 → 한국어 응답 + 영어 표현 코칭 / 영어 입력 → 영어 응답 + "참고하세요!" 토글
- **그래프 메모리**: Neo4j 에 대화/주제/표현을 노드-엣지로 적재, 다음 대화에서 multi-hop 으로 참조
- **자기 진화 프롬프트**: 종료 시 별점에 따라 메타-LLM 이 system prompt 새 버전 생성
- **오픈소스 Docker 배포**: 외부 클라우드 의존 없이 `docker compose up` 한 번으로 기동
- **Speaking Mock Test**: 오리지널 11문항, 브라우저 자동 타이머 녹음, 로컬 STT와 제한된 루브릭 에이전트 평가

## 빠른 시작 (Docker)

```bash
cp .env.example .env
# 비밀번호/모델 등은 필요 시 .env 에서 수정

# Modelfile 의 base 모델(GGUF) 을 ./model/ 에 두기
# 예: ./model/a.x-4.0-light-q4_k_m.gguf

docker compose up --build
```

기동 후 http://localhost:8501 접속.

사이드바의 `Speaking Mock Test`를 선택하면 모의고사 모드로 전환됩니다. 공개 배포에서는
HTTPS와 데스크톱 Chrome/Edge를 사용하고, `.env`의 서명·핑거프린트 비밀값을 반드시
교체하세요. 녹음, 전사문, 결과는 기본 72시간 뒤 정리됩니다.

첫 기동 시 다음이 자동 수행됩니다:
- Postgres alembic 마이그레이션 + 시드 prompt v1 삽입
- Ollama 가 `Modelfile` 로 모델 생성 (수 분 소요)
- 앱 첫 호출 시 faster-whisper / Piper voice 모델이 `./.cache/` 에 다운로드

## 구성

| 서비스    | 포트                | 비고                                         |
| -------- | ------------------- | -------------------------------------------- |
| app      | 8501                | Streamlit + LangGraph 에이전트              |
| ollama   | 11434               | 로컬 LLM. 첫 기동 시 `Modelfile` 로 모델 생성 |
| postgres | 5432                | pgvector 포함, 메시지/보고서/별점/프롬프트 버전 |
| neo4j    | 7474 (UI), 7687     | 대화 그래프 메모리                           |

## 로컬 개발

Postgres / Neo4j / Ollama 가 외부에 떠 있는 환경:

```bash
pip install -r requirements.txt
alembic upgrade head
python -m app.db.seed
streamlit run main.py
```

## 환경변수

`.env.example` 참고. 핵심:

- `LLM_PROVIDER` — `ollama` 기본. `openai` 로 두고 `OPENAI_API_KEY` 가 있으면 OpenAI 사용
- `MODEL_NAME` — Ollama 모델명 (Modelfile 의 tag 와 일치해야 함)
- `WHISPER_MODEL`, `WHISPER_DEVICE` — STT 모델/장치
- `TTS_VOICE_KO`, `TTS_VOICE_EN` — Piper voice 이름 (HuggingFace `rhasspy/piper-voices` 의 디렉토리 규칙)
- `EVOLVE_BATCH` — 별점 N 개 누적 시 프롬프트 진화 트리거 (기본 5)
- `MOCK_EXAM_SCORING_PROFILE` — `basic`, `advanced`, `auto`. 기본 Docker 이미지는 CPU `basic`
- `MOCK_EXAM_DAILY_LIMIT`, `MOCK_EXAM_GLOBAL_CONCURRENCY` — 공개 데모 자원 제한
- `MOCK_EXAM_SIGNING_SECRET`, `MOCK_EXAM_FINGERPRINT_SALT` — 공개 배포 시 필수 교체

## 모의고사 콘텐츠 제작

승인 세트는 `app/mock_exam/data/sets/`에 있습니다. 새 후보는 로컬 생성 모델과 별도의
로컬 검토 모델, 결정적 검증기, 최대 2회의 수정 루프를 거쳐 PR 검토 대상으로 만듭니다.

```bash
python -m app.mock_exam.authoring "Create an original workplace communication set"
python -m app.mock_exam.narration
```

후보 JSON은 자동 배포되지 않습니다. 라이선스 출처와 검토 기록을 사람이 확인하고
`status: approved`로 병합해야 합니다.

TOEIC® is a registered trademark of ETS. This product is not endorsed or approved by ETS.
Talky는 공식 문항·로고·채점 결과를 사용하지 않으며 결과는 학습 참고용 베타 추정치입니다.
자세한 내용은 [PRIVACY.md](PRIVACY.md)와 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요.

## 기술 스택

LangGraph · LangChain · Neo4j · Streamlit · PostgreSQL · faster-whisper · Piper · Docker
