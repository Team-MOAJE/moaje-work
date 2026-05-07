# moaje-work 기술 가이드 및 컨벤션

`moaje-work` 는 모아제(MoAje) 플랫폼의 AI 소비 패턴 분석 도메인입니다.
대학생의 학사 일정과 소비 데이터를 결합하여 맞춤형 일일 가용 생활비를 산출하고, FDS 이상거래 탐지 및 낭만 달빛 서비스를 제공합니다.

---

## 1. 서비스 개요

`moaje-work` 는 기존 핀테크 서비스가 반영하지 못하는 대학생 고유의 생애주기적 특성을 소비 예측에 통합하는 AI 분석 서비스입니다.

### 주요 역할

- 학사 일정(시험기간, MT, 축제 등) 연계 소비 패턴 분석
- Daily Limit 산출 엔진 (일일 가용 생활비 계산)
- AI 소비 프로필 생성 및 관리
- FDS 이상거래 탐지 및 블랙리스트 관리 (예정)
- 낭만 달빛 서비스 — 위치·날씨·천문 기반 별 관측 명소 추천 (예정)
- Asset 도메인과 Kafka 이벤트 기반 비동기 연동
- gRPC 인터페이스 제공 (GetDailyBudget)

---

## 2. 기술 스택

| 구분 | 기술 |
| --- | --- |
| Language | Python 3.12 |
| Framework | FastAPI 0.115.0 |
| DB | MySQL 8.0 |
| ORM | SQLAlchemy 2.0 (비동기) |
| Cache | Redis |
| Message Broker | Apache Kafka (KRaft 모드) |
| AI / 분석 | Pandas, Scikit-learn, NumPy |
| Container | Docker / Docker Compose |
| Internal API | gRPC (proto: moaje-infra) |

---

## 3. 실행 전 준비

Work 서비스는 Redis와 Kafka를 직접 관리하지 않습니다.
반드시 [moaje-infra](https://github.com/Team-MOAJE/moaje-infra) 를 먼저 실행한 뒤 Work 서비스를 실행해야 합니다.

### 3.1 moaje-infra 실행

    cd moaje-infra-dev
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
| Work API (Swagger) | http://localhost:8001/docs |
| Work API (ReDoc) | http://localhost:8001/redoc |
| Work DB (MySQL) | localhost:3308 |

---

## 4. 환경 변수

`.env.example` 을 참고하여 `.env` 를 구성합니다.

    APP_ENV=development

    DATABASE_URL=mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db

    REDIS_URL=redis://moaje-redis:6379/0

    KAFKA_BOOTSTRAP_SERVERS=kafka:9092

    SECRET_KEY=dev-secret-key-change-in-production

---

## 5. 서비스 간 통신 정책

모아제 프로젝트의 통신 방식은 다음과 같이 구분합니다.

| 구분 | 방식 | 예시 |
| --- | --- | --- |
| 외부 요청 | REST API | Daily Limit 계산, 학사 일정 등록 |
| 내부 서비스 간 요청/응답 | gRPC | GetDailyBudget (Asset → Work) |
| 비동기 이벤트 전달 | Kafka | 분석 완료 이벤트, 거래 수신 |

### Kafka 토픽

| 토픽 | 역할 | 방향 |
| --- | --- | --- |
| `work.spending.analyzed` | Daily Limit 계산 완료 이벤트 | Work → Asset, 알림 |
| `work.schedule.updated` | 학사 일정 등록/수정 이벤트 | Work → Asset |
| `work.fds.alert` | 이상거래 탐지 알림 | Work → Gateway, 알림 |
| `asset.transaction.created` | 거래 발생 이벤트 수신 | Asset → Work |

### Redis 캐시 키

| Key | TTL | 용도 |
| --- | --- | --- |
| `daily_limit:{user_id}` | 300s | Daily Limit 캐시 |
| `event_buffer:{user_id}` | 3600s | 학사 이벤트 버퍼 캐시 |
| `spending_profile:{user_id}` | 1800s | AI 소비 프로필 캐시 |

---

## 6. Daily Limit 산출 공식

Work 서비스의 핵심 기능으로, 학사 이벤트 버퍼를 포함하여 오늘 안전하게 쓸 수 있는 금액을 계산합니다.

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

## 7. API 명세

### 7.1 Daily Limit 계산

    curl -X POST "http://localhost:8001/api/v1/spending/daily-limit" \
      -H "Content-Type: application/json" \
      -d '{
        "user_id": 1001,
        "current_balance": 500000,
        "expected_income": 300000,
        "fixed_expenses": 150000,
        "days_until_payday": 10,
        "event_buffer": 50000
      }'

