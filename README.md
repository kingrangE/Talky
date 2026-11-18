# Talky

React와 Python API, LangGraph 기반 한·영 음성 회화 학습 AI 에이전트.

- **음성 루프**: STT (faster-whisper) → LangGraph 에이전트 → TTS (Piper)
- **이중 언어**: 한국어 입력 → 영어 표현 안내 + 영어 응답 / 영어 입력 → 영어 응답 + "참고하세요!" 교정 토글
- **그래프 메모리**: Neo4j 에 대화/주제/표현을 노드-엣지로 적재, 다음 대화에서 토픽 관계를 최대 3-hop 으로 참조
- **자기 진화 프롬프트**: 종료 시 별점에 따라 메타-LLM 이 system prompt 새 버전 생성
- **모델 제공자 선택**: NVIDIA Nemotron API, OpenAI API 또는 로컬 Ollama를 설정으로 선택
- **Speaking Mock Test**: 오리지널 11문항, 브라우저 자동 타이머 녹음, 로컬 STT와 제한된 루브릭 에이전트 평가
- **즉시 보이는 대화**: 보낸 원문·녹음은 즉시 표시하고 한국어 발화의 영어 표현은 접어서 제공
- **듣기 중심 응답**: 새 답변 음성 자동 재생, 자막·표현 교정은 펼쳐 보기, 텍스트·음성 입력 전환

## 웹 화면과 API

기본 웹은 React 정적 빌드와 FastAPI를 같은 Python 서버에서 제공합니다. 별도 Node 운영 서버는 필요하지 않으며 기존 8501 포트와 PostgreSQL·Neo4j·작업자를 유지합니다. Docker 빌드 단계에서만 Node 24로 프런트엔드를 빌드합니다. 기존 Streamlit 화면은 `main.py`에 남아 있지만 기본 실행 경로에서는 사용하지 않습니다.

새 대화에서는 직접 텍스트나 음성으로 시작하거나 일상·여행·음식·취미·일과 학교·임의 주제를 골라 AI가 먼저 질문하도록 할 수 있습니다. 주제 선택은 사용자의 발화로 기록하지 않고, 기존 영속 작업 큐에서 AI 첫 메시지와 음성을 생성합니다. 같은 요청의 재확인은 첫 메시지를 중복 생성하지 않으며, 대화가 시작된 뒤에는 주제로 기존 대화를 덮어쓰지 않습니다. 새 화면 버전이 배포되면 최신 화면을 불러오는 안내를 표시하고, 보내지 않은 녹음이 있는 동안 자동으로 새로고침하지 않습니다.

보낸 텍스트와 음성 말풍선은 서버 접수나 번역을 기다리지 않고 표시합니다. 음성 인식 결과는 번역 전에 조회할 수 있도록 작업 레코드에 저장하며, 영어 표현은 해당 사용자 메시지 아래에서 펼쳐 봅니다. AI 답변은 음성이 준비되면 한 번 자동 재생하고 자막은 기본적으로 접습니다. 브라우저가 자동 재생을 막거나 음성 생성에 실패하면 재생 버튼과 자막으로 확인할 수 있습니다. 이전 대화를 열거나 상태를 새로 조회할 때 기존 음성을 다시 자동 재생하지 않습니다.

AI 답변의 `자막 보기` 아래에는 `한국어로 확인하기`를 제공합니다. 영어 답변을 저장한 뒤 한국어 번역·음성·영어 표현 교정을 병렬로 생성하며, 주제를 골라 시작한 첫 답변도 번역과 음성을 함께 생성합니다. 번역은 저장 즉시 상태 조회에 반영하고, 음성은 작업이 완료되면 제공합니다. 번역 생성 중 펼치면 준비 중 상태를 표시하며, 번역이 없는 이전 답변이나 생성 실패 시에는 펼칠 때 번역을 요청해 저장하고 재사용합니다. 번역 실패 시 영어 원문을 유지하고 다시 시도할 수 있습니다. 번역은 해당 대화 소유자만 요청할 수 있고 선택한 모델 제공자를 사용합니다. 모델·음성 자원 제한에 따른 대기는 유지하며, 번역을 펼치지 않아도 답변마다 번역 모델 호출이 추가됩니다. 표현 다듬기는 사용자 발화 아래에 표시하고, 교정 모델이 이미 자연스럽다고 판단하거나 원문과 같은 제안이면 숨깁니다. 예전 assistant 메시지에 저장된 교정도 연결된 사용자 발화 아래에서 표시하며 새 교정은 사용자 메시지에 저장합니다.

표현 다듬기는 개선한 영어 문장 전체를 굵게 표시하고, 아래 불릿의 첫 항목에는 문장 전체의 한국어 해석을 제공합니다. 이후 항목에는 개선 문장에 실제 쓰인 주요 표현·문법의 형태와 문맥상 뜻을 정리합니다. 예를 들어 `end up ~ing`가 쓰이면 “결국 ~하게 되다”라는 뜻을 설명하며, 문장에 없는 표현은 추가하지 않습니다. 기존 형식으로 저장된 교정은 그대로 읽을 수 있습니다.

