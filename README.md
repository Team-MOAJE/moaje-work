# moaje-work 기술 가이드 및 컨벤션

`moaje-work` 는 모아제(MoAje) 플랫폼의 AI 소비 패턴 분석 도메인입니다.
대학생의 학사 일정과 소비 데이터를 결합하여 맞춤형 일일 가용 생활비를 산출하고, 하이브리드 적응형 FDS 이상거래 탐지 서비스를 제공합니다.

### 문서 안내

| 문서 | 내용 |
| --- | --- |
| `README.md` (이 문서) | 기술 가이드 · 구현 범위 · 컨벤션 |
| [`WORK_연동명세.md`](./WORK_연동명세.md) | **타 도메인 연동 규격 (최신)** — 개발환경 통합, Kafka · gRPC 계약, 결정 대기 항목 |
| `WORK_INTEGRATION_SPEC.md` | 구버전 (2026-05-28). 토픽명이 현재와 다르므로 참고용으로만 보관 |

| 인터페이스 | 경로 |
| --- | --- |
| OpenAPI (Swagger UI) | http://localhost:8084/docs |
| OpenAPI (JSON) | http://localhost:8084/openapi.json |
| Health | http://localhost:8084/health |

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
    REDIS_KEY_PREFIX=work:
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    KAFKA_CONSUMER_GROUP=moaje-work-group
    SECRET_KEY=dev-secret-key-change-in-production

비밀번호 · 개인키 · 실제 토큰은 커밋하지 않습니다. 위 값은 로컬 개발용 기본값입니다.

---

## 5. 서비스 간 통신 정책

| 구분 | 방식 | 예시 |
| --- | --- | --- |
| 외부 요청 | REST API | 학사 일정 등록, FDS 탐지 |
| 내부 서비스 간 요청/응답 | gRPC | GetDailyBudget (Asset → Work) |
| 비동기 이벤트 전달 | Kafka | 거래 발생, FDS 알림 발행 |

### Kafka 토픽

Consumer Group: `moaje-work-group`

| 토픽 | 역할 | 방향 | 규격 |
| --- | --- | --- | --- |
| `work.spending.analyzed` | Daily Limit 계산 완료 이벤트 | Work → Asset | JSON |
| `work.schedule.updated` | 학사 일정 등록/수정 이벤트 | Work → Asset | JSON |
| `work.fds.alert` | 이상거래 탐지 알림 | Work → 알림 서버 | JSON |
| `banking.transaction.created` | Banking 거래 완료 → FDS 자동 분석 | Banking → Work | JSON |
| `transaction_succeeded_events` | Asset 거래 완료 | Asset → Work | Protobuf |
| `asset.balance.deducted` | 거래 발생 이벤트 수신 | Asset → Work | JSON |
| `auth.user.registered` | 신규 유저 가입 이벤트 수신 | Auth → Work | JSON |

> **⚠️ 미확정 — 연동 전 반드시 확인 필요**
>
> Banking 저장소를 확인한 결과, 실제 발행 토픽은
> `moaje.banking.transfer-completed` (Protobuf)이며 위 표와 일치하지 않습니다.
> 또한 Banking 이벤트에는 `merchant` 필드가 없어 FDS 블랙리스트 대조가
> 불가능합니다. 두 사항 모두 팀 합의 후 반영 예정입니다.
>
> `auth.user.registered` 역시 Auth 측 발행 코드가 아직 구현 전입니다.
> (Work는 첫 API 호출 시 프로필을 생성하는 fallback이 있어 동작에는 지장 없음)

### 중복 소비 방지

Kafka는 at-least-once 전달이므로 `(user_id, transaction_id)` 기준으로 멱등 처리합니다.

- 애플리케이션: 처리 전 기존 로그 조회 후 존재하면 건너뜀
- DB: `fds_inference_log`에 `UNIQUE KEY (user_id, transaction_id)` 제약

