# 백업과 복구 검증

## 실제로 확인한 범위

전용 PostgreSQL 16 컨테이너에서 원본 대화 2개 메시지, 대기 중인 음성 요청, Graph Outbox, 고정 버전 프롬프트 캐시를 준비했다. `pg_dump --format=custom`으로 임시 스키마를 백업하고 새로 만든 별도 `_test` DB에 `pg_restore --single-transaction --exit-on-error`로 복원했다. 원본 DB를 덮어쓰지 않았다. 복원 후 원문·메시지 수·대기 작업의 음성 경로·Graph 이벤트·프롬프트 원문이 유지되는지 검사했다.

암호화 음성 파일도 별도 경로로 복사하고 원래 Fernet 키로 복호화했다. 키를 바꾸면 복호화가 거부되는지도 확인했다. 이 검증은 로컬 파일과 PostgreSQL의 복원이며, S3 이전 버전 복구나 RDS 시점 복구를 실행한 결과는 아니다. 실험용 데이터에는 실제 사용자 정보가 없다.

```sh
# 전용 talky-ops-* PostgreSQL 컨테이너와 *_test DB만 사용한다.
# 접속 정보는 TALKY_TEST_POSTGRES_DSN 환경 변수로 전달한다.
TALKY_TEST_POSTGRES_CONTAINER=talky-ops-postgres \
python -m pytest tests/test_backup_restore.py -q
```

## AWS 복구 훈련 순서

1. 장애 시각, 마지막 정상 접수·완료 시각, 배포 이미지 digest, 프롬프트 버전, 관련 비밀의 버전을 기록한다. 성공 응답을 받은 요청이 복원 후에도 존재하는지 대조할 기준으로 사용한다.
2. 운영 DB에 덮어쓰지 않고 별도 RDS 인스턴스로 시점 복구한다. 복구 대상 시각과 최신 복구 가능 시각을 확인하고 private subnet·보안 그룹·TLS·백업 정책을 검토한다. RDS 자동 백업은 비동기이므로 복구 가능 지점 이후의 접수까지 보존된다고 단정하지 않는다. [RDS 시점 복구 절차](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html)를 따른다.
3. 같은 시점에 유효한 Fernet 키·서명 비밀·실행 계정·cookie secret을 복구한다. S3 객체의 현재·이전 버전과 보존 시각을 확인한다. 키가 없는 음성 객체와 DB만으로는 음성을 복구할 수 없다. 복구한 요청이 참조하는 객체가 이미 보존 기간을 지나 삭제됐다면 해당 음성은 복구 불가로 기록한다.
4. 쓰기와 모델 작업자를 활성화하기 전에 복구 DB의 사용자·대화·메시지·요청·Outbox·시험 결과 수, 상태, 최근 시각을 점검한다. 고정 프롬프트 캐시의 원문 해시도 확인한다. 먼저 격리된 앱 환경을 복구 DB에 연결해 로그인과 조회를 검사한다.
5. 대기 작업은 한 작업자로 재개해 메시지 중복과 오래된 시도의 저장 차단을 확인한다. 실제 모델 호출에는 비용과 중복 계산이 생길 수 있으므로 복구 환경과 운영 환경의 작업자가 같은 데이터를 동시에 소비하지 않게 한다.
6. Graph 복구와 검색 검증을 마친 뒤 정상 접수·대화·시험·삭제를 점검한다. 관측한 데이터 손실 범위와 재개까지 걸린 시간을 RPO·RTO 결과로 기록하고, 확인되지 않은 구간을 표시한다. 이 저장소는 AWS의 RPO·RTO 수치를 제시하지 않는다.

## Graph 교체 후 원본에서 재구성

Neo4j를 잃었지만 PostgreSQL 원본이 남아 있다면 저장된 메시지와 토픽으로 검색용 Graph를 다시 구성할 수 있다. 명령은 노드를 삭제하지 않으며, 비어 있지 않은 대상 DB에서는 거부한다. 실행 전 대상 URI와 계정을 별도로 확인하고, 웹 쓰기·대화/시험 작업자·Graph 작업자를 중지한다. 실제 운영에서는 유지보수 창이나 검증한 별도 전환 절차가 필요하다.

```sh
# NEO4J_URI는 비어 있는 교체 DB, PostgreSQL은 복구할 정본 DB를 가리켜야 한다.
python -m app.graph_db.rebuild --confirm-empty-target
python -m app.graph_db.worker
```

재구성 명령은 대화별로 PostgreSQL 원본을 잠그고 Graph Outbox를 채운다. 이미 대기 중인 동일 메시지 이벤트는 재사용하며, 과거 메시지는 원본 시각을 사용해 최신 대기 이벤트보다 먼저 처리한다. 기존 삭제 이벤트는 유지하므로 삭제된 원본이 되살아나지 않는다. 재실행 전에 Graph 작업자를 시작하지 않았다면 같은 대기 메시지를 중복 등록하지 않는다. 일부 반영 후 재실행하려면 기존 Outbox를 계속 처리하거나 다시 비어 있는 교체 DB에서 시작한다.

큐가 비면 사용자별 메시지 수·연결 순서·원본 시각·다른 사용자 정보 유출 여부·실제 기억 질문을 검사하고 쓰기를 재개한다. Outbox에 실패가 남아 있으면 정상 복구로 판정하지 않는다. 과거 버전에서 PostgreSQL에 토픽을 저장하지 않았던 기록은 원문 검색용 메시지는 복원할 수 있지만 당시의 토픽 관계·가중치를 정확히 복원할 수 없다. 원본 메시지 순서가 사용자·튜터 쌍으로 정합하지 않으면 자동 재구성을 거부하므로 해당 대화를 먼저 점검한다.

```sql
SELECT count(*) AS pending, min(created_at) AS oldest_source_time,
       max(attempts) AS most_attempts FROM graph_sync_events;
SELECT status, count(*) FROM conversation_turns GROUP BY status;
```

재구성 기간의 Outbox 시각에는 원본 메시지 시각이 들어가므로 이때의 가장 오래된 시각을 평상시 동기화 지연 지표로 해석하지 않는다.
