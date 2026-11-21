# 대화 목록과 턴 인덱스 재현 및 운영 관측

## 합성 PostgreSQL 16에서 실행 계획 비교

저장소 루트에서 별도의 빈 `talky_indexes_test` 데이터베이스를 사용한다. `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`를 그 DB에 맞게 설정하고 `psql`도 같은 DB에 연결한다. 앱 의존성을 설치한 환경에서 `python -m alembic upgrade 0012_conversation_starter`를 실행한 뒤 아래 데이터를 한 번 적재한다.

```sql
BEGIN;

INSERT INTO users (id)
SELECT md5('user' || n)::uuid FROM generate_series(1, 1000) AS n;

INSERT INTO conversations (id, user_id, title, started_at)
SELECT md5('conversation' || n)::uuid, md5('user' || (1 + (n - 1) % 1000))::uuid,
       repeat('title ', 10), timestamptz '2026-01-01 UTC' + ((n - 1) / 2000) * interval '1 day'
FROM generate_series(1, 100000) AS n;

INSERT INTO conversation_turns
    (id, conversation_id, user_id, input_digest, input_text, status, result,
     queue_deadline, audio_expires_at, created_at, updated_at)
SELECT md5('turn' || n)::uuid, md5('conversation' || cid)::uuid, md5('user' || cid)::uuid,
       md5(n::text), repeat('input ', 20),
       CASE WHEN n <= 400 AND n % 2 = 0 THEN 'queued'
            WHEN n <= 400 THEN 'processing'
            WHEN n / 400 % 10 = 0 THEN 'failed' ELSE 'completed' END,
       CASE WHEN n > 400 THEN jsonb_build_object('ai_reply', repeat('reply ', 30)) END,
       ts + interval '5 min', ts + interval '1 day', ts, ts
FROM generate_series(1, 100000) AS n
CROSS JOIN LATERAL (
    SELECT 1 + (n - 1) % 400 AS cid,
           timestamptz '2026-01-01 UTC' + ((n - 1) / 800) * interval '1 minute' AS ts
) AS sample;

COMMIT;
ANALYZE conversations;
ANALYZE conversation_turns;
```

각 단계에서 같은 SQL을 한 번 예열하고 다섯 번 기록한다. 첫 번째 측정은 `0012_conversation_starter`에서, 두 번째는 `python -m alembic upgrade head` 이후 같은 데이터에서 실행한다. 날짜와 UUID가 모두 정렬에 참여하며 목록은 한 사용자의 100개 중 50개, 상세는 한 대화의 250개 중 200개를 조회한다. 실제 API와 같은 전체 행 투영을 사용한다. PostgreSQL 기본 스캔 설정을 유지하고, 계획 상단의 실행 노드에서 shared hit와 read를 합산한다. 하위 노드에는 중복 집계가 포함된다. 서버 `Execution Time`의 중앙값과 인덱스 크기를 함께 기록한다.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT c.* FROM conversations AS c
WHERE c.user_id = md5('user1')::uuid
ORDER BY c.started_at DESC, c.id ASC LIMIT 50 OFFSET 0;

EXPLAIN (ANALYZE, BUFFERS)
SELECT t.* FROM conversation_turns AS t
WHERE t.conversation_id = md5('conversation1')::uuid
ORDER BY t.created_at DESC, t.id DESC LIMIT 200;

SELECT indexrelname, pg_relation_size(indexrelid) AS bytes
FROM pg_stat_user_indexes
WHERE schemaname = current_schema()
  AND indexrelname IN ('ix_conversations_user_started_id', 'ix_conversation_turns_conversation_created_id');
