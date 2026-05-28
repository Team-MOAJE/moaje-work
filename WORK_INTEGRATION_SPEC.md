# Work 도메인 — Kafka · gRPC 연동 스펙

> 작성자: 김명성 | 2026-05-28

---

## Kafka

### 구독 토픽

| 토픽 | 발행 도메인 | 처리 내용 |
|---|---|---|
| `transaction_created_events` | Banking | FDS 이상거래 자동 분석 |
| `asset.balance.deducted` | Asset | Redis 캐시 무효화 |
| `asset.daily.budget.updated` | Asset | Redis 캐시 무효화 |
| `auth.user.registered` | Auth | 소비 프로필 자동 생성 |
| `work.fds.alert` | Work (자기 구독) | 이상거래 알림 자동 생성 |

### 발행 토픽

| 토픽 | 구독 도메인 | 발행 시점 |
|---|---|---|
| `work.spending.analyzed` | Asset | Daily Limit 계산 완료 |
| `work.schedule.updated` | Asset | 학사 일정 등록/수정 |
| `work.fds.alert` | 알림 서버 | HIGH 이상거래 탐지 |

---

## Kafka Payload 명세

### transaction_created_events (수신)

```json
{
  "transaction_id"  : "MOAJE-BNK-20260528-01HX...",
  "user_id"         : "01HX...",
  "amount"          : 200000,
  "merchant"        : "쿠팡",
  "transaction_type": "TRANSFER",
  "created_at"      : "2026-05-28T10:10:00",
  "hour"            : 10,
  "ip_address"      : "192.168.0.1",
  "device_info"     : "iPhone 15"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| transaction_id | string | ✅ | 거래 고유 ID |
| user_id | string | ✅ | 사용자 ID |
| amount | number | ✅ | 거래 금액 |
| merchant | string | ✅ | 가맹점명 |
| transaction_type | string | ✅ | TRANSFER / WITHDRAW / PAYMENT |
| created_at | string | ✅ | 거래 발생 시각 |
| hour | number | ✅ | 거래 시간대 (0~23) |
| ip_address | string | ⬜ | IP 주소 |
| device_info | string | ⬜ | 디바이스 정보 |

### work.fds.alert (발행)

```json
{
  "transaction_id": "MOAJE-BNK-20260528-01HX...",
  "user_id"       : "01HX...",
  "risk_level"    : "HIGH",
  "risk_score"    : 0.85,
  "reason_code"   : "RULE_ABNORMAL_AMOUNT(z=4.2σ) | ML_HIGH_RISK(prob=0.95)",
  "amount"        : 200000,
  "merchant"      : "쿠팡",
  "message"       : "🚨 평소보다 큰 금액의 결제가 감지됐어요! 200,000원 (쿠팡) — 본인 거래가 맞나요?",
  "timestamp"     : 1748390400000
}
```

---

## gRPC

### GetDailyBudgetContext

Asset 도메인이 가용 생활비 산출에 필요한 데이터를 Work 도메인에 요청합니다.

```protobuf
rpc GetDailyBudgetContext(GetDailyBudgetContextRequest)
    returns (GetDailyBudgetContextResponse);

message GetDailyBudgetContextRequest {
  string transaction_id = 1;
  string user_id        = 2;
  int64  timestamp      = 3;
}

message GetDailyBudgetContextResponse {
  string transaction_id  = 1;
  string user_id         = 2;
  int64  fixed_expenses  = 3;  // 고정 지출 (원)
  int64  event_buffer    = 4;  // 학사 이벤트 버퍼 (원)
  int64  expected_income = 5;  // 예상 수입 (원)
  int64  timestamp       = 6;
}
```

| 필드 | 설명 |
|---|---|
| fixed_expenses | 사용자 월 고정 지출 합산 |
| event_buffer | 7일 이내 학사 이벤트 예상 추가 지출 합산 |
| expected_income | 예정된 수입 (알바비·장학금) |
