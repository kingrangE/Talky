# 운영 검증

## 로컬 큐 부하 검증

2026-11-11에 전용 PostgreSQL 16 테스트 DB에서 8개 제출 스레드가 32개 대화에 각각 요청을 접수하고, 4개 처리 스레드가 추론 슬롯 2개를 공유하도록 검증했다. 모델 결과는 고정 문자열을 사용했다. 실제 LLM·STT·TTS, 네트워크 부하, Streamlit 연결 수를 측정한 결과가 아니다. 모델을 호출하지 않는 테스트이므로 초당 사용자 수나 GPU 용량 산정에 사용하면 안 된다.

32건이 모두 완료됐고 메시지는 정확히 64개, 모든 요청의 처리 시도는 1회였다. 전체 2.185초, 접수 p95 104.41ms, 대기 p95 1,865.22ms, 처리 p95 20.35ms였다. 대기 시간은 모든 접수 이후 처리 스레드를 시작하는 테스트 구조와 초기 모듈 로딩을 포함한다. 원본 수치는 `queue-load-result.json`에 보관했다.

```sh
TALKY_TEST_POSTGRES_DSN=postgresql+psycopg://USER:PASSWORD@HOST:PORT/talky_test \
TALKY_LOAD_RESULT=/tmp/talky-queue-load.json \
python -m pytest tests/test_operational_load.py -q -s
```

테스트는 이름이 `_test`로 끝나는 DB에서 임시 스키마를 생성하고 종료 시 제거한다. 운영 DB를 지정하지 않는다.

## 단계별 지연 조회

대화 생성, 종료 보고서, 시험 채점의 LangGraph 노드는 `talky.metrics` 로거로 JSON을 출력한다. `event=stage`는 단계, 소요 시간, 성공·오류를 기록한다. 작업자의 `event=job`은 요청 종류, 시도 횟수, 최초 접수 이후 대기 시간, 처리 시간을 기록한다. 재시도 시 대기 시간에는 이전 시도 시간이 포함된다. 입력 문장, 음성, 사용자 ID, 예외 원문은 이 운영 로그에 기록하지 않는다. Langfuse의 모델 입출력 수집 정책은 별도로 관리해야 한다.

CloudWatch Logs Insights에서 메시지 안 JSON을 추출해 볼 수 있다.

```text
fields @timestamp, @message
| parse @message /(?<payload>\{.*\})/
| fields jsonParse(payload) as m
| filter m.event = "stage"
| stats count(*) as calls, pct(m.duration_ms, 50) as p50_ms, pct(m.duration_ms, 95) as p95_ms by m.stage, m.outcome
```

운영 부하 검증은 실제 모델을 예열한 뒤 동시 사용자 수를 단계적으로 올려 접수 거절률, 큐 대기 p95, 단계별 p95, GPU VRAM·사용률, DB 연결 수를 함께 비교한다. 추론 슬롯을 늘렸을 때 p95와 실패율이 악화되면 이전 값으로 되돌린다. 이 저장소의 로컬 수치는 해당 운영 검증을 대신하지 않는다.
