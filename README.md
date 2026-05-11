# moaje-work 기술 가이드 및 컨벤션

`moaje-work` 는 모아제(MoAje) 플랫폼의 AI 소비 패턴 분석 도메인입니다.
대학생의 학사 일정과 소비 데이터를 결합하여 맞춤형 일일 가용 생활비를 산출하고, 하이브리드 적응형 FDS 이상거래 탐지 서비스를 제공합니다.

---

## 1. 서비스 개요

`moaje-work` 는 기존 핀테크 서비스가 반영하지 못하는 대학생 고유의 생애주기적 특성을 소비 예측에 통합하는 AI 분석 서비스입니다.

### 주요 역할

- 학사 일정(시험기간, MT, 축제 등) 연계 소비 패턴 분석
- Daily Limit 산출 엔진 — Asset 도메인이 gRPC GetDailyBudget으로 호출
- AI 소비 프로필 생성 및 관리
- 하이브리드 적응형 FDS 이상거래 탐지 (Rule-based + Z-score + XGBoost ML)
- 블랙리스트 관리
- Asset 도메인과 Kafka 이벤트 기반 비동기 연동
- gRPC 인터페이스 제공 (GetDailyBudget, CheckBlacklist)

---

## 2. 기술 스택

| 구분 | 기술 |
| --- | --- |
| Language | Python 3.12 |
| Framework | FastAPI 0.115.0 |
| DB | MySQL 8.0 |
| ORM | SQLAlchemy 2.0 (비동기) |
| Cache | Redis |
| Message Broker | Apache Kafka 4.0.2 (KRaft 모드) |
| AI / 분석 | Pandas, Scikit-learn, NumPy, XGBoost |
| Container | Docker / Docker Compose |
| Internal API | gRPC (proto: moaje-grpc-contracts) |

---

## 3. 실행 전 준비