```

전용 테스트 DB의 `postgresql+psycopg` DSN으로 `TALKY_TEST_POSTGRES_DSN`을 설정하고 `python -m pytest -q tests/test_operational_migration.py tests/test_schema_contract.py`를 실행하면 별도 스키마에서 행, 목록·상세의 정렬, 잠금 실패 후 롤백, 업그레이드·다운그레이드·재업그레이드를 검증한다.

## 마이그레이션 대기와 실패 확인

개별 인덱스 생성은 읽기를 허용하고 대상 테이블 쓰기와 충돌한다. 다운그레이드의 일반 `DROP INDEX`는 대상 테이블에서 `ACCESS EXCLUSIVE` 잠금을 사용한다. 잠금 획득 대기는 각 요청당 5초로 제한된다. 획득 후 빌드 시간에는 이 제한이 적용되지 않으므로, 운영 규모에 가까운 사본에서 빌드 시간과 디스크 여유를 확인한다. Lightsail 릴리스는 마이그레이션 전에 웹과 worker를 중지한다. 별도 클라이언트의 장기 트랜잭션도 있으면 `pg_stat_activity`, `pg_locks`, `pg_blocking_pids()`로 대기 원인을 확인한다.

잠금 타임아웃 등으로 두 번째 인덱스 DDL이 실패하면 PostgreSQL 트랜잭션이 첫 번째 DDL과 버전 변경도 롤백한다. 실패 로그를 확인하고 `alembic current` 및 `pg_indexes`로 이전 상태인지 확인한 뒤 차단 원인이 해소되면 기존 배포 절차로 재시도한다. 앱 버전 복구에는 기존 배포기의 스키마 호환성 확인을 사용한다. 기존 앱이 사용할 컬럼과 타입이 유지되므로 인덱스를 제거하기 위해 DB 다운그레이드를 먼저 실행할 필요는 없다. [PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/16/sql-createindex.html) · [PostgreSQL DROP INDEX](https://www.postgresql.org/docs/16/sql-dropindex.html) · [Lightsail 배포 절차](../infrastructure/lightsail/README.md)

## 운영에서 읽기와 쓰기 비용 관측

대상 DB에서 다음 읽기 전용 점검으로 preload와 확장을 확인한다. 둘 중 하나가 빠졌으면 현재는 쿼리별 집계를 얻을 수 없다. 활성화가 필요할 때는 DB 관리자와 별도로 계획한다. `shared_preload_libraries` 설정 변경에는 재시작이 필요하며, 확장은 대상 DB에 설치해야 한다. 확장 없이도 아래 인덱스·테이블 통계를 수집할 수 있다. [pg_stat_statements](https://www.postgresql.org/docs/16/pgstatstatements.html)

```sql
SHOW shared_preload_libraries;
SELECT extversion FROM pg_extension WHERE extname = 'pg_stat_statements';

SELECT now(), pg_postmaster_start_time(), stats_reset
FROM pg_stat_database WHERE datname = current_database();

SELECT relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch, pg_relation_size(indexrelid) AS bytes
FROM pg_stat_user_indexes
WHERE schemaname = current_schema() AND relname IN ('conversations', 'conversation_turns');

SELECT relname, seq_scan, seq_tup_read, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del
FROM pg_stat_user_tables
WHERE schemaname = current_schema() AND relname IN ('conversations', 'conversation_turns');

SELECT wal_records, wal_bytes, stats_reset FROM pg_stat_wal;
```

확장이 활성화된 DB에서 운영자가 아래 집계를 확인한다. 앱 역할과 대상 DB의 목록·턴 SQL에 대응하는 `queryid`를 찾는다. 배포 전후 각각 트래픽과 길이가 비슷한 구간에서 시작·종료 스냅샷을 남긴다. 각 구간의 `calls`, `total_exec_time`, shared·temporary block 증가량을 비교하고, 실행 시간 증가량을 호출 수 증가량으로 나눈다. `stats_reset`이 달라지거나 항목이 퇴거돼 기준점이 사라진 구간은 다시 잡는다. 다른 역할의 쿼리 텍스트를 읽으려면 `pg_read_all_stats` 등 기존에 승인된 통계 권한을 사용한다.

```sql
SELECT stats_reset, dealloc FROM pg_stat_statements_info;

SELECT userid::regrole, queryid, calls, total_exec_time, shared_blks_hit, shared_blks_read,
       temp_blks_read, temp_blks_written, query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  AND (query ILIKE '%from conversations%' OR query ILIKE '%from conversation_turns%')
  AND query ILIKE '%order by%' AND query ILIKE '%limit%'
ORDER BY total_exec_time DESC;
```

쓰기량이 비슷한 구간에 새 인덱스 크기와 DB 쓰기 지연 및 WAL을 함께 본다. `pg_stat_wal`은 클러스터 전체 통계이므로 대상 쓰기 쿼리를 따로 비교할 때는 `pg_stat_statements`의 해당 `queryid`에서 `calls`, `total_exec_time`, `wal_bytes`의 증가량을 사용한다. 각 인덱스는 새 행마다 엔트리를 추가하며, 인덱스 키가 바뀌는 갱신에도 비용이 생긴다. 인덱스 읽기 카운터는 역할과 최근 사용 구간을 함께 해석한다. 빈 큐나 드물게 도는 보존 작업, 유일성 제약의 인덱스는 `idx_scan = 0`만으로 삭제를 결정할 수 없다. [PostgreSQL 누적 통계](https://www.postgresql.org/docs/16/monitoring-stats.html)