한국어와 영어를 섞어 입력하면 `영어로는 이렇게 말해요`에서 마지막 입력 전체를 자연스러운 영어로 제공합니다. 한국어 부분의 번역뿐 아니라 기존 영어 부분의 문법과 표현도 함께 다듬고, 각 부분의 사실·횟수·시간 관계와 의도를 유지하도록 요청합니다.

문법 검사 뒤에는 원어민이 실제 대화에서 흔히 고르는 어휘 조합·동사·구어 표현·문장 흐름·말투를 별도로 검토합니다. 문법이 맞아도 번역투나 문어체를 더 자연스러운 회화 표현으로 바꿀 수 있으면 적극적으로 한 가지 개선안을 제안합니다. 예를 들어 일상 대화의 “I am not accustomed to speaking English.”는 “I'm not used to speaking English.”로 제안할 수 있습니다. 원래 의미와 상황에 맞는 말투는 유지하고, 선택적 자연스러움 제안과 문법 오류를 구분합니다. 이미 자연스러운 문장의 임의 동의어 교체나 억지 관용구·슬랭은 피합니다. 음성 전사에서 대문자나 구두점만 정리하는 제안은 숨깁니다. 예를 들어 “Hi, I'm studying English. Can you help me?”는 자연스러운 요청이므로 교정을 만들 필요가 없습니다.

녹음은 브라우저 MediaRecorder를 사용합니다. 최대 90초, 녹음 상태·중지·미리 듣기·다시 녹음·전송을 제공하며, 마이크 권한 거부와 지원하지 않는 브라우저를 안내합니다. 업로드는 서버에서 크기와 실제 디코딩 길이를 다시 검증합니다. 텍스트·음성 입력 중 선택한 하나만 표시하고 한국어 조합 중 Enter는 전송하지 않습니다.

인증은 기존 계정 검증과 로그인 잠금을 재사용하고 서명한 HttpOnly·SameSite 쿠키로 세션을 유지합니다. HTTPS에서는 Secure 쿠키를 사용하며 수정 요청의 Origin을 확인합니다. 요청·대화·음성은 매번 소유권을 검사합니다. 클라이언트에는 모델 API 키나 암호화 파일 경로를 보내지 않습니다. 접수 응답이 유실되면 같은 요청 번호로 다시 확인해 중복 대화를 방지합니다. 입력 초안은 현재 브라우저 탭에 보관하고, 보내지 못한 녹음은 화면을 유지하는 동안 재전송할 수 있습니다. 접수 전 녹음은 새로고침하면 사라질 수 있습니다.

## 음성 언어 자동 감지

일반 회화의 음성 언어는 자동 감지합니다. 한국어와 영어의 판단이 애매하고 감지된 발화가 4초 이상이면, 말소리의 중간 최대 3초에서 언어를 한 번 더 확인합니다. 반대 언어의 확률과 확률 차이, 두 감지 결과의 합산 근거가 기준을 만족하면 언어를 바꿉니다. 두 감지가 엇갈리지만 확률만으로 결정하지 못하면 같은 짧은 구간을 두 언어로 전사해 엔진의 평균 로그 확률을 비교합니다. 빈 결과, 무음 확률과 반복이 높은 결과는 제외하고 후보의 점수가 충분히 높으며 차이가 0.15 이상일 때만 변경합니다. 이 경우 짧은 후보 전사 두 개의 비용이 추가되지만 전체 녹음의 전사문은 선택한 언어로 한 번 생성합니다. 모든 호출은 번역이 아닌 전사 작업을 명시합니다. 확실한 언어와 짧은 발화는 추가 확인하지 않으며, 재확인 실패 시 원래 결과를 유지합니다. 현재 기준은 제한된 사례로 확인한 실험적 값이며 실제 사용자 발화 전체의 정확도나 지연 상한을 보장하지 않습니다. 시험 모드는 기존 영어 고정을 유지합니다.

## 빠른 시작 (NVIDIA Nemotron API)

```bash
cp .env.example .env
# .env의 NVIDIA_API_KEY를 채우세요. 키는 채팅·Git에 올리지 마세요.
docker compose -f docker-compose.yml -f docker-compose.api.yml up --build -d
```

새 환경 예시는 `LLM_PROVIDER=nvidia`, `NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b`를 사용합니다. 기존 `.env`는 이 두 항목과 `NVIDIA_API_KEY`를 설정하세요. API 구성은 Docker Compose 2.24.4 이상이 필요하며 Ollama를 시작하거나 모델 파일을 다운로드하지 않습니다. PostgreSQL·Neo4j·웹·회화/시험 작업자·그래프 작업자는 유지하고 STT/TTS는 기존 로컬 엔진을 사용합니다. 기존 구성에서 전환한다면 이전 Ollama 컨테이너는 `docker compose stop ollama`로 별도 중지할 수 있습니다.

NVIDIA 엔드포인트는 OpenAI 호환 클라이언트로 호출하되, 서버 주소와 API 키를 제공자별로 분리합니다. 키가 없거나 API가 실패하면 오류로 처리하며 OpenAI로 자동 전환하지 않습니다. Nemotron은 대화 지연을 줄이기 위해 thinking을 끄고, 코칭·채점·보고서는 함수 호출과 Pydantic 검증으로 구조화합니다. 한국어 지원 모델이지만 실제 교육 품질과 지연은 별도 평가가 필요합니다. [공식 API 예시](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b)를 참고하세요.

