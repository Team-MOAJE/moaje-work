# Work 도메인 API 연동 가이드 (프론트엔드용)

작성: 김명성 (Work 담당)
기준 커밋: `746e4d0` (dev)
갱신: 2026-10-11

Work 도메인이 제공하는 REST API 29개의 전체 목록과 호출 규칙입니다.
이 문서가 정본이며, 저장소와 함께 갱신됩니다.

실행 중인 서버의 Swagger 로도 같은 내용을 볼 수 있습니다.

    http://localhost:8084/docs          (직접)
    http://localhost:19080/docs/work/openapi.json   (Gateway 경유)

---

## 1. 호출 규칙 — 먼저 읽어주세요

### 1.1 Base URL

모든 경로는 `/api/v1/work` 로 시작합니다. Gateway 를 통해 호출합니다.

    {GATEWAY}/api/v1/work/...

### 1.2 인증

**프론트엔드는 인증 헤더를 직접 넣지 않습니다.** JWT 를 Gateway 에 보내면,
Gateway 가 검증한 뒤 `X-Authenticated-User-Id` 헤더를 Work 로 넘깁니다.
Work 는 그 헤더로 사용자를 식별합니다.

| 상황 | 응답 |
|---|---|
| 헤더가 없거나 형식이 틀림 | `401` |
| 경로의 `{user_id}` 가 로그인한 사용자와 다름 | `403` |
| 운영 전용 API 를 사용자 자격으로 호출 | `403` |

**경로에 `{user_id}` 가 있는 API 는 전부 본인 것만 조회·수정할 수 있습니다.**
남의 `user_id` 를 넣으면 403 이며, 이 검사는 모든 엔드포인트에 일괄 적용됩니다.

### 1.3 운영 전용 API

아래 3개는 **프론트엔드에서 호출하지 않습니다.** 내부 토큰(`X-Internal-Token`)
으로만 통과하며, 사용자 헤더로는 403 입니다.

- `POST /fds/blacklist`
- `DELETE /fds/{user_id}/blacklist`
- `GET /metrics/retention`

### 1.4 금액과 날짜

| 항목 | 형식 | 비고 |
|---|---|---|
| 금액 | 문자열 또는 숫자 (원 단위) | 소수점이 있을 수 있으니 `Decimal`/문자열로 받으세요 |
| 날짜 | `YYYY-MM-DD` | |
| 일시 | ISO 8601 | 저장은 UTC, **날짜 경계 계산은 한국 시간** |
| 연월 | `YYYY-MM` | Recap 전용 |

### 1.5 공통 오류 응답

```json
{
  "code": "...",
  "message": "...",
  "timestamp": "2026-10-11T10:00:00+00:00",
  "service": "moaje-work-service"
}
```

| 코드 | 언제 |
|---|---|
| `400` | 값이 규칙에 안 맞음 (예: `year_month` 형식 오류) |
| `401` | 인증 헤더 없음·형식 오류 |
| `403` | 남의 데이터 접근 / 운영 API 를 사용자 자격으로 |
| `404` | 대상 없음 |
| `422` | 쿼리 파라미터 범위 초과 (예: `limit` 상한) |
| `503` | DB·Redis 동시 장애, 운영 토큰 미설정 |

---

## 2. API 목록 (29개)

### 2.1 소비 분석 — `/spending`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/spending/{user_id}/profile` | AI 소비 프로필 |
| GET | `/spending/{user_id}/event-buffer` | 7일 이내 학사 이벤트 예비비 |
| POST | `/spending/schedule` | 학사 일정 수동 등록 |
| GET | `/spending/{user_id}/schedules` | 학사 일정 목록 |
| GET | `/spending/{user_id}/report` | 학기 소비 리포트 카드 |

---

**GET `/spending/{user_id}/profile`**

```json
{
  "user_id": 1174162530076472050,
  "avg_daily_amount": "17500.0000",
  "peak_spend_hour": 11,
  "top_category": "식비",
  "risk_score_baseline": "0.10",
  "last_analyzed_at": "2026-10-11T10:04:45"
}
```

> 거래가 없는 신규 사용자도 기본 프로필이 자동으로 생성되어 200 이 옵니다.

---

**GET `/spending/{user_id}/event-buffer`**

```json
{ "user_id": 1174162530076472050, "event_buffer": "150000.0000" }
```

