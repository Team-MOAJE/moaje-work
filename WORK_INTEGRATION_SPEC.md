# Work 도메인 — Kafka · gRPC 연동 스펙

> 팀장님 요청 사항에 대한 Work 도메인의 연동 스펙 정의  
> 작성자: 김명성 | 날짜: 2026-05-28

---

## 1. Kafka 거래 이벤트 수신 스펙 (3번 요청)

### 우리가 구독할 토픽

```
토픽명: transaction_created_events (팀장님 확정 예정)
방향:   Banking → Work FDS
목적:   이상거래 분석
```

### 우리가 필요한 Payload 필드

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

### 필드 설명

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| transaction_id | string | ✅ | 거래 고유 ID |
| user_id | string | ✅ | 사용자 ID |
| amount | number | ✅ | 거래 금액 (FDS Rule 1 — Z-score 분석) |
| merchant | string | ✅ | 가맹점명 (알림 메시지 표시용) |
| transaction_type | string | ✅ | TRANSFER / WITHDRAW / PAYMENT |
| created_at | string | ✅ | 거래 발생 시각 |
| hour | number | ✅ | 거래 시간대 (FDS Rule 2 — 새벽 탐지) |
| ip_address | string | ⬜ | IP 주소 (추후 위치 기반 탐지용) |
| device_info | string | ⬜ | 디바이스 정보 (추후 디바이스 이상 탐지용) |

### FDS 분석 후 Work → 팀장님 전달값

HIGH 이상거래 탐지 시 Kafka로 발행합니다.

```
토픽명: work.fds.alert
방향:   Work → (알림 서버 or Auth 2단계 인증)
```

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

## 2. gRPC proto 추가 스펙 (4번 요청)

### 추가할 RPC

Asset 도메인이 Work 도메인에 일일 가용 생활비 산출에 필요한 데이터를 요청합니다.

```protobuf
// work_service.proto에 추가

// 가용 생활비 산출 데이터 요청
rpc GetDailyBudgetContext(GetDailyBudgetContextRequest)
    returns (GetDailyBudgetContextResponse);
```

### Request / Response Message

```protobuf
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

### 반환값 설명

| 필드 | 설명 | 산출 방식 |
|---|---|---|
| fixed_expenses | 고정 지출 | 사용자가 등록한 월 고정 지출 합산 |
| event_buffer | 학사 이벤트 버퍼 | 7일 이내 academic_schedule 예상 추가 지출 합산 |
| expected_income | 예상 수입 | 사용자가 등록한 예정 수입 (알바비·장학금) |

---

## 3. 처리 흐름 전체 정리

```
사용자 송금 요청
    ↓
Gateway (JWT 검증 · 라우팅)
    ↓
Banking 도메인 (실제 처리)
    ↓
transaction_created_events 발행
    ↓
┌───────────────────────────────┐
│  Work FDS 구독                │
│  → Z-score + ML 이상거래 분석  │
│  → HIGH 탐지 시               │
│     work.fds.alert 발행       │
└───────────────────────────────┘
    ↓
알림 서버 or Auth 2단계 인증 연동
(추후 논의)
```

```
Asset 도메인 (가용 생활비 산출 필요)
    ↓
gRPC GetDailyBudgetContext 호출
    ↓
Work 도메인
    ↓
fixed_expenses + event_buffer + expected_income 반환
    ↓
Asset 도메인에서 Daily Limit 최종 산출
```

---

## 4. 현재 Work 도메인 준비 상태

```
✅ work.fds.alert Kafka Producer 구현 완료
✅ FDS 하이브리드 탐지 엔진 구현 완료
   (Rule-based + Z-score + XGBoost ML)
✅ event_buffer 산출 로직 구현 완료
✅ fixed_expenses, expected_income API 구현 완료

⏳ transaction_created_events Consumer 추가 예정
   (토픽명 확정 후 바로 작업 가능)

⏳ GetDailyBudgetContext proto 추가 예정
   (팀장님 proto commit 후 작업)
```