NVIDIA 무료 API는 개발·평가용으로 사용하고, 외부 사용자에게 서비스를 제공하기 전 적용 약관과 호출 한도를 확인하세요. 비상업적 공개도 운영 사용에 해당할 수 있습니다. [NVIDIA API Trial 약관](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)을 참고하세요.

추후 유료 서비스에서 OpenAI로 전환할 때는 `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 설정하고 같은 API Compose 명령으로 웹·작업자를 재생성하세요. OpenAI 요청에는 NVIDIA 전용 인자를 보내지 않습니다. 결제·구독·이용 권한 확인·사용량 과금은 아직 구현하지 않았으며, 모델 제공자 변경이 결제 완료나 유료 이용 권한 부여를 의미하지 않습니다. 결제 도입 시 서버에서 검증한 결제 상태를 기준으로 이용 권한과 한도를 적용해야 합니다.

실험용 `MEMORY_GATE_ENABLED=false`를 유지하면 일반 회화와 시험은 Ollama 없이 실행할 수 있습니다. 독립 로컬 모델을 사용하는 콘텐츠 제작 검토, 평가 judge, `operations`의 로컬 추론 진단은 별도의 Ollama가 필요합니다. 제공자 전환 후 기존 프로세스의 모델 캐시가 남지 않도록 웹·작업자를 모두 재시작하세요.

## 로컬 Ollama 실행 (Docker)

```bash
cp .env.example .env
# .env에서 LLM_PROVIDER=ollama로 변경하고 비밀번호/모델을 설정

# Modelfile 의 base 모델(GGUF) 을 ./model/ 에 두기
# 예: ./model/a.x-4.0-light-q4_k_m.gguf