> 오늘부터 7일 이내 학사 일정의 예상 추가 지출 합계입니다.
> 일정을 등록·동기화하면 **즉시** 반영됩니다(캐시 무효화 처리됨).

---

**POST `/spending/schedule`** → `201`

요청:

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `user_id` | int | ✓ | 로그인 사용자와 같아야 함 |
| `event_type` | enum | ✓ | `EXAM` `MT` `FESTIVAL` `VACATION` `EMPLOYMENT` `SEMESTER_START` `SEMESTER_END` `MIDTERM` `FINAL` `HOLIDAY` |
| `event_name` | string | ✓ | |
| `start_date` | date | ✓ | `YYYY-MM-DD` |
| `end_date` | date | ✓ | |
| `expected_extra_spend` | decimal | | 기본 0 |

응답: 등록된 일정 1건 (`id`, `user_id`, `event_type`, `event_name`, `start_date`, `end_date`, `expected_extra_spend`)

---

**GET `/spending/{user_id}/schedules`**

| 쿼리 | 기본 | 범위 |
|---|---|---|
| `year` | 없음 | 2020~2030. 그 해에 걸치는 일정만 |
| `limit` | 200 | 1~500 |
| `offset` | 0 | 0 이상 |

> 학기마다 쌓이므로 **학기 화면에서는 `year` 를 넣는 쪽을 권장**합니다.
> 범위를 벗어난 `limit` 은 422 입니다.

---

**GET `/spending/{user_id}/report?year=2026&semester=2`**

| 쿼리 | 기본 | 범위 |
|---|---|---|
| `year` | 2026 | 2020~2030 |
| `semester` | 1 | 1 또는 2 |

응답 주요 구조:

```json
{
  "user_id": 0, "year": 2026, "semester": 2,
  "period_label": "2026년 2학기",
  "period_start": "2026-09-01", "period_end": "2026-12-20",
  "spending": { "total_tx_count": 0, "avg_daily_limit": "0",
                "peak_spend_date": "2026-10-01", "peak_spend_amount": "0",
                "lowest_spend_date": "...", "lowest_spend_amount": "0",
                "peak_spend_hour": 11, "top_category": "식비",
                "total_log_days": 41,
                "daily_limit_compliance_rate": "0.9000",
                "daily_limit_cv": "0.312" },
  "fds": { "total_detected": 0, "high_count": 0, "medium_count": 0,
           "safety_score": 40, "safety_grade": "A", "top_reason": "없음" },
  "events": [ { "event_type": "MT", "event_name": "...",
                "start_date": "...", "end_date": "...", "period_days": 2,
                "avg_daily_spend": "0", "total_spend": "0",
                "vs_normal_ratio": "1.20" } ],
  "score_breakdown": { "safety_score": 40, "daily_limit_score": 30,
                       "event_score": 20, "regularity_score": 10, "total": 100 },
  "overall_grade": "A", "overall_score": 100,
  "summary_message": "...", "badges": ["..."]
}
```

> **`overall_grade` 가 `"N/A"` 로 올 수 있습니다.** Daily Limit 기록이 없어
> 100점 중 40점을 채점할 수 없는 경우이며, 낮은 점수가 아니라 **평가 불가**입니다.
> 화면에서 D등급처럼 보이지 않게 따로 처리해주세요.
>
> `daily_limit_cv` 는 `null` 일 수 있습니다(기록이 1일 이하).

---

### 2.2 학사 일정 자동 연동 — `/calendar`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/calendar/universities` | 지원 학교 목록 |
| POST | `/calendar/sync` | 학교 선택 → 학사 일정 자동 등록 |
| GET | `/calendar/{user_id}/schedules` | 전체 일정 (자동 + 수동) |

**GET `/calendar/universities`**

```json
[ { "id": 1, "name": "한신대학교", "short_name": "한신대", "region": "경기도 오산시" } ]
```

**POST `/calendar/sync`**

요청: `{ "user_id": 0, "university_id": 1, "year": 2026, "semester": 2 }`

응답: `{ "user_id": 0, "university_name": "한신대학교", "year": 2026, "semester": 2, "synced_count": 6, "schedules": [ ... ] }`