Work 서비스는 Redis와 Kafka를 직접 관리하지 않습니다.
반드시 [moaje-infra](https://github.com/Team-MOAJE/moaje-infra) 를 먼저 실행한 뒤 Work 서비스를 실행해야 합니다.

### 3.1 moaje-infra 실행

    cd moaje-infra
    docker compose up -d

실행 후 확인:

| 서비스 | 접속 주소 |
| --- | --- |
| Kafka UI | http://localhost:8989 |
| Redis | localhost:6379 |
| Kafka | localhost:9092 |

### 3.2 Work 서비스 실행

    cd moaje-work
    docker compose up -d --build

실행 후 확인:

| 서비스 | 접속 주소 |
| --- | --- |
| Work API (Swagger) | http://localhost:8084/docs |
| Work API (ReDoc) | http://localhost:8084/redoc |
| Work DB (MySQL) | localhost:3308 |

---

## 4. 환경 변수

`.env.example` 을 참고하여 `.env` 를 구성합니다.

    APP_ENV=development

    DATABASE_URL=mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db

    REDIS_URL=redis://moaje-redis:6379/0

    KAFKA_BOOTSTRAP_SERVERS=moaje-kafka:9092

    SECRET_KEY=dev-secret-key-change-in-production

---

## 5. 서비스 간 통신 정책

모아제 프로젝트의 통신 방식은 다음과 같이 구분합니다.

| 구분 | 방식 | 예시 |
| --- | --- | --- |
| 외부 요청 | REST API | 학사 일정 등록, FDS 탐지 |
| 내부 서비스 간 요청/응답 | gRPC | GetDailyBudget (Asset → Work) |
| 비동기 이벤트 전달 | Kafka | 분석 완료 이벤트, 거래 수신 |

### Kafka 토픽

| 토픽 | 역할 | 방향 |
| --- | --- | --- |
| `work.spending.analyzed` | Daily Limit 계산 완료 이벤트 | Work → Asset, 알림 |
| `work.schedule.updated` | 학사 일정 등록/수정 이벤트 | Work → Asset |
| `work.fds.alert` | 이상거래 탐지 알림 | Work → Gateway, 알림 |
| `asset.balance.deducted` | 거래 발생 이벤트 수신 | Asset → Work |
| `asset.daily.budget.updated` | 일일 예산 업데이트 수신 | Asset → Work |
| `auth.user.registered` | 신규 유저 가입 이벤트 수신 | Auth → Work |

### Redis 캐시 키

| Key | TTL | 용도 |
| --- | --- | --- |
| `daily_limit:{user_id}` | 300s | Daily Limit 캐시 |
| `event_buffer:{user_id}` | 3600s | 학사 이벤트 버퍼 캐시 |
| `spending_profile:{user_id}` | 1800s | AI 소비 프로필 캐시 |

---

## 6. Daily Limit 산출 공식

학사 이벤트 버퍼를 포함하여 오늘 안전하게 쓸 수 있는 금액을 계산합니다.
Asset 도메인이 gRPC GetDailyBudget으로 호출하면 Work 서비스가 산출하여 응답합니다.

    Daily_Limit = (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼)
                  ÷ 월급날까지 남은 일수

각 변수 설명:

| 변수 | 설명 |
| --- | --- |
| 현재 잔고 | Asset 도메인이 보유한 실시간 계좌 잔액 |
| 예상 알바비 | 예정된 수입 |
| 고정 지출 | 월세, 통신비 등 반복 고정 지출 |
| 이벤트 버퍼 | 7일 이내 학사 이벤트의 expected_extra_spend 합산 |
| 월급날까지 남은 일수 | 다음 수입 예정일까지의 잔여 일수 |

---

## 7. FDS 이상거래 탐지

### 7.1 하이브리드 적응형 구조

거래 건수(tx_count)에 따라 Rule-based, Z-score 개인화, XGBoost ML 세 가지 방식의 비중을 자동으로 조정합니다.

| 거래 건수 | Rule-based | Z-score 개인화 | XGBoost ML |
| --- | --- | --- | --- |
| 0 ~ 10건 | 100% | 0% | 0% |
| 11 ~ 30건 | 70% | 30% | 0% |
| 31 ~ 70건 | 30% | 40% | 30% |
| 71건 이상 | 10% | 10% | 80% |

신규 유저는 Rule-based 100%로 즉시 보호하고, 거래 데이터가 쌓일수록 개인 패턴 기반의 정밀 탐지로 자동 전환됩니다.

### 7.2 Rule-based 탐지 룰

| 룰 | 조건 | 가중치 |
| --- | --- | --- |
| Rule 1. 이상 금액 | 유저 평균 일별 지출 대비 3배 이상 | 최대 +0.5 |
| Rule 2. 이상 시간대 | 새벽 02:00 ~ 05:00 사이 거래 | +0.3 |
| Rule 3. 단시간 반복 거래 | 10분 내 3회 이상 거래 | +0.4 |

### 7.3 Risk Level 판정

| Risk Level | 범위 | 조치 |
| --- | --- | --- |
| LOW | 0.0 ~ 0.4 | 정상 통과 |
| MEDIUM | 0.4 ~ 0.7 | 주의 (추후 2단계 인증 연동 예정) |
| HIGH | 0.7 ~ 1.0 | 즉시 차단 + Kafka work.fds.alert 발행 |

최대 risk_score는 1.0으로 상한선 고정됩니다.

### 7.4 XGBoost ML 모델

합성 데이터(10,000건)로 학습한 XGBoost 이진 분류 모델입니다.

| 항목 | 내용 |
| --- | --- |
| 모델 | XGBoost 2.1.1 |
| 학습 데이터 | 합성 데이터 10,000건 (정상 8,000 / 이상 2,000) |
| AUC-ROC | 1.0000 |
| 피처 | amount_zscore, amount_ratio, hour, is_night, tx_count, recent_tx_10min, day_of_week, has_event_7days, days_to_event |
| 모델 파일 | app/services/fds/fds_model.pkl |

---

## 8. API 명세

### 8.1 AI 소비 프로필 조회

    curl -X GET "http://localhost:8084/api/work/spending/1001/profile"

### 8.2 학사 이벤트 버퍼 조회

    curl -X GET "http://localhost:8084/api/work/spending/1001/event-buffer"

### 8.3 학사 일정 등록

    curl -X POST "http://localhost:8084/api/work/spending/schedule" \
      -H "Content-Type: application/json" \
      -d '{
        "user_id": 1001,
        "event_type": "EXAM",
        "event_name": "2025-1 기말고사",
        "start_date": "2025-06-16",
        "end_date": "2025-06-20",
        "expected_extra_spend": 45000
      }'

