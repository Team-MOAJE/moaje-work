# Work 도메인 연동 명세

작성: 김명성 (Work 담당)
작성일: 2026-09-15
대상 회의: 9/15 21:00

---

## 1. 개발환경 통합

### 저장소

| 항목 | 값 |
|---|---|
| 저장소 | `https://github.com/Kim-myeongseong/moaje-work` |
| 브랜치 | `dev` |
| 커밋 ID | `b3acf81` |

서버는 타 도메인 없이 단독 기동 가능합니다. Kafka·Redis 연결 실패 시에도
fail-open으로 처리되어 REST API는 정상 응답합니다.

### 실행 환경

| 항목 | 값 |
|---|---|
| Python | 3.12 |
| 프레임워크 | FastAPI 0.115.0 |
| 의존성 | `requirements.txt` |
| Dockerfile | 저장소 루트 |
| 실행 명령 | `uvicorn app.main:app --host 0.0.0.0 --port 8084` |
| HTTP 포트 | `8084` |
| gRPC 포트 | `50051` |
| Health 경로 | `GET /health` |
| OpenAPI | `GET /openapi.json` (Swagger UI: `/docs`) |

> Gateway에 설정된 Work `8084`는 현재 구현과 일치합니다. 이대로 확정 가능합니다.

### 환경변수

```bash
APP_ENV=development
DATABASE_URL=mysql+aiomysql://root:root_password@moaje-work-db:3306/work_db
REDIS_URL=redis://moaje-redis:6379/0
REDIS_KEY_PREFIX=work:
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
KAFKA_CONSUMER_GROUP=moaje-work-group
```

비밀번호·토큰은 커밋하지 않으며, 위 값은 로컬 개발용 기본값입니다.

### DB

- 전용 인스턴스 `moaje-work-db` (외부 포트 `3309`, banking-mysql `3308`과 분리)
- 스키마·초기 데이터: `scripts/init.sql`
- 초기 데이터에 한신대 포함 국내 대학 10개교의 학사일정 마스터가 포함됩니다.
- 테이블 8개, 전체 TSID PK

### Redis

| 항목 | 내용 |
|---|---|
| 용도 | Daily Limit 캐시, 학사 이벤트 버퍼 캐시, 소비 프로필 캐시 |
| 키 접두어 | `work:` (전부 이 접두어를 사용) |
| 키 형식 | `work:daily_limit:{user_id}`, `work:event_buffer:{user_id}`, `work:spending_profile:{user_id}` |
| TTL | daily_limit 300초 / event_buffer 3600초 / spending_profile 1800초 |
| 삭제 책임 | Work 도메인. 자기 접두어 키만 개별 삭제합니다. |

> **공용 Redis 관련 확인 요청**
> 기존 코드는 접두어 없이 `daily_limit:{user_id}` 형태를 사용하고 있었습니다.
> Asset도 생활비를 계산하므로 같은 키를 쓸 경우 서로의 캐시를 덮어쓸 위험이 있어
> 이번에 `work:` 접두어를 추가했습니다. **다른 도메인도 접두어 사용 여부를 확인 부탁드립니다.**
> `FLUSHDB`/`FLUSHALL` 등 전체 삭제 명령은 사용하지 않습니다.

### Kafka

**Consumer Group**: `moaje-work-group` (Work 전용)

같은 이벤트를 여러 도메인이 각각 수신해야 하므로 도메인별로 그룹을 분리했습니다.
Work는 위 단일 그룹으로 아래 토픽을 구독합니다.

| 토픽 | 방향 | 규격 | 용도 |
|---|---|---|---|
| `banking.transaction.created` | Banking → Work | JSON | 거래 수신 → FDS 자동 분석 |
| `transaction_succeeded_events` | Asset → Work | Protobuf | 거래 완료 이벤트 |
| `asset.balance.deducted` | Asset → Work | JSON | Redis 캐시 무효화 |
| `auth.user.registered` | Auth → Work | JSON | 소비 프로필 자동 생성 |
| `work.fds.alert` | Work → Work | JSON | 이상거래 알림 생성 (자기 구독) |

**Producer**

| 토픽 | 규격 | 발행 시점 |
|---|---|---|
| `work.spending.analyzed` | JSON | 소비 분석 완료 시 |
| `work.schedule.updated` | JSON | 학사 일정 등록·변경 시 |
| `work.fds.alert` | JSON | 이상거래 탐지 시 |