> 해당 학교·학기의 마스터 일정이 없으면 `404` 입니다.
> 재동기화하면 **그 학기 자동 일정만** 교체되고, 다른 학기와 사용자가 직접
> 등록한 일정은 그대로 남습니다.

**GET `/calendar/{user_id}/schedules`** — 쿼리는 `/spending/.../schedules` 와 동일
(`year`, `limit`, `offset`). 응답에 `is_auto` 가 추가로 들어갑니다.

---

### 2.3 이상거래 탐지 — `/fds`

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/fds/detect` | 이상거래 탐지 |
| GET | `/fds/{user_id}/logs` | 탐지 로그 |
| GET | `/fds/{user_id}/alerts` | 알림 목록 |
| PATCH | `/fds/{user_id}/alerts/{alert_id}/confirm` | 알림 확인 처리 |
| GET | `/fds/{user_id}/safety-score` | 소비 안전도 |
| GET | `/fds/{user_id}/blacklist` | 차단 여부 조회 |

**POST `/fds/detect`**

요청:

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `user_id` | int | ✓ | |
| `transaction_id` | string | ✓ | 거래 고유 ID |
| `amount` | decimal | ✓ | |
| `merchant` | string | | |
| `hour` | int | ✓ | 0~23 (한국 시간) |
| `occurred_at` | datetime | | 거래 실제 발생 시각 |

응답: `{ "user_id", "transaction_id", "risk_score", "risk_level", "reason_code", "is_alerted", "message" }`

- `risk_level` : `LOW` (0~0.4) / `MEDIUM` (0.4~0.7) / `HIGH` (0.7~1.0)
- `is_alerted` 가 `true` 면 알림이 자동 생성됩니다.

> **같은 `transaction_id` 를 다시 보내도 안전합니다.** 처음 판정이 그대로
> 돌아오며, 동시에 여러 번 보내도 같은 값이 옵니다. 타임아웃 재시도에
> 그대로 쓰셔도 됩니다.

**GET `/fds/{user_id}/logs?limit=20`** — `limit` 1~100 (기본 20)

**GET `/fds/{user_id}/alerts?limit=20&unread=false`**

```json
{
  "user_id": 0,
  "total": 150,      // 전체 알림 수
  "unread": 100,     // 미확인 수
  "returned": 20,    // 이번에 내려준 수
  "alerts": [ { "id": 0, "transaction_id": "...", "amount": "...",
                "merchant": "...", "risk_level": "HIGH", "reason_code": "...",
                "message": "...", "is_confirmed": false,
                "confirmed_at": null, "created_at": "..." } ]
}
```

> **`total`·`unread` 는 `limit` 과 무관한 전체 기준입니다.** 배지 숫자는 이 값을
> 쓰시면 되고, `alerts` 배열 길이는 `returned` 입니다.

**PATCH `/fds/{user_id}/alerts/{alert_id}/confirm`**

응답: `{ "alert_id", "is_confirmed": true, "confirmed_at", "message" }`
남의 `alert_id` 는 `404` 입니다.

**GET `/fds/{user_id}/safety-score`**

```json
{ "user_id": 0, "safety_score": 85, "grade": "B", "emoji": "🟡",
  "total_tx": 12, "high_count": 0, "medium_count": 2,
  "period_days": 30, "message": "🟡 소비 안전도 85점 (B등급)" }
```

**GET `/fds/{user_id}/blacklist`**

`{ "user_id": 0, "is_blocked": false, "message": "✅ 정상 사용자입니다." }`

---

### 2.4 재무 온보딩 — `/onboarding`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/onboarding/questions` | 10문항과 선택지 |
| POST | `/onboarding/{user_id}/answers` | 여러 문항 일괄 답변 |
| POST | `/onboarding/{user_id}/answers/{question_no}` | 한 문항씩 답변 |
| GET | `/onboarding/{user_id}/answers` | 내 답변 목록 |
| GET | `/onboarding/{user_id}/progress` | 진행 상태 |
| GET | `/onboarding/{user_id}/profile` | Future Profile (미래 카드) |

**GET `/onboarding/questions`**

```json
[ { "question_no": 1, "phase": "...", "text": "...", "purpose": "...",
    "free_text": false,
    "options": [ { "code": "MONTHLY_RENT", "label": "월세" } ] } ]
```

**POST `/onboarding/{user_id}/answers`** (일괄)