### Redis 캐시 전략

Cache-Aside (Lazy Loading) 패턴 적용. Redis 장애 시 DB fallback, DB 장애 시 캐시 반환.

`moaje-redis`는 여러 도메인이 공유하는 인스턴스이므로 모든 Work 캐시 키에
`work:` 접두어를 사용합니다. 삭제 책임은 Work에 있으며, 자기 접두어 키만
개별 삭제합니다. (`FLUSHDB` / `FLUSHALL` 사용 금지)

| Key | TTL | 용도 | 무효화 시점 |
| --- | --- | --- | --- |
| `work:daily_limit:{user_id}` | 300s | Daily Limit 캐시 | 거래 발생 시 |
| `work:event_buffer:{user_id}` | 3600s | 학사 이벤트 버퍼 캐시 | 학사 일정 변경 시 |
| `work:spending_profile:{user_id}` | 1800s | AI 소비 프로필 캐시 | 거래 발생 시 |

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

최근 30일 FDS 탐지 이력 기반으로 산출합니다.

감점 기준은 리포트 카드(8절)의 안전도 항목과 동일하며, 만점 40점을 기준으로
계산한 뒤 단독 API에서는 100점 스케일로 환산해 노출합니다.

| 항목 | 감점 |
| --- | --- |
| HIGH 탐지 1건 | −8 |
| MEDIUM 탐지 1건 | −3 |

| 등급 | 점수 | 의미 |
| --- | --- | --- |
| A 🟢 | 90~100 | 매우 안전 |
| B 🟡 | 75~89 | 양호 |
| C 🟠 | 55~74 | 주의 |
| D 🔴 | 0~54 | 위험 |

---

## 8. 학기 소비 리포트 카드

학기 단위 소비 활동을 100점 만점으로 채점하고 등급·총평·뱃지를 제공합니다.

`GET /api/v1/work/spending/{uid}/report`

### 8.1 채점 항목

| 항목 | 배점 | 기준 |
| --- | --- | --- |
| 안전도 | 40 | HIGH −8 / MEDIUM −3 / 블랙리스트 이력 −10 |
| Daily Limit 준수율 | 30 | 90%↑=30 / 70~89%=22 / 50~69%=15 / 50%↓=7 |
| 이벤트 대비 지출 | 20 | 1.2배↓=20 / 1.5배↓=15 / 2.0배↓=10 / 2.0배↑=5 |
| 소비 규칙성 (CV) | 10 | 0.5↓=10 / 0.8↓=7 / 1.2↓=5 / 1.2↑=3 |

이벤트 대비 점수는 학기 전체 이벤트의 **평균** 배율을 기준으로 합니다.
최댓값을 쓰면 MT 한 번의 과소비로 시험기간에 절약한 결과가 묻히기 때문입니다.

### 8.2 등급

| 등급 | 점수 |
| --- | --- |
| A | 90~100 |
| B | 75~89 |
| C | 55~74 |
| D | 0~54 |
| N/A | 평가 불가 |

Daily Limit 기록이 없으면 100점 중 40점(준수율·규칙성)을 채점할 수 없습니다.
이때 낮은 점수를 그대로 등급화하면 "앱을 쓰지 않은 것"이 "소비 관리를 못한 것"으로
오해되므로 `N/A`로 표시합니다.

### 8.3 응답 구조

`score_breakdown` 필드로 항목별 점수를 함께 반환합니다.

```json
{
  "score_breakdown": {
    "safety_score"     : 40,
    "daily_limit_score": 30,
    "event_score"      : 20,
    "regularity_score" : 10,
    "total"            : 100
  },
  "overall_grade"  : "A",
  "overall_score"  : 100,
  "summary_message": "🏆 이번 학기 소비를 완벽하게 관리했어요!",
  "badges"         : ["🛡️ 완벽 안전 — 이상거래 0건"]
}
```

---