### 8.4 학사 일정 목록 조회

    curl -X GET "http://localhost:8084/api/work/spending/1001/schedules"

사용 가능한 event_type 값:

| 값 | 설명 |
| --- | --- |
| `EXAM` | 시험기간 |
| `MT` | 엠티 |
| `FESTIVAL` | 축제 |
| `VACATION` | 방학 |
| `EMPLOYMENT` | 취업준비 |

### 8.5 FDS 이상거래 탐지

    curl -X POST "http://localhost:8084/api/work/fds/detect" \
      -H "Content-Type: application/json" \
      -d '{
        "user_id": 1001,
        "transaction_id": "tx-001",
        "amount": 200000,
        "merchant": "쿠팡",
        "hour": 3
      }'

응답 예시:

    {
      "user_id": 1001,
      "transaction_id": "tx-001",
      "risk_score": "0.80",
      "risk_level": "HIGH",
      "reason_code": "RULE_ABNORMAL_AMOUNT(avg:35,000원 대비 200,000원) | RULE_ABNORMAL_TIME(hour:3시) | ML_HIGH_RISK(prob=1.00)",
      "is_alerted": true,
      "message": "🚨 이상거래가 탐지되었습니다. 즉시 확인이 필요합니다."
    }

### 8.6 블랙리스트 조회

    curl -X GET "http://localhost:8084/api/work/fds/1001/blacklist"

### 8.7 블랙리스트 등록

    curl -X POST "http://localhost:8084/api/work/fds/blacklist" \
      -H "Content-Type: application/json" \
      -d '{
        "user_id": 1001,
        "reason": "ABNORMAL_AMOUNT",
        "description": "이상 금액 반복 탐지"
      }'

### 8.8 블랙리스트 해제

    curl -X DELETE "http://localhost:8084/api/work/fds/1001/blacklist"

### 8.9 FDS 탐지 이력 조회

    curl -X GET "http://localhost:8084/api/work/fds/1001/logs"

---

## 9. DB 테이블 구조

### ai_spending_profile

유저별 AI 소비 패턴 프로필을 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| avg_daily_amount | DECIMAL(18,4) | 최근 3개월 평균 일별 지출 |
| std_daily_amount | DECIMAL(18,4) | 일별 지출 표준편차 (Z-score FDS용) |
| tx_count | INT | 누적 거래 건수 (하이브리드 FDS 가중치 기준) |
| peak_spend_hour | SMALLINT | 주요 지출 시간대 (0~23) |
| top_category | VARCHAR(50) | 최다 지출 카테고리 |
| risk_score_baseline | DECIMAL(5,2) | FDS 기준 위험 점수 |
| last_analyzed_at | DATETIME | AI 분석 마지막 실행 시각 |

### academic_schedule

학사 이벤트를 저장합니다. 지출 예측에 자동 반영됩니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| event_type | ENUM | EXAM / MT / FESTIVAL / VACATION / EMPLOYMENT |
| event_name | VARCHAR(100) | 이벤트명 |
| start_date | DATE | 이벤트 시작일 |
| end_date | DATE | 이벤트 종료일 |
| expected_extra_spend | DECIMAL(18,4) | 예상 추가 지출 |

### ai_analysis_log

