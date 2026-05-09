# Talky

LangGraph 기반 한·영 음성 회화 학습 AI 에이전트.

- **음성 루프**: STT (faster-whisper) → LangGraph 에이전트 → TTS (Piper)
- **이중 언어**: 한국어 입력 → 한국어 응답 + 영어 표현 코칭 / 영어 입력 → 영어 응답 + "참고하세요!" 토글
- **그래프 메모리**: Neo4j 에 대화/주제/표현을 노드-엣지로 적재, 다음 대화에서 multi-hop 으로 참조
- **자기 진화 프롬프트**: 종료 시 별점에 따라 메타-LLM 이 system prompt 새 버전 생성
- **오픈소스 Docker 배포**: 외부 클라우드 의존 없이 `docker compose up` 한 번으로 기동

## 빠른 시작 (Docker)

```bash
cp .env.example .env
# 비밀번호/모델 등은 필요 시 .env 에서 수정

# Modelfile 의 base 모델(GGUF) 을 ./model/ 에 두기
# 예: ./model/a.x-4.0-light-q4_k_m.gguf

docker compose up --build
```

기동 후 http://localhost:8501 접속.

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

## 기술 스택

LangGraph · LangChain · Neo4j · Streamlit · PostgreSQL · faster-whisper · Piper · Docker