`{ "user_id": 0, "answers": [ { "question_no": 1, "answer_code": "MONTHLY_RENT" } ] }`

**POST `/onboarding/{user_id}/answers/{question_no}`** (단건)

`{ "question_no": 1, "answer_code": "MONTHLY_RENT", "answer_text": null }`

> 선택지를 모르면 `answer_code` 에 **`UNKNOWN`** 을 보내주세요. 빈 값 대신
> 명시적으로 보내면 계산에서 기본 가정이 적용되고, 어느 문항이 그랬는지
> 진행 상태의 `unknown_answers` 로 알려줍니다.
>
> 같은 문항에 다시 답하면 덮어씁니다(재답변 허용).

**진행 상태 응답 (두 POST 와 `/progress` 공통)**

```json
{ "user_id": 0, "answered_count": 7, "total_questions": 10,
  "is_completed": false, "remaining": [8, 9, 10],
  "unknown_answers": [3], "next_question_no": 8 }
```

**GET `/onboarding/{user_id}/profile`** — 10문항을 모두 채우기 전에는 `404` 입니다.

---

### 2.5 Future Simulator — `/simulator`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/simulator/baselines` | 계산 기준값과 출처 |
| POST | `/simulator/{user_id}/simulate` | 미래 자금 역산 |
| POST | `/simulator/{user_id}/adjust` | 조건 변경 시뮬레이션 |

**POST `/simulator/{user_id}/simulate`**

요청 (전부 선택 — 비우면 기준값·Asset 조회로 채움):
`monthly_income` `monthly_housing` `monthly_living` `monthly_leisure`
`deposit` `move_in_cost` `current_asset`

응답:

```json
{
  "user_id": 0,
  "target":   { "deposit": "...", "move_in_cost": "...",
                "emergency_fund": "...", "total": "..." },
  "cashflow": { "income": "...", "housing": "...", "living": "...",
                "leisure": "...", "expense": "...", "savable": "..." },
  "current_asset": "5000000", "gap": "...",
  "months_to_goal": 10, "progress_rate": "0.35",
  "asset_source": "ASSET_SERVICE",
  "assumed_keys": ["monthly_leisure"],
  "baselines": [ { "key": "...", "label": "...", "amount": "...",
                   "kind": "STATISTIC", "source": "...", "as_of": "...",
                   "scope": "...", "url": "..." } ],
  "advice": ["..."]
}
```

> **`months_to_goal` 이 `null` 일 수 있습니다.** 월 저축 가능액이 0 이하라
> 달성 시점을 계산할 수 없는 경우입니다. 큰 숫자로 바꾸지 말고 "현재 조건으로는
> 달성 시점을 계산할 수 없음"으로 보여주세요.
>
> **`assumed_keys`** 에 든 항목은 사용자가 입력하지 않아 가정값으로 계산한
> 것입니다. 화면에 '가정값' 표시를 해주시면 좋습니다.
>
> **`asset_source`** 는 아래 2.8 참고.

**POST `/simulator/{user_id}/adjust`** — `simulate` 요청 + `monthly_delta` (필수, 음수면 절약)

```json
{ "user_id": 0, "monthly_delta": "-200000",
  "base_monthly_savable": "...", "adjusted_monthly_savable": "...",
  "base_months_to_goal": 10, "adjusted_months_to_goal": 9,
  "months_saved": 1, "message": "..." }
```

---

### 2.6 사회인 준비도 — `/readiness`

**POST `/readiness/{user_id}`** — 요청은 `simulate` 와 동일

```json
{ "user_id": 0, "percent": 62, "level": 3, "level_label": "...",
  "items": [ { "key": "...", "label": "...", "weight": 50,
               "score": 30, "detail": "..." } ],
  "evaluated": ["..."], "excluded": ["..."],
  "asset_source": "INPUT", "message": "...", "disclaimer": "..." }
```

> **`items[].score` 가 `null` 이면 데이터가 없어 평가에서 제외된 항목**입니다
> (0점이 아닙니다). `excluded` 에도 같은 항목이 들어갑니다.
> `disclaimer` 는 반드시 화면에 함께 노출해주세요 — 개인 평가가 아니라
> 앱 내부 지표임을 알리는 문구입니다.

---

### 2.7 Money Recap — `/recap`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/recap/{user_id}/months` | Recap 이 있는 월 목록 |
| GET | `/recap/{user_id}/{year_month}` | 월별 Recap |