docker compose up --build
```

기동 후 http://localhost:8501 접속.

회화는 계정을 만들고 로그인한 뒤 사용할 수 있습니다. 사용자별 대화·보고서·별점과
프롬프트를 분리하며, 로그아웃·브라우저 새로고침 후에는 다시 로그인합니다.
로그인 실패 5회 시 해당 계정은 15분 동안 잠깁니다. 이메일 인증·비밀번호 재설정은
포함하지 않습니다. 공개 서버는 HTTPS 역방향 프록시를 앞에 두어야 합니다.
앱 포트는 기본 localhost에만 게시하며 DB·Neo4j·Ollama 포트는 외부에 게시하지 않습니다.

사이드바의 `Speaking Mock Test`를 선택하면 모의고사 모드로 전환됩니다. 공개 배포에서는
HTTPS와 데스크톱 Chrome/Edge를 사용하고, `.env`의 서명·핑거프린트 비밀값을 반드시
교체하세요. 녹음, 전사문, 결과는 기본 72시간 뒤 정리됩니다.

첫 기동 시 다음이 자동 수행됩니다:
- Postgres alembic 마이그레이션 (계정의 첫 대화 생성 시 개인 prompt v1 삽입)
- Ollama 가 `Modelfile` 로 모델 생성 (수 분 소요)
- 앱 첫 호출 시 faster-whisper / Piper voice 모델이 `./.cache/` 에 다운로드

시험 응답은 PostgreSQL에 작업 상태를 저장하고 별도 `worker`가 처리합니다.
중단된 작업은 재시작 후 최대 3회까지 복구하며, 작업이 기본 180초를 넘으면
worker 프로세스를 종료·재시작합니다. 같은 worker 프로세스에서 음성 모델을 재사용합니다.
앱과 worker는 설정된 모델 실행 슬롯을 공유하며, API 구성에서는 기본 2개입니다.
이미 제출된 시험 응답이 있으면 다음 모델 실행 슬롯을 먼저 배정해 회화 부하에 밀리지 않게 합니다.
음성·암호화 키·안내 음성은 `mock-exam-data` 볼륨에 보존합니다.
만료 정리는 worker가 기본 60초 간격으로 실행하며 파일 삭제 실패는 재시도합니다.
worker가 중단되면 시험 처리와 만료 정리도 중단되므로 운영 중 worker 상태를 확인해야 합니다.

기존 배포를 갱신할 때는 **컨테이너 재생성 전에** 기존 앱을 중지하고
`/app/.data/mock-exam`의 음성·`.audio-key`·자동 생성된 `.signing-key`를 백업한 뒤
새 `mock-exam-data` 볼륨으로 옮기세요. 기존 컨테이너의 쓰기 영역은 새 볼륨으로
자동 복사되지 않습니다. 키가 없어지면 기존 음성을 복호화할 수 없습니다.

## 구성

| 서비스    | 포트                | 비고                                         |
| -------- | ------------------- | -------------------------------------------- |
| app      | localhost:8501      | React 정적 화면 + FastAPI                    |
| worker   | 없음                 | 영속 시험 작업·만료 정리                    |
| ollama   | 내부 11434          | 로컬 LLM. 첫 기동 시 `Modelfile` 로 모델 생성 |
| postgres | 내부 5432           | 메시지/보고서/별점/프롬프트 버전              |
| neo4j    | 내부 7474, 7687     | 대화 그래프 메모리                           |

## 로컬 개발

Postgres / Neo4j / Ollama 가 외부에 떠 있는 환경:

```bash
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.api:app --host 127.0.0.1 --port 8501 --reload
```

다른 터미널에서 `python -m app.mock_exam.worker`를 실행해야 시험 제출이 처리됩니다.
Compose의 DB 포트는 게시하지 않으므로 호스트에서 개발하려면 별도 로컬 DB를 쓰거나
개발 전용 Compose override에서 loopback 포트를 명시하세요.

화면 개발은 실행 중인 Docker API 또는 위의 로컬 API에 연결합니다. Node 24를 사용하고 `frontend` 폴더에서 `npm ci`, `npm run dev`를 실행하면 Vite가 `/api` 요청을 localhost:8501로 전달합니다. 운영 빌드는 `npm run build`, 화면 회귀 테스트는 `npm test`, Python API와 기존 로직의 테스트는 루트에서 `python -m pytest -q`로 실행합니다. PostgreSQL 통합 테스트는 별도 `*_test` DB의 `TALKY_TEST_POSTGRES_DSN`이 필요합니다. `python -m app.graph_db.worker`도 실행해야 대화 기억 투영과 삭제가 처리됩니다.

모의고사는 기존 11문항·자동 타이머·기술 오류 재시도·72시간 보관·일일 및 동시 이용 제한을 유지합니다. 복구 링크와 별도 HttpOnly 시험 쿠키를 사용하며, 원래의 10분 중단 복구 정책을 적용합니다. 새 웹의 서버 준비 상태 경로는 `/api/health`입니다.

## 사용자별 시스템 프롬프트

`prompt_versions.user_id`를 기준으로 사용자마다 활성 시스템 프롬프트 하나를 관리합니다.
기본 로컬 모드는 `prompts/seed_v1.md`에서 사용자별 RDB 본문을 생성합니다.
Cloud 모드에서는 Langfuse의 `talky/system`을 출발점으로 이름·버전만 연결합니다.
개인화된 프롬프트는 시드 파일 변경이나 재시작으로 덮어쓰지 않습니다. 로컬 기본 프롬프트를 그대로 쓰는 계정은 새 대화를 만들 때 최신 기본값을 새 버전으로 적용하며, 이전 대화는 시작 당시 버전을 유지합니다.
별점 집계, 개선 자료, 버전 활성화, 품질 하락 롤백은 모두 해당 사용자 범위로 제한합니다.

기본 대화 톤은 친구처럼 구체적인 반응과 가벼운 관점을 보태고, 적절한 순간에 일상 질문이나 새 화제를 먼저 꺼내도록 합니다. 지원 상담 문구와 요청하지 않은 조언, 반복적인 칭찬과 질문 공세는 피합니다. 영어 답변과 학습 피드백은 분리하고, 사용자가 대화를 끝내거나 질문을 원하지 않을 때는 따릅니다. 친근함을 위해 실제로 겪지 않은 일이나 근거 없는 공유 기억을 만들지 않습니다.

새 대화는 사용자의 활성 버전을 고정합니다. 에이전트는 매 실행 시 RDB에서
대화 소유자와 프롬프트 소유자를 확인하고 고정 참조로 해당 본문을 읽습니다. 진행 중인 대화는
기존 버전을 유지하며, 개선된 버전은 새 대화부터 적용됩니다.

기존 설치는 `alembic upgrade head`를 실행해야 합니다. `0003_user_prompts`는
기존 공통 버전 이력을 기존 사용자별로 복제하고 대화·평가·부모 버전 연결을 옮깁니다.
기존 공통 본문을 출발점으로 보존하며, 이후 개선부터 사용자별로 독립합니다.
소유자가 없는 기존 공통 버전은 비활성 이력으로 보존합니다.

`0004_operational_safety`는 계정 인증 필드·작업 시도 횟수·평가 수정 번호와 파일 삭제
목록을 추가합니다. 기존 사용자와 대화는 보존하며 새 가입자에게 자동 연결하지 않습니다.
관리자가 기존 데이터의 소유자를 확인한 경우에만 서버 CLI로 해당 UUID에 로그인 계정을
연결할 수 있습니다. 비밀번호는 터미널에서 숨김 입력합니다.

```bash
python -m app.auth my_login --existing-user-id EXISTING_USER_UUID
```

이미 로그인 계정이 연결된 사용자는 이 명령으로 덮어쓸 수 없습니다.

## 환경변수

`.env.example` 참고. 핵심:

- `LLM_PROVIDER` — `nvidia`, `openai`, `ollama` 중 명시적으로 선택. `.env.example`은 NVIDIA, 설정 파일이 없는 코드 기본값은 Ollama
- `NVIDIA_API_KEY`, `NVIDIA_MODEL` — NVIDIA 전용 인증 키와 모델명. 선택한 제공자의 키가 없으면 다른 제공자로 대체하지 않음
- `OPENAI_API_KEY`, `OPENAI_MODEL` — 추후 OpenAI 전환 시 사용할 별도 키와 모델명
- `MODEL_NAME` — Ollama 모델명 (Modelfile 의 tag 와 일치해야 함)
- `WHISPER_MODEL`, `WHISPER_DEVICE` — STT 모델/장치
- `TTS_VOICE_KO`, `TTS_VOICE_EN` — Piper voice 이름 (HuggingFace `rhasspy/piper-voices` 의 디렉토리 규칙)
- `APP_USER_ID` — 기존 시드 CLI용 UUID. 회화 로그인 신원으로 사용하지 않음
- `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`, `LLM_MAX_INPUT_BYTES` — 호출 기한·출력 토큰·입력 UTF-8 바이트 상한
- `MAX_HISTORY_BYTES`, `MAX_USER_TEXT_CHARS` — 최근 대화 문맥·개별 텍스트 상한. 긴 대화의 예전 내용은 모델 입력에서 제외
- `MAX_AUDIO_BYTES`, `MAX_AUDIO_SECONDS` — 압축 업로드 크기·실제 디코딩 길이 상한
- `MOCK_EXAM_JOB_TIMEOUT_SECONDS`, `MOCK_EXAM_MAX_ATTEMPTS` — worker 강제 재시작 기한·처리 시도 한도
- `MOCK_EXAM_CLEANUP_INTERVAL_SECONDS` — worker의 만료 정리 간격
- `EVOLVE_BATCH` — 사용자별 현재 프롬프트에 미반영 별점 N 개 누적 시 개별 진화 (기본 5)
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

React · Vite · FastAPI · LangGraph · LangChain · Langfuse · Neo4j · PostgreSQL · faster-whisper · Piper · Docker

## Langfuse Cloud 프롬프트 관리

Cloud 모드에서는 프롬프트 본문·버전을 Langfuse에서 관리합니다. PostgreSQL에는
사용자별 활성 프롬프트·부모·별점 연결과 Langfuse 이름·숫자 버전, 대화가 선택한
공통 프롬프트 참조만 저장합니다. Cloud에서 새로 만드는 프롬프트의 DB `content`는
빈 문자열이며, 기존 본문은 검증과 복구를 위해 자동 삭제하지 않습니다.

먼저 Langfuse Cloud에 프로젝트를 만들고 Settings → API Keys에서 키와 해당 리전의
접속 URL을 확인합니다. 키를 채팅에 보내거나 Git에 올리지 말고 로컬 `.env`에 넣습니다.

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=프로젝트의_public_key
LANGFUSE_SECRET_KEY=프로젝트의_secret_key
LANGFUSE_BASE_URL=프로젝트에서_안내하는_URL
LANGFUSE_PROMPT_LABEL=staging
LANGFUSE_PROMPT_VERSIONS={}
LANGFUSE_RELEASE=실행할_코드의_commit_SHA
```