## 9. gRPC 인터페이스

    service WorkService {
      rpc GetDailyBudget(GetDailyBudgetRequest) returns (GetDailyBudgetResponse);
      rpc CheckBlacklist(CheckBlacklistRequest) returns (CheckBlacklistResponse);
    }

| RPC | 호출 도메인 | 설명 |
| --- | --- | --- |
| GetDailyBudget | Asset | 학사 이벤트 버퍼 포함 일일 가용 생활비 산출 |
| CheckBlacklist | Gateway | FDS 블랙리스트 등록 여부 확인 |

---

## 10. API 목록 (총 16개)

### 소비 패턴 분석

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/v1/work/spending/{uid}/profile` | AI 소비 프로필 조회 |
| GET | `/api/v1/work/spending/{uid}/event-buffer` | 7일 이내 이벤트 버퍼 조회 |
| POST | `/api/v1/work/spending/schedule` | 학사 일정 수동 등록 |
| GET | `/api/v1/work/spending/{uid}/schedules` | 학사 일정 전체 목록 |
| GET | `/api/v1/work/spending/{uid}/report` | 학기 소비 리포트 카드 (8절 참조) |

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

## 11. DB 테이블 구조 (8개)

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

## 12. 장애 대응 전략

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

## 13. 프로젝트 구조

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
    │   │   ├── ai/
    │   │   │   ├── spending_service.py
    │   │   │   └── report_service.py   # 학기 소비 리포트 카드 채점
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

## 14. 구현 완료 범위

완료:

- FastAPI 프로젝트 구조 및 GitHub 연결
- MySQL DB + SQLAlchemy 2.0 비동기 ORM
- TSID 기반 PK 채번 (tsidpy)
- AI 소비 패턴 분석 API 5개 (학기 리포트 카드 포함)
- 하이브리드 적응형 FDS API 8개 (Rule + Z-score + XGBoost ML)
- 학기 소비 리포트 카드 — 4항목 100점 채점 + 등급 · 총평 · 뱃지
- 이상거래 알림 시스템 (fds_alert_log + polling)
- 소비 안전도 점수 (A/B/C/D 등급)
- 소비 프로필 실거래 자동 갱신 (Welford 온라인 알고리즘)
- 학교 선택 → 학사 일정 자동 연동 (한신대 2026년 실제 일정)
- gRPC 서버 실제 구현 (GetDailyBudget, CheckBlacklist)
- Kafka Producer 3개 토픽 발행
- Kafka Consumer 5개 토픽 구독 (Banking FDS 자동 분석 포함)
- Kafka 중복 소비 방지 (멱등 처리 + DB 유니크 제약)
- Redis Cache-Aside + 장애 대응 fallback
- 전역 예외 핸들러 7개
- 헬스체크 (DB·Redis·ML 모델 상태 포함)
- FDS Rule 1 국제 표준 적용 (JPMorgan Chase 3-sigma rule)

연동 대기 (팀 합의 필요):

- Banking 토픽명 확정 — 현재 구독명과 Banking 실제 발행명 불일치
- `merchant` 필드 공급 방안 — 계약에 해당 필드 없음, FDS 블랙리스트 대조 불가
- Banking 이벤트 직렬화 확정 — Work는 JSON 전제, Banking 실제는 Protobuf
- `user_id` 타입 String 통일 — Work는 현재 int 처리
- `GetDailyBudget` 존치 여부 — Asset 생활비 계산과 책임 중복
- Auth `auth.user.registered` 발행 구현 대기
- Bearer 인증 선언 — JWT 규격 확정 후 적용

추후 확장:

- 실제 유저 데이터 기반 ML 모델 재학습
- gRPC 실 연동 테스트 (Asset 도메인 연동 후)
- FDS MEDIUM 2단계 인증 연동
- 서버 배포 (AWS EC2 / NCP)

---

## 15. 브랜치 전략

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