**GET `/recap/{user_id}/months?limit=12`** — `limit` 1~36

`{ "user_id": 0, "months": ["2026-10", "2026-09"] }`

**GET `/recap/{user_id}/2026-09`**

```json
{
  "user_id": 0, "year_month": "2026-09", "revision": 2,
  "total_income": "...", "total_expense": "...",
  "total_transfer": "...", "net_saving": "...",
  "categories": [ { "code": "FOOD", "label": "식비",
                    "amount": "...", "share": "0.42" } ],
  "top_category": { ... },
  "nickname": "미식 탐험가", "nickname_reason": "...",
  "coverage": { "total_tx": 24, "classified_tx": 22,
                "unclassified_share": "0.08",
                "is_reliable": true, "note": "..." },
  "insights": ["..."]
}
```

> **`nickname` 이 `null` 일 수 있습니다.** 거래가 10건 미만이거나 미분류 비중이
> 30% 를 넘으면 성향을 단정하지 않습니다. 이때 `coverage.is_reliable` 이
> `false` 이고 `coverage.note` 에 이유가 들어갑니다. 별명 자리에 그 문구를
> 보여주시면 됩니다.
>
> 형식이 잘못된 `year_month` 는 `400`, 해당 월 집계가 없으면 `404` 입니다.

---

### 2.8 `asset_source` — 시뮬레이터·준비도 공통

현재 자산을 어디서 가져왔는지 알려줍니다. **값마다 사용자에게 할 말이 다릅니다.**

| 값 | 뜻 | 화면 안내 |
|---|---|---|
| `INPUT` | 사용자가 직접 입력 | 그대로 표시 |
| `ASSET_SERVICE` | Asset 조회 성공 (**0원도 포함**) | 그대로 표시 |
| `NOT_LINKED` | 계좌 미연동 · 활성 계좌 없음 | "계좌를 연결하면 자동으로 가져와요" — **계좌 연결 유도** |
| `UNAVAILABLE` | 조회 실패 (일시 장애 등) | "지금은 가져올 수 없어요" — 사용자가 할 수 있는 일 없음 |

> `NOT_LINKED` 와 `UNAVAILABLE` 을 같이 처리하지 말아주세요. 앞은 사용자가
> 계좌를 연결하면 해결되고, 뒤는 기다리는 것 말고 할 수 있는 일이 없습니다.
> 두 경우 모두 `current_asset` 은 `0` 으로 계산이 진행됩니다.

---

## 3. 화면 붙일 때 주의할 값 모음

| API | 값 | 주의 |
|---|---|---|
| 학기 리포트 | `overall_grade` | `"N/A"` 가 올 수 있음 (평가 불가, D등급 아님) |
| 학기 리포트 | `daily_limit_cv` | `null` 가능 |
| 시뮬레이터 | `months_to_goal` | `null` 가능 (달성 불가) |
| 시뮬레이터 | `assumed_keys` | 비어 있지 않으면 '가정값' 표시 |
| 준비도 | `items[].score` | `null` = 평가 제외 (0점 아님) |
| 준비도 | `disclaimer` | 반드시 노출 |
| Recap | `nickname` | `null` 가능 — `coverage.note` 로 대체 |
| 알림 | `total` / `returned` | 배지는 `total`, 목록 길이는 `returned` |
| 공통 | `asset_source` | 4가지 값마다 안내 문구가 다름 |

---

## 4. 아직 합의가 남은 것

| 항목 | 상태 |
|---|---|
| 카테고리 코드 체계 | Recap 의 `categories[].code` 는 Asset 집계를 그대로 전달합니다. 코드 목록 확정 전까지 `label` 을 쓰시는 편이 안전합니다. |
| `user_id` 타입 | 계약상 문자열, Work 내부는 정수입니다. 경계에서 변환하고 있으며 통일은 팀 공통 과제입니다. |
| Daily Limit 계산 주체 | Asset 과 책임 분리를 협의 중입니다. 확정되면 이 문서를 갱신합니다. |

---

## 5. 문의

Work 도메인 관련 문의는 김명성에게 주세요.
이 문서와 실제 동작이 다르면 **실제 동작이 맞고 문서가 틀린 것**이니 알려주시면
바로 고치겠습니다.