프로젝트의 Python 환경에서 다음 순서로 실행합니다. 기존 앱을 업데이트할 때는 먼저
DB를 백업하고 앱·worker를 중지한 뒤 마이그레이션합니다.

```bash
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m app.llm.manage_prompts publish --label staging
python -m app.llm.manage_prompts migrate
python -m app.llm.manage_prompts check
```

Compose로 운영 중이라면 호스트의 Python 대신 다음 명령을 사용합니다. DB 포트를
외부에 게시할 필요가 없습니다. 앱 기본 entrypoint는 웹 API를 시작하므로 관리
명령에서는 반드시 `--entrypoint python`을 지정합니다.

```bash
docker compose stop app worker
docker compose build app
docker compose up -d postgres
docker compose run --rm --no-deps --entrypoint python app -m alembic upgrade head
docker compose run --rm --no-deps --entrypoint python app -m app.llm.manage_prompts publish --label staging
docker compose run --rm --no-deps --entrypoint python app -m app.llm.manage_prompts migrate
docker compose run --rm --no-deps --entrypoint python app -m app.llm.manage_prompts check
docker compose up -d app worker
```

- `publish`: `prompts/`의 system·한국어 코칭·영어 답변·영어 교정과 기억·대화·표현 지도 judge를 등록합니다. 같은 최신 본문은 새 버전을 만들지 않습니다. 기본 라벨은 staging이며 production은 바꾸지 않습니다. 저장소 프롬프트를 변경한 뒤 재실행하면 새 버전이 생깁니다.
- `migrate`: 기존 DB 버전들을 사용자별 `talky/users/<UUID>/system`으로 업로드하고,
  Cloud 원문과 일치하는지 확인한 뒤 참조를 연결합니다. 기존 ID·대화·별점·본문은
  보존하며 이미 연결된 버전은 건너뜁니다. 실행은 기존 사용자 프롬프트를 Cloud에
  업로드하므로 사용 중인 프로젝트와 데이터 범위를 확인한 뒤 실행합니다.
- `check`: 인증과 선택된 라벨/숫자 버전의 공통 프롬프트 조회를 확인합니다.

앱을 재시작하고 새 대화에서 한국어·영어 입력을 각각 확인합니다. Langfuse의 Prompts와 Traces에서 선택 버전을 확인하고, [기억·대화·표현 지도 평가](data/test/README.md)를 거쳐 선택한 공통 버전에 UI에서 production 라벨을 붙입니다. `.env`의 라벨도 production으로 바꾸고 재시작합니다. 로컬 데이터셋은 `python -m app.evaluate`로 영역별 생성·채점·Langfuse 기록을 실행합니다. 평가용 Neo4j 설정과 파일럿 실행 방법은 데이터셋 README를 참고하세요. Hosted Dataset 업로드와 ChromaDB 비교는 별도입니다.

