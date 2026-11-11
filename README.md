# Talky

LangGraph 기반 한·영 음성 회화 학습 AI 에이전트.

- **음성 루프**: STT (faster-whisper) → LangGraph 에이전트 → TTS (Piper)
- **이중 언어**: 한국어 입력 → 영어 표현 안내 + 영어 응답 / 영어 입력 → 영어 응답 + "참고하세요!" 교정 토글
- **그래프 메모리**: Neo4j 에 대화/주제/표현을 노드-엣지로 적재, 다음 대화에서 토픽 관계를 최대 3-hop 으로 참조
- **자기 진화 프롬프트**: 종료 시 별점에 따라 메타-LLM 이 system prompt 새 버전 생성
- **오픈소스 Docker 배포**: 기본 로컬 모드는 외부 클라우드 의존 없이 `docker compose up`으로 기동
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
앱과 worker는 모델 실행 슬롯 하나를 공유하므로 혼잡한 회화 요청에는 재시도를 안내합니다.
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
| app      | localhost:8501      | Streamlit + LangGraph 에이전트              |
| worker   | 없음                 | 영속 시험 작업·만료 정리                    |
| ollama   | 내부 11434          | 로컬 LLM. 첫 기동 시 `Modelfile` 로 모델 생성 |
| postgres | 내부 5432           | 메시지/보고서/별점/프롬프트 버전              |
| neo4j    | 내부 7474, 7687     | 대화 그래프 메모리                           |

## 로컬 개발

Postgres / Neo4j / Ollama 가 외부에 떠 있는 환경:

```bash
pip install -r requirements-dev.txt
alembic upgrade head
streamlit run main.py --server.maxMessageSize=10 --server.maxUploadSize=5
```

다른 터미널에서 `python -m app.mock_exam.worker`를 실행해야 시험 제출이 처리됩니다.
Compose의 DB 포트는 게시하지 않으므로 호스트에서 개발하려면 별도 로컬 DB를 쓰거나
개발 전용 Compose override에서 loopback 포트를 명시하세요.

## 사용자별 시스템 프롬프트

`prompt_versions.user_id`를 기준으로 사용자마다 활성 시스템 프롬프트 하나를 관리합니다.
기본 로컬 모드는 `prompts/seed_v1.md`에서 사용자별 RDB 본문을 생성합니다.
Cloud 모드에서는 Langfuse의 `talky/system`을 출발점으로 이름·버전만 연결합니다.
기존 개인 프롬프트는 시드 파일 변경이나 재시작으로 덮어쓰지 않습니다.
별점 집계, 개선 자료, 버전 활성화, 품질 하락 롤백은 모두 해당 사용자 범위로 제한합니다.

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

- `LLM_PROVIDER` — `ollama` 기본. `openai` 로 두고 `OPENAI_API_KEY` 가 있으면 OpenAI 사용
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

LangGraph · LangChain · Langfuse · Neo4j · Streamlit · PostgreSQL · faster-whisper · Piper · Docker

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
외부에 게시할 필요가 없습니다. 앱 기본 entrypoint는 Streamlit을 시작하므로 관리
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
