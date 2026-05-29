# moaje-work 기술 가이드 및 컨벤션

`moaje-work` 는 모아제(MoAje) 플랫폼의 AI 소비 패턴 분석 도메인입니다.
대학생의 학사 일정과 소비 데이터를 결합하여 맞춤형 일일 가용 생활비를 산출하고, 하이브리드 적응형 FDS 이상거래 탐지 서비스를 제공합니다.

---

## 1. 서비스 개요

`moaje-work` 는 기존 핀테크 서비스가 반영하지 못하는 대학생 고유의 생애주기적 특성을 소비 예측에 통합하는 AI 분석 서비스입니다.

### 주요 역할

- 학사 일정(시험기간·MT·축제 등) 연계 소비 패턴 분석
- Daily Limit 산출 엔진 — Asset 도메인이 gRPC GetDailyBudget으로 호출
- AI 소비 프로필 생성 및 관리
- 하이브리드 적응형 FDS 이상거래 탐지 (Rule-based + Z-score + XGBoost ML)
- 이상거래 알림 시스템 및 소비 안전도 점수
- 블랙리스트 관리
- 학교 선택 → 학사 일정 자동 연동
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
| Message Broker | Apache Kafka 4.0.2 (KRaft) |
| AI / 분석 | Pandas · Scikit-learn · NumPy · XGBoost 2.1.1 |
| PK 채번 | TSID (tsidpy) |
| Container | Docker / Docker Compose |
| Internal API | gRPC (proto: moaje-grpc-contracts) |
| Architecture | MSA |

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
| Work DB (MySQL) | localhost:3309 |
| gRPC | localhost:50051 |

---

## 4. 환경 변수

`.env.example` 을 참고하여 `.env` 를 구성합니다.

    APP_ENV=development
    DATABASE_URL=mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db
    REDIS_URL=redis://moaje-redis:6379/0
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    SECRET_KEY=dev-secret-key-change-in-production

---

## 5. 서비스 간 통신 정책

| 구분 | 방식 | 예시 |
| --- | --- | --- |
| 외부 요청 | REST API | 학사 일정 등록, FDS 탐지 |
| 내부 서비스 간 요청/응답 | gRPC | GetDailyBudget (Asset → Work) |
| 비동기 이벤트 전달 | Kafka | 거래 발생, FDS 알림 발행 |

### Kafka 토픽

| 토픽 | 역할 | 방향 |
| --- | --- | --- |
| `work.spending.analyzed` | Daily Limit 계산 완료 이벤트 | Work → Asset |
| `work.schedule.updated` | 학사 일정 등록/수정 이벤트 | Work → Asset |
| `work.fds.alert` | 이상거래 탐지 알림 | Work → 알림 서버 |
| `transaction_created_events` | Banking 거래 완료 → FDS 자동 분석 | Banking → Work |
| `asset.balance.deducted` | 거래 발생 이벤트 수신 | Asset → Work |
| `asset.daily.budget.updated` | 일일 예산 업데이트 수신 | Asset → Work |
| `auth.user.registered` | 신규 유저 가입 이벤트 수신 | Auth → Work |

### Redis 캐시 전략

Cache-Aside (Lazy Loading) 패턴 적용. Redis 장애 시 DB fallback, DB 장애 시 캐시 반환.

| Key | TTL | 용도 | 무효화 시점 |
| --- | --- | --- | --- |
| `daily_limit:{user_id}` | 300s | Daily Limit 캐시 | 거래 발생 시 |
| `event_buffer:{user_id}` | 3600s | 학사 이벤트 버퍼 캐시 | 학사 일정 변경 시 |
| `spending_profile:{user_id}` | 1800s | AI 소비 프로필 캐시 | 거래 발생 시 |

---

## 6. Daily Limit 산출 공식

    Daily_Limit = (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼)
                  ÷ 월급날까지 남은 일수

| 변수 | 설명 |
| --- | --- |
| 현재 잔고 | Asset 도메인이 보유한 실시간 계좌 잔액 |
| 예상 알바비 | 예정된 수입 |
| 고정 지출 | 월세, 통신비 등 반복 고정 지출 |
| 이벤트 버퍼 | **7일 이내 학사 이벤트의 expected_extra_spend 합산** |
| 월급날까지 남은 일수 | 다음 수입 예정일까지의 잔여 일수 |

---

## 7. FDS 이상거래 탐지

### 7.1 하이브리드 적응형 구조

거래 건수(tx_count)에 따라 세 가지 방식의 비중을 자동으로 조정합니다.

| 거래 건수 | Rule-based | Z-score 개인화 | XGBoost ML |
| --- | --- | --- | --- |
| 0 ~ 10건 | 100% | 0% | 0% |
| 11 ~ 30건 | 70% | 30% | 0% |
| 31 ~ 70건 | 30% | 40% | 30% |
| 71건 이상 | 10% | 10% | 80% |