새 대화에서 라벨을 숫자 버전으로 해석해 고정하므로, 라벨 이동은 진행 중인 대화에
영향을 주지 않습니다. 사용자별 개선 프롬프트는 각자 다른 이름으로 생성하고 DB의
활성 참조로 선택하므로 다른 사용자의 개선이나 전역 production 라벨과 섞이지 않습니다.
기본 `talky/system`을 쓰는 사용자는 새 대화에서 새 기본 버전을 선택하고, 이미 개인화된
사용자는 개인 버전을 유지합니다. 기존 대화의 공통 지시는 최초 재개 시 로컬 파일의
해시로 고정하므로 새 Cloud 라벨로 자동 교체되지 않습니다.

SDK 캐시는 프로세스별로 300초 유지됩니다. 같은 숫자 버전의 캐시가 남아 있으면
API 장애 중에도 이를 사용할 수 있지만, 캐시가 비어 있으면 Cloud 조회가 필요합니다.
Cloud 모드에서 다른 본문이나 로컬 프롬프트로 조용히 대체하지 않습니다. 로컬 파일이
바뀌어 고정 해시와 달라진 기존 로컬 대화도 재개를 거절하므로 이전 코드로 재개하거나
새 대화를 시작합니다. Cloud 프롬프트·사용 중인 버전을 삭제하지 마세요.

평가 시에는 `.env`에서 비교할 버전을 JSON으로 명시하고 새 대화/평가 상태를 사용합니다.
버전이 없거나 이름을 잘못 쓰면 다른 프롬프트로 평가하지 않고 실패합니다.

```dotenv
LANGFUSE_PROMPT_VERSIONS={"talky/system":1,"talky/ko-coach":1,"talky/en-reply":1,"talky/better-expression":1,"talky/evaluation-judge":1,"talky/dialogue-judge":1,"talky/expression-judge":1}
```

이 설정의 system 버전은 기본 system을 쓰는 사용자에게 적용되며 기존 개인 버전을
덮어쓰지 않습니다. 비교 실험에서는 같은 사용자 system 참조도 별도로 고정합니다.
generation은 공통 프롬프트에 직접 연결하고, 사용자 system 참조는 metadata에 기록합니다.
실제 모델 입력·출력도 Cloud trace에 기록되므로 합성 데이터로 먼저 확인하세요.
현재 직접 계측하는 호출은 한국어 코칭·영어 답변·영어 교정이며, 토픽 추출·종료 보고서·
프롬프트 개선 모델 등 나머지 호출의 trace는 포함하지 않습니다. 구조화 출력 호출은
현재 팩토리에서 토큰 사용량을 반환하지 않으므로 그 호출의 토큰·비용은 미측정입니다.

LangChain 0.3 계열을 유지하고 Langfuse 5.2 SDK의 수동 generation 기록을 사용합니다.
최신 LangChain callback을 추가하지 않습니다. Cloud에서 프롬프트 게시에 성공한 뒤
DB 커밋이 실패하면 참조되지 않은 Cloud 버전이 남을 수 있지만 앱에서 활성화하지는
않습니다. DB downgrade는 Cloud 참조가 남아 있으면 중단하므로, 본문을 내보내고
참조를 안전하게 되돌리는 이전 절차 없이 downgrade하지 않습니다.

`LANGFUSE_ENABLED=false`가 기본값이며 기존 로컬/DB 방식으로 동작합니다. 이미 Cloud
참조를 가진 대화를 이 설정만 바꾸어 로컬로 전환할 수는 없습니다.

