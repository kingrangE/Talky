# 사용자 음성 전사 지연 실험

[Talky #16](https://github.com/kingrangE/Talky/issues/16)의 전사 엔진 비교와 브라우저 표시 검증이다. 기존 Docker 워커 이미지의 faster-whisper small, CPU int8, beam size 1, 자동 언어 판별을 사용한다. 엔진 비교에서는 대화 큐, DB 저장, 브라우저와 후속 답변을 제외하고 오디오 검증부터 최종 세그먼트 소비까지 측정한다.

## 재현

[기존 음원 설명](../stt-language-recheck-20261116/README.md)에 나온 네 AIFF 파일을 다음 명령으로 다시 만든다. `english-korean-voice.aiff`는 한국어 음성으로 읽은 영어 문장이다. 실제 한영 혼합 발화가 아니다.

```sh
mkdir -p /tmp/talky-stt-eval
say -v Samantha -o /tmp/talky-stt-eval/short-english.aiff 'Good morning.'
say -v Samantha -o /tmp/talky-stt-eval/english.aiff 'I went to the museum with my friends yesterday. We are planning to travel together next weekend.'
say -v Yuna -o /tmp/talky-stt-eval/english-korean-voice.aiff 'I went to the museum with my friends yesterday. We are planning to travel together next weekend.'
say -v Yuna -o /tmp/talky-stt-eval/korean.aiff '오늘은 친구와 박물관에 다녀왔어요. 주말에는 가족과 함께 여행을 갈 계획이에요.'

docker compose -f docker-compose.yml -f docker-compose.api.yml run --rm --no-deps -T \
  --entrypoint python -v /tmp/talky-stt-eval:/fixtures:ro worker - /fixtures --repeat 3 \
  < operations/experiments/stt-latency/engine.py > /tmp/talky-stt-latency.jsonl
```

모델이 Docker 캐시 볼륨에 있어야 한다. 각 설정에 모델을 새로 만들고 적재와 첫 전사를 따로 기록한다. 워밍된 모델로 네 음원을 3회씩 반복한다. 동시 전사 2개 조건에서는 음원 두 개를 같은 시점에 제출하며 각 실행 스레드가 모델을 하나씩 유지한다. `cpu_threads=0`은 현재 라이브러리의 기본값을 사용한다. JSONL에는 음원 이름, 선택 언어, 전사문 비교용 해시, 연산 시간만 남긴다. 같은 텍스트라는 확인은 정확도 평가를 대신하지 않는다.

## 애플리케이션에서 이어서 측정하는 방법

- UI는 녹음 종료와 전송이 별도 버튼이다. 종료→미리 듣기 준비와 음성 보내기→최종 전사 표시를 각각 기록한다. 사용자 검토 시간은 시스템 지연에서 뺀다. 첫 대화라면 대화 생성 요청도 전송 이후의 대기시간에 포함한다.
- 브라우저 개발자 도구에서 음성 PUT의 요청 ID와 `Server-Timing`을 확인한다. `body`는 서버 본문 수신, `accept`는 제출 처리와 DB 접수 시간이다. 서버 수신은 브라우저 업로드와 겹친다.
- 워커에서 같은 요청 ID의 `voice_job_claimed`와 `transcript_saved`를 시도 번호로 연결한다. 첫 로그의 `queue_wait_ms`는 최초 저장 시각부터 점유까지로, 재시도에는 이전 시도와 재시도 대기가 포함된다. 두 번째 로그의 `recognition_ms`는 오디오 슬롯 대기와 모델 초기화, 실제 인식을 포함한다. `validate_and_save_ms`는 전사 문자열 검사와 DB 커밋에 걸린 시간이다.
- 조회 대기 자체는 정상적인 브라우저 요청과 실제 화면 표시 시각을 같은 시계로 측정한다. 화면 회귀 검증에서는 서버 조회 응답을 제어하고 음성 전사가 비어 있는 동안 400ms에 조회하는지, 텍스트가 렌더링되면 1500ms로 복귀하는지 검사한다. 서비스 부하에서의 실측 절감치는 별도다.

## 첫 측정 결과

동일 Docker 환경에서 CPU 12개가 보이고 컨테이너 CPU 쿼터 제한은 없었다. 각 설정의 12건은 네 합성 음원을 각각 3회씩 처리한 결과다. 분당 처리 수는 배치 실행 시간으로 계산했다. 설정을 순서대로 비교한 첫 실험이므로 호스트 부하와 파일 캐시 상태의 영향이 남아 있다. 이 표본에서 최근접 순위 p95는 최댓값이다.

| CPU 스레드 | 동시 전사 | 표본 | 중앙값(초) | p95(초) | 분당 처리 수 |
|---|---:|---:|---:|---:|---:|
| 기본 | 1 | 12 | 1.93 | 4.80 | 23.5 |
| 기본 | 2 | 12 | 2.82 | 6.70 | 27.6 |
| 2 | 1 | 12 | 3.19 | 8.04 | 14.2 |
| 2 | 2 | 12 | 3.44 | 8.90 | 19.9 |
| 4 | 1 | 12 | 1.85 | 4.57 | 24.9 |
| 4 | 2 | 12 | 2.52 | 5.15 | 32.3 |
| 6 | 1 | 12 | 1.91 | 4.17 | 25.3 |
| 6 | 2 | 12 | 3.35 | 5.91 | 27.5 |

- 기본값에서 모델 첫 적재는 단독 테스트에서 약 4.92초였고 두 모델을 함께 올린 테스트에서 각각 4.39초와 4.48초였다. 워커 사전 로딩의 효과를 볼 근거는 있지만 서비스 시작과 메모리 비용도 따로 확인해야 한다.
- 한국어 음성으로 읽은 영어 클립은 기본값 단독 사용 시 인식 중앙값 4.72초 중 재검사 시간이 1.40~1.64초였다. 나머지 세 음원의 재검사 조건 확인은 0.5ms 이하였다.
- 음원별 선택 언어와 최종 전사 해시는 모든 스레드 설정에서 같았다. 4개 설정의 동시 전사 측정이 유망했으나, 같은 조건을 교차 실행하고 실제 한국어·영어·혼합 녹음으로 확인할 때까지 운영 설정은 기본값을 사용한다.
- 이번 결과는 순수 엔진 시간과 제어한 UI 조회 검증이다. 브라우저부터 실제 대기열을 통과하는 동시 사용자 1·2·4명, 긴 발화, 실사용 전사 오류율, 조건별 100건 기준은 후속 검증으로 남는다.
