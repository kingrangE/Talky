# AWS 운영 구성

## 구성과 적용 범위

`runtime.yaml`은 기존 VPC의 서로 다른 두 가용 영역에 있는 public·private subnet을 받아 HTTPS ALB, ECS Fargate 웹 2개·대화/시험 작업자 2개·Graph 작업자 2개, PostgreSQL 16.15 Multi-AZ RDS, 비공개 S3, Secrets Manager 참조, CloudWatch 로그·기본 경보를 정의한다. Terraform 상태 저장소를 새로 운영하는 부담을 줄이고 AWS의 변경 세트 검토를 사용하기 위해 CloudFormation을 선택했다. 실제 자원은 생성하지 않았다. 템플릿 검증은 배포 성공이나 장애 전환 성공을 증명하지 않는다.

웹 연결은 ALB 세션 고정과 공통 Streamlit cookie secret을 사용한다. 웹 프로세스가 사라지면 현재 화면 상태와 로그인이 끊길 수 있지만, 계정으로 다시 로그인해 대화를 선택하면 DB에 접수된 작업과 저장된 기록을 조회할 수 있다. Fargate 서비스는 가용 영역 재균형과 배포 실패 롤백을 사용한다. 관련 동작은 [ECS 가용 영역 재균형](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-rebalancing.html), [ALB 세션 고정](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html)에 근거한다.

PostgreSQL은 Multi-AZ, 저장 암호화, 14일 자동 백업, 삭제 보호를 설정했다. 스택에서 DB를 제거할 때 스냅샷을 남기고 S3와 실행 계정 비밀은 보존한다. 템플릿의 엔진 버전은 배포 리전의 제공 여부를 다시 확인해야 한다. [RDS 백업 보존 정책](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.BackupRetention.html), [RDS PostgreSQL 지원 버전](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-versions.html)을 참고한다.

이 템플릿은 모델 서버와 Neo4j 클러스터를 생성하지 않는다. `OllamaUrl`에는 서로 다른 가용 영역에 배치하고 모델 예열 후에만 트래픽을 받는 GPU 서버 풀의 비공개 엔드포인트를 전달해야 한다. Ollama 포트를 인터넷에 노출하지 않고 애플리케이션 보안 그룹에서만 접근하도록 구성한다. 기본 추론 슬롯 1개는 보수적인 초기값이며, GPU 실측 없이 늘리지 않는다. GPU 한 대의 주소를 전달하면 모델 계층에는 단일 장애점이 남는다.

`Neo4jUri`는 TLS와 라우팅을 사용하는 `neo4j+s://` 관리형 클러스터 주소를 요구한다. 데이터 리전·네트워크와 인증 정보를 별도로 준비한다. Neo4j가 중단되면 기존 검색 코드는 기억 없이 응답하고 Outbox는 반영을 재시도한다. 이때 장기 기억 품질이 저하되므로 큐 적체를 확인해야 한다. 로컬 Compose의 단일 Neo4j를 그대로 사용하면 클러스터 고가용성을 제공하지 않는다.

## 사전 준비

- 두 public subnet과 두 private subnet은 각각 서로 다른 가용 영역에 있어야 한다. private subnet에는 ECR·CloudWatch·Secrets Manager·S3 접근과 외부 Langfuse·모델 자산 접근에 필요한 NAT 또는 VPC endpoint가 있어야 한다. NAT도 가용 영역별로 구성해야 단일 장애점을 줄일 수 있다. VPC와 NAT는 이 템플릿의 생성 대상이 아니다.
- 같은 리전의 ACM 인증서와 경보 수신용 SNS topic ARN, ECR 이미지 digest, 관리형 Neo4j와 모델 엔드포인트를 준비한다. 별도 도메인을 ALB로 연결한다. DNS는 이 템플릿이 변경하지 않는다.
- 기존 Secrets Manager JSON에 `audio_key`, `signing_secret`, `cookie_secret`, `fingerprint_salt`, `neo4j_user`, `neo4j_password`, `langfuse_public_key`, `langfuse_secret_key`를 저장한다. `audio_key`는 `Fernet.generate_key()` 형식이어야 한다. 기존 서비스를 이전할 때는 기존 키와 토큰 서명 비밀을 유지한다. 비밀 값을 저장소·커밋·배포 명령 인자·로그에 넣지 않는다. 사용자 관리 KMS 키를 사용하는 비밀은 실행 역할에 해당 키의 decrypt 권한을 별도로 부여해야 한다.
- `PromptVersions`는 `talky/system`, `talky/ko-coach`, `talky/en-reply`, `talky/better-expression`의 검증한 숫자 버전을 지정한 JSON이다. 평가에서 사용한 정확한 버전을 배포 대상으로 검토한다. 라벨만으로 실행 중인 대화의 버전을 바꾸지 않는다.