공식 문서: [버전·라벨](https://langfuse.com/docs/prompt-management/features/prompt-version-control),
[프롬프트와 trace 연결](https://langfuse.com/docs/prompt-management/features/link-to-traces).

## 회귀 테스트

```bash
python -m pytest -q
```

PostgreSQL 경합·롤백·worker 복구 검사는 전용 테스트 DB를 지정해야 실행합니다.
DB 이름은 반드시 `_test`로 끝나야 하며, 검사마다 별도 스키마를 만들고 제거합니다.

```bash
TALKY_TEST_POSTGRES_DSN='postgresql+psycopg://USER:PASSWORD@localhost:5432/talky_test' python -m pytest -q
```

## Graph 동기화와 장애 재처리

- 대화·보고서의 원본과 토픽은 PostgreSQL에 저장하고, 같은 트랜잭션에서 Graph 동기화 작업을 기록합니다. `graph-worker`가 대화별 순서를 지켜 Neo4j에 반영합니다. 장애 시 작업을 버리지 않고 지수 backoff로 재시도하며, worker를 여러 개 실행해도 같은 대화의 작업은 순서대로 처리합니다.
- 원본 대화가 삭제됐으면 늦게 도착한 저장 작업도 Graph 삭제로 처리합니다. 회상 노드에서도 PostgreSQL의 현재 소유권·존재 여부를 확인하므로 삭제된 대화를 동기화 대기 중에 다시 제공하지 않습니다.
- 재전송해도 메시지 ID·원래 시간·대화 연결을 유지하고 토픽 작업은 Neo4j의 완료 표식으로 중복 집계를 막습니다. PostgreSQL 커밋 전에 프로세스가 종료돼도 미완료 작업은 남아 다시 처리됩니다. 이는 재시도와 멱등성을 조합한 방식이며 두 DB의 분산 트랜잭션은 아닙니다.
- 기존 설치는 앱·worker를 중지한 뒤 `python -m alembic upgrade head`를 실행하고 `python -m app.graph_db.worker` 또는 Compose의 `graph-worker`를 함께 시작해야 합니다. 대기 작업이 남은 상태의 스키마 downgrade는 거부합니다. 동기화가 지연되면 장기 기억이 일시적으로 덜 검색될 수 있으므로 작업 지연을 관측해야 합니다.

### 대화 요청의 저장과 복구

대화 입력은 PostgreSQL의 요청 큐에 저장한 뒤 `python -m app.mock_exam.worker` 작업자가 처리합니다. 웹 화면을 닫거나 다른 대화로 이동해도 접수한 요청은 남으며, 같은 대화를 다시 선택하면 진행 상태를 조회합니다. 요청 번호로 중복 입력을 구분하고, 대화별로 한 요청만 처리합니다. 기본 전체 대기 한도는 32건, 접수 후 대기 제한은 300초입니다. `CONVERSATION_QUEUE_LIMIT`, `CONVERSATION_QUEUE_WAIT_SECONDS`로 조정합니다.

텍스트 응답과 Graph Outbox는 같은 트랜잭션으로 저장합니다. 이후 음성 합성이 실패해도 텍스트는 유지됩니다. 저장 후 작업자가 중단되면 재시도는 저장된 응답을 사용하므로 LLM을 다시 호출하거나 메시지를 중복 저장하지 않습니다. 음성 입출력은 기존 암호화 저장소와 72시간 보존 정책을 공유합니다. 배포 전 `alembic upgrade head`를 실행하고, 웹과 작업자를 함께 운영해야 합니다.

### 작업자 증설과 추론 한도

작업자는 PostgreSQL `SKIP LOCKED`로 서로 다른 요청을 가져옵니다. 시험 작업도 정상 처리 중인 작업을 재시작 시 회수하지 않고, 감독 프로세스의 실행 제한보다 30초 긴 임대가 만료된 뒤에만 재처리합니다. 시험과 대화 큐를 번갈아 확인해 한쪽 요청만 계속 처리하는 상황을 줄였습니다. 이 변경을 배포할 때는 기존 작업자를 먼저 중지하고 마이그레이션 후 새 작업자를 시작합니다.

API 구성(`docker-compose.yml` + `docker-compose.api.yml`)은 기본 `WORKER_REPLICAS=2`, `AUDIO_CAPACITY=2`, `INFERENCE_CAPACITY=2`로 서로 다른 요청을 동시에 처리합니다. 기본 Compose만 사용하는 로컬 Ollama 구성의 슬롯 기본값은 1개입니다. 작업자만 추가해도 슬롯 한도를 초과해 모델·음성 작업을 실행하지 않습니다. CPU 음성 인식과 TTS는 오디오 슬롯을 함께 쓰며, CUDA 음성 인식은 추론 슬롯을 사용합니다. 한국어 번역은 같은 답변의 중복 호출만 잠그고, 서로 다른 답변은 추론 슬롯 안에서 병렬 실행합니다. 같은 대화 안의 입력은 계속 순서대로 처리합니다.

작업자는 컨테이너마다 모델을 따로 캐시하므로 작업자 수를 늘리면 RAM도 추가로 필요합니다. 한도를 더 올리기 전 CPU·RAM, 원격 API의 요청 한도와 p95 지연을 측정하세요. 로컬 모델은 실제 GPU의 VRAM과 Ollama 동시 처리 설정도 확인해야 합니다. PostgreSQL 세션 advisory lock을 슬롯별로 사용하므로 작업 중 슬롯마다 DB 연결 하나를 점유합니다. PgBouncer의 transaction pooling과 함께 사용하면 안 됩니다. 임대 복구는 저장 중복을 막지만, 중단 직전 원격 모델에 전달된 계산까지 취소한다고 보장하지 않습니다.

Piper의 ONNX 세션은 프로세스당 계산 스레드를 1개로 제한해 여러 작업자가 CPU를 두고 과도하게 경합하지 않도록 합니다. 이 제한은 TTS에만 적용하며 음성 인식 엔진의 스레드 설정은 바꾸지 않습니다.

2026-11-17 로컬 Docker에서 Amy medium 모델을 미리 로딩한 프로세스 2개로 동일한 짧은 문장 4개를 합성했을 때, 최종 스레드 설정에서 오디오 슬롯 1개는 3.64초, 2개는 1.80초였으며 실제 합성 동시 실행도 각각 1개·2개로 확인했습니다. 슬롯 2개에서 프로세스별 최대 RSS 합계는 약 566MiB였습니다. 이는 TTS만의 소규모 측정이며, 모델 로딩·STT·원격 LLM·HTTP·브라우저 지연을 포함한 전체 서비스의 처리량이나 메모리 상한은 아닙니다.

### Google Neural2 대화 음성

일반 회화에는 선택적으로 Google Neural2(F)를 사용합니다. 첫 문장을 암호화 저장한 즉시 재생 경로에 공개하고 뒤 문장을 이어서 생성하므로 번역·교정 완료와 전체 음성 완성을 기다리지 않습니다. Neural2는 양방향 스트리밍 API가 아닌 문장별 합성을 사용하며 화면은 기존 1.5초 상태 조회 주기를 사용합니다. 모의고사와 기존 저장 음성은 기존 경로를 사용합니다.

프로젝트에 결제 계정을 연결하고 Text-to-Speech API를 활성화한 뒤 ADC를 준비합니다. `.env`의 `GOOGLE_CLOUD_PROJECT`와 `GOOGLE_ADC_FILE`에 프로젝트 ID와 저장소 밖의 ADC 파일 절대 경로를 설정하고 아래 명령을 실행합니다. 인증 파일은 웹과 대화 작업자 컨테이너에 읽기 전용으로 연결하며 이미지나 Git에 넣지 않습니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.api.yml -f docker-compose.tts.yml up -d --no-deps --build app worker
```

음성 비교 화면과 전용 합성 API는 배포 대상에서 제거했습니다. Neural2는 기존 누적 50,000자 실험 예산을 유지합니다. 사용량 파일 `tts-comparison-usage.json`은 기존 카운터를 이어 쓰기 위해 이름을 유지하고 PostgreSQL 잠금으로 동시 예약을 조정합니다. 파일을 지우면 예산이 초기화됩니다. 실패한 요청도 호출 전에 예약한 문자 수를 차감합니다. 상한에 도달하면 텍스트 답변은 유지하지만 새 음성을 제공하지 못할 수 있습니다. 이는 Google 청구 계정 전체의 지출 제한이 아닙니다.

최대 4,000자·32구간·120초 음성으로 제한하며 호출별 15초, 합성 전체 약 90초의 시간 예산을 둡니다. 작업자의 시도 번호와 유효 임대를 확인한 뒤 생성하고 각 문장까지의 음성을 기존 암호화 저장소에 교체 저장합니다. 완료 전 파일은 정상 WAV 다운로드로 제공하지 않으며 실패하면 부분 파일을 회수합니다. 재생 연결을 끊어도 이미 접수한 작업자는 완료 음성을 보관하므로 다시 듣기는 추가 합성 없이 가능합니다. 브라우저가 재생을 멈추면 네트워크 읽기와 예약된 소리는 취소합니다. 작업자 장애 뒤 재시도에는 추가 합성 비용이 들 수 있으며 이미 재생한 음성을 자동으로 처음부터 반복하지 않습니다. 실제 기기 청취, 부하와 p95 검증은 [이슈 #15](https://github.com/kingrangE/Talky/issues/15)에 남아 있습니다.

주제를 골라 시작할 때는 앞선 발화를 가정하지 않는 검토된 첫 질문을 사용합니다. 음식은 “What's a meal you could eat every day without getting tired of it?”로 시작하며, 무작위 주제는 검토된 질문 중 하나를 고릅니다. 사용자 답변 이후의 대화는 기존 LLM 생성 경로를 사용합니다.

### AWS S3 음성 공유 저장소

`AUDIO_S3_BUCKET`을 지정하면 웹과 작업자가 S3의 `audio/` 경로를 공유합니다. 별도 URL을 공개하지 않고 인증된 애플리케이션이 암호화 객체를 읽습니다. `MOCK_EXAM_AUDIO_KEY`에는 모든 인스턴스가 공유하는 Fernet 키를, `MOCK_EXAM_SIGNING_SECRET`에는 복구 토큰 서명 비밀을 주입해야 합니다. S3 모드에서는 로컬 키를 자동 생성하지 않습니다. AWS에서는 Secrets Manager와 ECS 작업 역할을 사용하고 접근 키를 이미지나 `.env`에 넣지 않습니다.

기존 로컬 저장소가 있는 서비스는 `.audio-key`를 먼저 안전하게 백업한 뒤 같은 키로 기존 암호화 파일을 S3의 `audio/` 아래 상대 경로 그대로 옮겨야 합니다. 모든 인스턴스를 같은 저장소·키로 전환하기 전에는 쓰기를 중지합니다. 키를 새로 생성해 교체하면 기존 음성을 읽지 못합니다. 기존 `.signing-key`도 유지해야 발급된 복구 토큰을 검증할 수 있습니다. S3 서비스 오류는 삭제 완료로 처리하지 않으며 기존 삭제 Outbox가 재시도합니다.

AWS 운영 구성과 단계별 배포·복구 검증 기준은 [AWS 운영 안내](infrastructure/aws/README.md)에 정리했습니다. 템플릿은 리소스를 자동 생성하지 않으며 실제 적용에는 별도 승인이 필요합니다. 기존 Compose 배포도 마이그레이션을 일회성 `migrate` 서비스로 분리했으므로 새 설정을 함께 적용해야 합니다.