### 7.2 Rule-based 탐지 룰

| 룰 | 조건 | 기준 | 가중치 |
| --- | --- | --- | --- |
| Rule 1. 이상 금액 | Z-score 3σ 이상 | JPMorgan Chase 3-sigma rule (FATF 국제 표준) | 최대 +0.5 |
| Rule 2. 이상 시간대 | 새벽 02:00 ~ 05:00 | — | +0.3 |
| Rule 3. 단시간 반복 | 10분 내 3회 이상 | — | +0.4 |

> Rule 1 참고: [JPMorgan Chase Engineering Blog](https://medium.com/next-at-chase/cutting-time-to-detect-customer-impact-with-z-score-anomaly-detection-8dd03cbd9227)

### 7.3 XGBoost ML 모델

| 항목 | 내용 |
| --- | --- |
| 학습 데이터 | 합성 데이터 10,000건 (정상 8,000 / 이상 2,000) |
| AUC-ROC | **1.0000** |
| 피처 | amount_zscore, amount_ratio, hour, is_night, tx_count, recent_tx_10min, day_of_week, has_event_7days, days_to_event |
| 모델 파일 | app/services/fds/fds_model.pkl |

### 7.4 Risk Level 판정

| Level | 범위 | 조치 |
| --- | --- | --- |
| LOW | 0.0 ~ 0.4 | 정상 통과 |
| MEDIUM | 0.4 ~ 0.7 | 주의 |
| HIGH | 0.7 ~ 1.0 | Kafka `work.fds.alert` 발행 + fds_alert_log 자동 생성 |

### 7.5 소비 안전도 점수

최근 30일 FDS 탐지 이력 기반 0~100점 산출.

| 등급 | 점수 | 의미 |
| --- | --- | --- |
| A 🟢 | 90~100 | 매우 안전 |
| B 🟡 | 70~89 | 양호 |
| C 🟠 | 50~69 | 주의 |
| D 🔴 | 0~49 | 위험 |

---

## 8. gRPC 인터페이스

    service WorkService {
      rpc GetDailyBudget(GetDailyBudgetRequest) returns (GetDailyBudgetResponse);
      rpc CheckBlacklist(CheckBlacklistRequest) returns (CheckBlacklistResponse);
    }

| RPC | 호출 도메인 | 설명 |
| --- | --- | --- |
| GetDailyBudget | Asset | 학사 이벤트 버퍼 포함 일일 가용 생활비 산출 |
| CheckBlacklist | Gateway | FDS 블랙리스트 등록 여부 확인 |

---

## 9. API 목록 (총 15개)

### 소비 패턴 분석

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/v1/work/spending/{uid}/profile` | AI 소비 프로필 조회 |
| GET | `/api/v1/work/spending/{uid}/event-buffer` | 7일 이내 이벤트 버퍼 조회 |
| POST | `/api/v1/work/spending/schedule` | 학사 일정 수동 등록 |
| GET | `/api/v1/work/spending/{uid}/schedules` | 학사 일정 전체 목록 |

### 학사 일정 자동 연동

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/v1/work/calendar/universities` | 지원 학교 목록 (10개교) |
| POST | `/api/v1/work/calendar/sync` | 학교 선택 → 학사 일정 자동 동기화 |
| GET | `/api/v1/work/calendar/{uid}/schedules` | 전체 일정 조회 (자동+수동) |

### FDS 이상거래 탐지

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/v1/work/fds/detect` | 하이브리드 이상거래 탐지 |
| GET | `/api/v1/work/fds/{uid}/logs` | FDS 탐지 로그 이력 |
| GET | `/api/v1/work/fds/{uid}/alerts` | 이상거래 알림 목록 |
| PATCH | `/api/v1/work/fds/{uid}/alerts/{id}/confirm` | 알림 확인 처리 |
| GET | `/api/v1/work/fds/{uid}/safety-score` | 소비 안전도 점수 |
| GET | `/api/v1/work/fds/{uid}/blacklist` | 블랙리스트 조회 |
| POST | `/api/v1/work/fds/blacklist` | 블랙리스트 등록 |
| DELETE | `/api/v1/work/fds/{uid}/blacklist` | 블랙리스트 해제 |

---

## 10. DB 테이블 구조 (8개)

| 테이블 | 설명 | PK 채번 |
| --- | --- | --- |
| `ai_spending_profile` | AI 소비 패턴 프로필 (avg, std, tx_count) | TSID |
| `academic_schedule` | 사용자 학사 이벤트 (자동/수동) | TSID |
| `academic_calendar` | 학교별 학사 일정 마스터 | TSID |
| `university` | 대학교 목록 (10개교) | TSID |
| `ai_analysis_log` | AI 분석 이력 | TSID |
| `fds_inference_log` | FDS 탐지 로그 | TSID |
| `fds_alert_log` | 이상거래 알림 (is_confirmed) | TSID |
| `fds_blacklist` | 블랙리스트 | TSID |

### ai_spending_profile 주요 컬럼

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| avg_daily_amount | DECIMAL(18,4) | 평균 일별 지출 |
| std_daily_amount | DECIMAL(18,4) | 표준편차 (Z-score FDS용) |
| tx_count | INT | 누적 거래 건수 (하이브리드 FDS 가중치 기준) |
| university_id | INT | 연동된 학교 |

---

## 11. 장애 대응 전략

금융 앱 가용성을 최우선으로 Redis·DB 2단계 fallback 전략을 적용합니다.

| 상황 | 대응 |
| --- | --- |
| Redis 장애 | DB에서 직접 조회 (fail-open) |
| DB 장애 | Redis 캐시 데이터 반환 |
| 둘 다 장애 | 기본값 반환 후 503 응답 |
| Kafka 장애 | 10초마다 자동 재시도 |
| ML 모델 없음 | Rule-based fallback |

### 전역 예외 핸들러

| 예외 | HTTP 코드 |
| --- | --- |
| RequestValidationError | 422 |
| DB 연결 실패 | 503 |
| DB 쿼리 오류 | 500 |
| Redis 연결 실패 | 503 |
| gRPC 타임아웃 | 504 |
| ValueError | 400 |
| 기타 모든 예외 | 500 |

---

## 12. 프로젝트 구조

    moaje-work/
    ├── app/
    │   ├── api/v1/endpoints/
    │   │   ├── spending.py          # 소비 패턴 분석 API
    │   │   ├── fds.py               # FDS 이상거래 탐지 API
    │   │   └── calendar.py          # 학사 일정 자동 연동 API
    │   ├── core/config.py
    │   ├── db/session.py
    │   ├── grpc/
    │   │   ├── server.py            # gRPC 서버 (GetDailyBudget, CheckBlacklist)
    │   │   ├── work_service_pb2.py
    │   │   └── work_service_pb2_grpc.py
    │   ├── kafka/
    │   │   ├── producer.py
    │   │   └── consumer.py          # Banking FDS 자동 분석 포함
    │   ├── models/
    │   │   ├── spending.py          # TSID PK
    │   │   └── fds.py               # TSID PK
    │   ├── redis/client.py          # Redis fallback 포함
    │   ├── schemas/
    │   ├── services/
    │   │   ├── ai/spending_service.py
    │   │   └── fds/
    │   │       ├── detector.py      # 하이브리드 FDS 엔진
    │   │       └── fds_model.pkl    # XGBoost ML (AUC 1.0)
    │   └── main.py                  # 전역 예외 핸들러 포함
    ├── proto/grpc/work_service.proto
    ├── scripts/init.sql             # 학사 일정 마스터 데이터 포함
    ├── docker-compose.yml
    ├── Dockerfile
    └── requirements.txt

---

## 13. 구현 완료 범위

완료:

- FastAPI 프로젝트 구조 및 GitHub 연결
- MySQL DB + SQLAlchemy 2.0 비동기 ORM
- TSID 기반 PK 채번 (tsidpy)
- AI 소비 패턴 분석 API 4개
- 하이브리드 적응형 FDS API 7개 (Rule + Z-score + XGBoost ML)
- 이상거래 알림 시스템 (fds_alert_log + polling)
- 소비 안전도 점수 (A/B/C/D 등급)
- 학교 선택 → 학사 일정 자동 연동 (한신대 2026년 실제 일정)
- gRPC 서버 실제 구현 (GetDailyBudget, CheckBlacklist)
- Kafka Producer 3개 토픽 발행
- Kafka Consumer 5개 토픽 구독 (Banking FDS 자동 분석 포함)
- Redis Cache-Aside + 장애 대응 fallback
- 전역 예외 핸들러 7개
- 헬스체크 (DB·Redis·ML 모델 상태 포함)
- FDS Rule 1 국제 표준 적용 (JPMorgan Chase 3-sigma rule)

추후 확장:

- 실제 유저 데이터 기반 ML 모델 재학습
- Banking 토픽명 확정 후 연동 테스트
- gRPC 실 연동 테스트 (Asset 도메인 연동 후)
- FDS MEDIUM 2단계 인증 연동

---

## 14. 브랜치 전략

    git checkout dev
    git pull origin dev
    git checkout -b feat/기능이름

    git add .
    git commit -m "feat: 기능 설명"
    git push origin feat/기능이름

GitHub에서 `feat/*` → `dev` 방향으로 Pull Request를 생성합니다.

| 태그 | 설명 |
| --- | --- |
| `feat` | 새 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 코드 리팩토링 |
| `docs` | 문서 수정 |
| `chore` | 설정 · 빌드 변경 |