AI 분석 실행 이력 및 결과 메시지를 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| analysis_type | ENUM | DAILY_LIMIT / PATTERN_UPDATE / SCHEDULE_ALERT |
| input_snapshot | JSON | 분석 시점의 입력 데이터 스냅샷 |
| result_message | VARCHAR(500) | AI 생성 조언 메시지 |
| daily_limit | DECIMAL(18,4) | 산출된 일일 가용 금액 |
| confidence_score | DECIMAL(5,4) | AI 신뢰도 점수 (0~1) |

### fds_inference_log

FDS 탐지 실행 로그를 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| transaction_id | VARCHAR(100) | 거래 고유 ID |
| amount | DECIMAL(18,4) | 거래 금액 |
| risk_score | DECIMAL(5,4) | 최종 risk_score (0~1) |
| risk_level | ENUM | LOW / MEDIUM / HIGH |
| reason_code | VARCHAR(200) | 탐지 사유 |
| is_alerted | TINYINT | Kafka alert 발행 여부 |

### fds_blacklist

FDS 블랙리스트를 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| reason | ENUM | ABNORMAL_AMOUNT / ABNORMAL_TIME / RAPID_REPEAT / MANUAL |
| description | VARCHAR(300) | 차단 사유 설명 |
| is_active | TINYINT | 현재 차단 상태 여부 |
| registered_at | DATETIME | 등록 시각 |
| released_at | DATETIME | 해제 시각 |

---

## 10. 프로젝트 구조

    moaje-work/
    ├── app/
    │   ├── api/
    │   │   └── v1/
    │   │       ├── endpoints/
    │   │       │   ├── spending.py
    │   │       │   └── fds.py
    │   │       └── router.py
    │   ├── core/
    │   │   └── config.py
    │   ├── db/
    │   │   └── session.py
    │   ├── grpc/
    │   │   └── server.py
    │   ├── kafka/
    │   │   ├── producer.py
    │   │   └── consumer.py
    │   ├── models/
    │   │   ├── spending.py
    │   │   └── fds.py
    │   ├── redis/
    │   │   └── client.py
    │   ├── schemas/
    │   │   ├── spending.py
    │   │   └── fds.py
    │   ├── services/
    │   │   ├── ai/
    │   │   │   └── spending_service.py
    │   │   └── fds/
    │   │       ├── detector.py
    │   │       └── fds_model.pkl
    │   └── main.py
    ├── scripts/
    │   └── init.sql
    ├── docker-compose.yml
    ├── Dockerfile
    └── requirements.txt

---

## 11. 구현 완료 범위

완료:

- FastAPI 프로젝트 구조 및 GitHub 연결
- MySQL DB + SQLAlchemy 2.0 비동기 ORM
- AI 소비 패턴 분석 API 4개
- 하이브리드 적응형 FDS 이상거래 탐지 API 5개
- XGBoost ML 모델 학습 및 연동 (AUC 1.0)
- Kafka Producer 3개 토픽 발행
- Kafka Consumer 3개 토픽 구독
- Redis 캐시 연동 (HIT/MISS/INVALIDATE)
- gRPC WorkService 인터페이스 구현
- moaje-grpc-contracts proto PR 제출 완료

추후 확장:

- 실제 유저 데이터 기반 ML 모델 재학습
- FDS MEDIUM 2단계 인증 연동 (Auth MFA)
- gRPC 실 연동 테스트 (Asset 도메인 연동 후)
- 낭만 달빛 서비스 (KASI + OpenWeatherMap API)

---

## 12. 브랜치 전략

작업 시작 전 반드시 dev 브랜치 최신 상태를 유지합니다.

    git checkout dev
    git pull origin dev

기능 브랜치 생성:

    git checkout -b feat/기능이름

작업 완료 후 push:

    git add .
    git commit -m "feat: 기능 설명"
    git push origin feat/기능이름

GitHub에서 `feat/*` → `dev` 방향으로 Pull Request를 생성합니다.

커밋 메시지 규칙:

| 태그 | 설명 |
| --- | --- |
| `feat` | 새 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 코드 리팩토링 |
| `docs` | 문서 수정 |
| `chore` | 설정 · 빌드 변경 |