비밀은 ECS가 환경 변수로 주입한다. 애플리케이션 실행 역할은 관리자 DB 비밀을 읽지 못하며, 마이그레이션 실행 역할만 관리자 비밀과 실행 계정 비밀에 접근한다. 실행용 PostgreSQL 계정은 데이터 읽기·쓰기만 허용하며 테이블 변경과 마이그레이션 버전 테이블 접근은 허용하지 않는다. [ECS의 Secrets Manager JSON 키 주입 형식](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)을 사용한다.

S3 객체는 애플리케이션 Fernet 암호화와 서버 측 암호화를 함께 사용하고 public 접근과 평문 HTTP를 거부한다. 애플리케이션의 72시간 삭제 작업 외에 4일 객체 수명 주기와 이전 버전 1일 보존을 둔다. 삭제 후에도 이전 암호화 버전이 일정 기간 남을 수 있다. 암호화 키는 DB/S3와 별도로 복구 가능한 비밀 저장소에서 보호하고, 키가 없는 DB·음성 백업만으로 복구가 가능하다고 판단하지 않는다.

## 이미지와 사전 검증

```sh
docker build -f docker/Dockerfile.app -t talky-app:build .
# ECR에 게시한 베이스 이미지의 실제 digest를 사용한다.
docker build -f docker/Dockerfile.release \
  --build-arg APP_BASE_IMAGE=ACCOUNT.dkr.ecr.REGION.amazonaws.com/talky@sha256:BASE_DIGEST \
  -t talky-app:release .
python -m pip install 'cfn-lint>=1,<2'
cfn-lint infrastructure/aws/runtime.yaml
docker compose config --quiet
```

릴리스 이미지는 faster-whisper와 영어 Piper 음성을 이미지 생성 시 준비한다. `HF_HUB_OFFLINE=1`로 실행해 사용자 첫 요청에서 Whisper를 다운로드하지 않는다. 작업자는 큐에서 요청을 가져오기 전에 오디오 엔진과 Ollama 모델을 예열한다. 클러스터의 모든 모델 서버도 각각 예열과 준비 상태 검사를 통과해야 한다. 이 저장소에서 해당 GPU 서버를 생성하거나 이미지를 실제 빌드한 것은 아니다.

## 최초 배포와 업데이트 순서

1. 비용·리전·네트워크·외부 서비스 조건을 검토하고 CloudFormation 변경 세트를 확인한 뒤 `DeployServices=false`로 스택을 생성한다. 서비스 요청 수는 0이므로 스키마가 없는 DB에 웹·작업자가 먼저 접근하지 않는다. 이 단계는 실제 AWS 자원과 비용을 발생시킨다.
2. 새 릴리스 이미지로 마이그레이션 작업을 준비한다. 아래 명령은 기본적으로 계획만 출력하며 AWS에 요청하지 않는다.

```sh
python infrastructure/aws/migrate.py --stack TALKY_STACK --image ECR_IMAGE_WITH_SHA256_DIGEST
# 별도 배포 승인 후에만 실제 실행한다.
python infrastructure/aws/migrate.py --stack TALKY_STACK --image ECR_IMAGE_WITH_SHA256_DIGEST --execute
```

3. 실제 실행기는 관리 계정으로 `app.db.deploy`를 한 번 실행하고 종료 코드를 확인한다. PostgreSQL advisory lock으로 중복 배포 실행을 차단한다. 마이그레이션 이후 실행용 DB 계정을 생성·갱신하고 DML 권한을 부여한다. 마이그레이션 로그에는 비밀번호를 출력하지 않는다. 실패하면 서비스를 활성화하지 않는다.
4. 마이그레이션 성공 후 `ImageUri`를 같은 digest로 지정하고 `DeployServices=true`로 변경 세트를 적용한다. ECS 안정 상태, ALB 정상 대상, 작업자 heartbeat, 실제 로그인·대화·시험·삭제를 확인한다.
5. 이후 릴리스도 새 이미지의 마이그레이션 작업을 먼저 실행하고 같은 이미지로 서비스를 업데이트한다. 기존 버전과 호환되는 확장 마이그레이션을 우선한다. 이번 구조 전환 시에는 기존 독점 작업자를 중지한 뒤 진행한다. 데이터 삭제나 기존 컬럼 의미 변경이 필요한 릴리스는 별도 유지보수 창과 복구 계획을 마련한다. 현재 웹 준비 검사는 스키마 버전 일치를 요구하므로 스키마 변경 배포를 무중단이라고 주장할 수 없다.