**중복 처리 정책**

Kafka는 at-least-once이므로 `(user_id, transaction_id)` 기준으로 멱등 처리합니다.

- 애플리케이션: 처리 전 기존 로그 조회 후 존재하면 건너뜀
- DB: `fds_inference_log`에 `UNIQUE KEY (user_id, transaction_id)` 제약

**실패 재처리**

현재는 핸들러 내 예외를 로깅 후 컨슈머를 계속 진행시킵니다(메시지 유실 허용).
DLQ 도입 여부는 팀 정책에 맞추겠습니다.

---

## 2. Work 연동 계약 — 확인·합의 필요 항목

### 2-1. Daily Limit 책임 분리 (가장 중요)

회의안건 지적대로 Asset의 생활비 계산과 Work의 `GetDailyBudget`이 겹칩니다.

**Work 측 제안: 계산은 Asset, 분석·조언은 Work**

현재 Work의 Daily Limit 공식은 다음과 같습니다.

```
Daily_Limit = (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼)
              ÷ 월급날까지 남은 일수
```

이 중 Work가 고유하게 보유한 값은 **이벤트 버퍼**뿐입니다.
(7일 이내 학사일정의 `expected_extra_spend` 합산 — 학사일정은 Work만 관리)

따라서 다음 분담을 제안합니다.

| 구분 | 담당 | 비고 |
|---|---|---|
| 잔고·고정지출 기반 생활비 계산 | Asset | 이미 구현되어 있음 |
| 학사 이벤트 버퍼 산출 | Work | Work 고유 데이터 |
| 소비 패턴 분석·조언 문구 | Work | AI 분석 |
| FDS 이상거래 탐지 | Work | Work 전담 |

이 경우 Work의 `GetDailyBudget`은 **이벤트 버퍼 제공 RPC로 축소**하거나,
Asset이 Work의 `GET /spending/{uid}/event-buffer`를 호출하는 방식이 됩니다.
어느 쪽이 좋을지 Asset 담당자와 합의 필요합니다.

**결정 필요**: 유지 / 이벤트 버퍼 전용으로 축소 / 제거

### 2-2. 예상 수입·다음 수입일·고정 지출 관리 주체

현재 Work는 이 값들을 **호출자가 전달한 값으로만** 사용하며 자체 보관하지 않습니다.
Asset도 동일하다면 실제 저장 주체가 없는 상태입니다.

**결정 필요**: 어느 도메인이 보관할지, 데이터가 없을 때의 기본 동작

Work가 맡는다면 사용자 입력 API와 기본값 정책을 추가 구현하겠습니다.

### 2-3. `transaction_id` 의미 확인

회의안건 지적대로 Work가 받는 `transaction_id`를 Banking의 `transferId`와
동일하다고 가정하지 않겠습니다.

**확인 필요**
- `banking.transaction.created`의 `transaction_id`가 무엇을 가리키는지
- Asset `transaction_succeeded_events`의 식별자와 같은 값인지 다른 값인지

두 토픽의 식별자 체계가 다르면 중복 방지 기준을 토픽별로 분리해야 합니다.

### 2-4. 통계 날짜 기준

현재 `occurred_at`(ISO8601)에서 시각을 추출해 사용하며, **메시지 수신 시각으로
대체하지 않습니다.** `occurred_at`이 없으면 기본값 12시로 처리합니다.

**확인 필요**: `occurred_at`의 타임존 표기와 의미(발생/완료/복구 중 어느 시점인지)

### 2-5. 늦게 도착한 과거 거래

현재 소비 프로필은 Welford 온라인 알고리즘으로 도착 순서대로 갱신합니다.
과거 거래가 늦게 도착해도 순서 보정 없이 반영됩니다.

통계 정확도가 중요하다면 일 단위 배치 재계산을 추가할 수 있습니다.
**결정 필요**: 현행 유지 / 배치 재계산 추가

### 2-6. 분석 결과 제공 경로

**결정 필요**: Work API에서 앱에 직접 제공 / Asset에 전달해 함께 표시

현재는 Work REST API가 직접 제공하는 구조입니다
(`GET /api/v1/work/spending/{uid}/report` 등).

---

## 3. Work 제공 API (Swagger 준비 항목)