응답 예시:

    {
      "success": true,
      "message": "ok",
      "user_id": 1001,
      "daily_limit": 60000,
      "advice": "📅 곧 예정된 일정이 있어 50,000원을 미리 빼뒀어요. 오늘은 60,000원까지 안전하게 쓸 수 있어요!",
      "formula_detail": {
        "current_balance": "500000",
        "expected_income": "300000",
        "fixed_expenses": "150000",
        "event_buffer": "50000",
        "days_until_payday": 10,
        "daily_limit": "60000"
      }
    }

### 7.2 AI 소비 프로필 조회

    curl -X GET "http://localhost:8001/api/v1/spending/1001/profile"

### 7.3 학사 이벤트 버퍼 조회

    curl -X GET "http://localhost:8001/api/v1/spending/1001/event-buffer"

### 7.4 학사 일정 등록

    curl -X POST "http://localhost:8001/api/v1/spending/schedule" \
      -H "Content-Type: application/json" \
      -d '{
        "user_id": 1001,
        "event_type": "EXAM",
        "event_name": "2025-1 기말고사",
        "start_date": "2025-06-16",
        "end_date": "2025-06-20",
        "expected_extra_spend": 45000
      }'

### 7.5 학사 일정 목록 조회

    curl -X GET "http://localhost:8001/api/v1/spending/1001/schedules"

사용 가능한 event_type 값:

| 값 | 설명 |
| --- | --- |
| `EXAM` | 시험기간 |
| `MT` | 엠티 |
| `FESTIVAL` | 축제 |
| `VACATION` | 방학 |
| `EMPLOYMENT` | 취업준비 |

---

## 8. DB 테이블 구조

### ai_spending_profile

유저별 AI 소비 패턴 프로필을 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| user_id | BIGINT | Auth 도메인 사용자 ID (논리적 FK) |
| avg_daily_amount | DECIMAL(18,4) | 최근 3개월 평균 일별 지출 |
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

---

## 9. 프로젝트 구조

    moaje-work/
    ├── app/
    │   ├── api/
    │   │   └── v1/
    │   │       ├── endpoints/
    │   │       │   └── spending.py
    │   │       └── router.py
    │   ├── core/
    │   │   └── config.py
    │   ├── db/
    │   │   └── session.py
    │   ├── models/
    │   │   └── spending.py
    │   ├── schemas/
    │   │   └── spending.py
    │   ├── services/
    │   │   └── ai/
    │   │       └── spending_service.py
    │   └── main.py
    ├── scripts/
    │   └── init.sql
    ├── docker-compose.yml
    ├── Dockerfile
    └── requirements.txt

---

## 10. 초기 구현 범위

초기 구현:

- FastAPI 기본 구조 세팅
- MySQL DB 연결 (비동기 SQLAlchemy)
- ai_spending_profile, academic_schedule, ai_analysis_log 테이블
- Daily Limit 산출 엔진
- 학사 일정 CRUD API
- Redis 캐시 연동 준비
- Kafka Producer / Consumer 연동 준비
- Dockerfile 작성

추후 확장:

- FDS 이상거래 탐지 AI 모델
- 낭만 달빛 서비스 (KASI + OpenWeatherMap API)
- gRPC GetDailyBudget 서버 구현
- Kafka 이벤트 발행 / 수신 구현
- Redis 캐시 무효화 전략 적용
- XGBoost 기반 소비 예측 모델 통합

---

## 11. 브랜치 전략

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