Compose도 이제 `migrate` 일회성 서비스가 성공해야 웹을 시작한다. 웹 컨테이너마다 DDL을 동시에 실행하지 않는다. 웹 준비 상태는 Streamlit 응답과 스키마 일치를 확인하고, 작업자 준비 상태는 실제 처리 루프가 갱신하는 heartbeat를 확인한다. 프로세스가 살아 있는 것과 업무 처리가 가능한 것을 구분한다.

## 복구와 용량 검증 기준

| 실험 | 확인할 결과 | 현재 확인 범위 |
| --- | --- | --- |
| 작업 중 프로세스 종료 | 임대 만료 후 인계, 이전 시도 저장 차단, 메시지 중복 없음 | 로컬 PostgreSQL과 실제 자식 프로세스 테스트 |
| Neo4j 반영 실패 | 원본 유지, Outbox 재시도, 삭제 대화 재생성 방지 | 로컬 PostgreSQL 장애 주입 및 실제 Neo4j 재반영 테스트 |
| S3 접근 오류 | 실패를 성공으로 처리하지 않고 삭제 Outbox 유지 | Botocore Stubber, 실제 AWS 미실행 |
| 웹 task 종료 | 재로그인 후 접수한 요청과 저장 기록 복구 | AWS 실험 필요 |
| RDS 강제 failover | 연결 재수립, 접수·결과 누락과 중복 여부, 복구 시간 | AWS 실험 필요 |
| 모델 노드/AZ 중단 | 정상 노드로 라우팅, 예열 지연·VRAM·대기 p95 | 외부 GPU 풀 실험 필요 |
| DB·음성 복구 | 격리된 DB로 복원, 동일 키로 복호화, 계정·대화·시험 검증 | 로컬 복구 실험과 별도 AWS 복구 훈련 필요 |

DB 연결 경보 100개와 여유 저장 공간 5GiB는 시작점이다. 프로세스별 SQLAlchemy pool과 추론 슬롯이 점유하는 연결, ECS 서비스 복제 수를 합산해 RDS 한도보다 여유 있게 운영한다. CPU 사용률만으로 추론 용량을 자동 확장하지 않는다. 작업자 수를 늘려도 추론 슬롯과 GPU가 병목이면 대기만 늘어날 수 있다. 초기에는 슬롯 1개와 작업자 2개로 장애 대기를 두고 실제 p95·실패율을 측정한 뒤 조정한다. CloudWatch 경보에는 SNS 수신 구독 확인도 필요하다.

실제 AWS 자원 생성, 리전별 SKU·할당량·정확한 비용, 이미지 빌드, IAM 실행 권한, Multi-AZ 강제 장애 전환은 이 변경에서 검증하지 않았다. 전체 서비스의 고가용성을 주장하려면 표의 AWS 실험과 외부 모델·Neo4j 계층 준비를 완료해야 한다.

## 프롬프트 원문 캐시

AWS 설정은 `PROMPT_CACHE_ENABLED=true`를 사용한다. 정상 조회한 Langfuse 프롬프트의 이름·숫자 버전·원문·SHA-256을 PostgreSQL에 보관하고, 연결 장애·시간 초과·429·서버 오류일 때만 같은 버전의 검증된 원문을 사용한다. 인증 실패나 없는 버전, 변경된 원문, 변조된 캐시는 대체하지 않는다. 가변 라벨의 최신 버전을 추측하거나 저장소의 기본 프롬프트로 바꾸지 않는다. Langfuse 추적이 실패해도 모델 호출은 계속할 수 있지만 그 호출의 원격 trace는 없을 수 있다.

서비스 활성화 전 같은 실행 환경에서 `python -m app.llm.manage_prompts cache`를 실행해 배포할 네 종류의 프롬프트뿐 아니라 기존 사용자·대화가 참조하는 숫자 버전도 미리 저장한다. 이 명령은 Langfuse 원문을 읽고 PostgreSQL 캐시를 쓰며 원격 프롬프트를 변경하지 않는다. 한 번도 가져오지 않은 원문은 장애 중 복구할 수 없다. 기본 로컬 설정에서는 캐시가 비활성화돼 있으므로 사용하려면 마이그레이션 후 설정을 켜야 한다.