| 항목 | 내용 |
|---|---|
| 웹 프레임워크 | FastAPI 0.115.0 |
| OpenAPI 제공 | 제공함 |
| OpenAPI 경로 | `/openapi.json` |
| Swagger UI | `/docs` |
| ReDoc | `/redoc` |
| 엔드포인트 수 | 17개 (health 포함) |

### 호출 가능한 분석 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/work/spending/{uid}/profile` | AI 소비 프로필 |
| GET | `/api/v1/work/spending/{uid}/event-buffer` | 학사 이벤트 버퍼 |
| GET | `/api/v1/work/spending/{uid}/report` | 학기 소비 리포트 카드 |
| POST | `/api/v1/work/spending/schedule` | 학사 일정 등록 |
| GET | `/api/v1/work/spending/{uid}/schedules` | 학사 일정 목록 |
| GET | `/api/v1/work/calendar/universities` | 대학교 목록 |
| POST | `/api/v1/work/calendar/sync` | 학사 일정 동기화 |
| GET | `/api/v1/work/calendar/{uid}/schedules` | 캘린더 일정 |
| POST | `/api/v1/work/fds/detect` | 이상거래 탐지 |
| GET | `/api/v1/work/fds/{uid}/logs` | 탐지 로그 |
| GET | `/api/v1/work/fds/{uid}/alerts` | 알림 목록 |
| PATCH | `/api/v1/work/fds/{uid}/alerts/{id}/confirm` | 알림 확인 |
| GET | `/api/v1/work/fds/{uid}/safety-score` | 안전도 점수 |
| GET | `/api/v1/work/fds/{uid}/blacklist` | 블랙리스트 조회 |
| POST | `/api/v1/work/fds/blacklist` | 블랙리스트 등록 |
| DELETE | `/api/v1/work/fds/{uid}/blacklist` | 블랙리스트 해제 |

### 테스트 데이터

`scripts/init.sql`에 대학 10개교와 학사일정 마스터가 포함됩니다.
사용자별 소비 데이터는 `auth.user.registered` 수신 또는
`POST /fds/detect` 호출로 생성됩니다.

### Bearer 인증 선언

**미적용 — 작업 예정**

현재 Work의 OpenAPI에는 `securitySchemes`가 선언되어 있지 않습니다.
JWT 규격(안건 4번) 확정 후 FastAPI에 Bearer 인증을 선언하겠습니다.

**확인 필요**
- Gateway가 전달하는 내부 사용자 헤더의 정확한 이름
- Work가 헤더의 사용자 ID를 신뢰하고, 경로의 `{user_id}`와 불일치 시 거부하는 정책으로 가는지

---

## 4. 알려진 이슈 / 결정 대기

| 항목 | 상태 | 상대 |
|---|---|---|
| `user_id` 타입 String 통일 | 현재 Work는 int 처리 중. 전환 시점 합의 필요 | 팀장 |
| `auth.user.registered` 토픽명 최종 확인 | 미확정 | 이다경 |
| `auth.user.registered`에 `university_id` 포함 | 요청 — 현재 프로필 생성 시 null로 저장됨 | 이다경 |
| `banking.transaction.created`에 `category` 필드 추가 | 요청 — 현재 merchant 문자열만 수신 | 팀장 |
| `GetDailyBudget` 존치 여부 | 2-1 참조 | Asset 담당 |
| Bearer 인증 선언 | JWT 규격 확정 후 적용 | Auth·Gateway |

### `category` 필드 요청 사유

교수님 피드백(대학생 특화 기능 부족)에 대응해 "배달 vs 학식 비용 비교" 같은
분석을 검토 중입니다. 이를 위해선 가맹점 카테고리 분류가 필요한데,
Work가 merchant 문자열을 추측으로 분류하는 것보다 Banking이 분류해
전달하는 편이 정확합니다.

```json
// 현재
{"user_id": 1, "amount": 12000, "merchant": "배달의민족"}

// 요청
{"user_id": 1, "amount": 12000, "merchant": "배달의민족", "category": "DELIVERY"}
```

Work는 `category`가 없어도 동작하도록 구현되어 있습니다(기본값 처리).

---

## 5. 일정

- 10월 말 개발 완료 목표 인지했습니다.
- Work 도메인 기본 기능은 구현 완료 상태이며, 현재는 타 도메인 연동 대기입니다.
- AISW 페스티벌 발표용 추가 기능은 팀 합의 후 착수하겠습니다.
