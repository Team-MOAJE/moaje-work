"""
Asset 이벤트 주입 — 수신부 검증용

Asset 을 띄우지 않고 Work 의 Kafka 수신부만 확인한다.
Asset 의 AssetOutboxKafkaPublisher 가 만드는 것과 같은 방식으로
Protobuf 메시지를 직렬화해 같은 토픽·같은 키로 보낸다.

    docker compose exec work-service python scripts/inject_asset_events.py

확인 대상
  - 세 토픽이 Protobuf 로 파싱되는가
  - 계약의 user_id(string)가 경계에서 정수로 변환되는가
  - revision 규칙이 재전송·역순 도착을 걸러내는가
  - 같은 거래가 두 번 와도 중복 적재되지 않는가
"""
import sys
from pathlib import Path

# python 은 cwd 가 아니라 스크립트가 있는 폴더를 sys.path 에 넣는다.
# 여기서는 /app/scripts 가 되어 app 패키지를 찾지 못하므로 상위를 직접 올린다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from aiokafka import AIOKafkaProducer

from app.core.config import settings
from app.grpc.common import resources_pb2 as res
from app.grpc.events import asset_events_pb2 as ev

USER_ID    = "777"          # 계약상 string
YEAR_MONTH = "2026-09"


def money(amount: int) -> res.Money:
    return res.Money(amount=amount, currency="KRW")


def transaction(tx_id: int, amount: int, hour: int = 14) -> bytes:
    """Asset 의 toProtoEvent() 가 채우는 필드만 채운다."""
    return ev.TransactionSucceededEvent(
        event_id                = f"evt-{tx_id}",
        transaction_id          = tx_id,
        public_transaction_id   = f"PUB-{tx_id}",
        user_id                 = USER_ID,
        account_id              = 42,
        external_transaction_id = f"EXT-{tx_id}",
        transaction_type        = "WITHDRAWAL",
        amount                  = money(amount),
        target_token            = "tok-abc",
        snapshot_balance        = money(250_000),
        snapshot_as_of          = f"{YEAR_MONTH}-20T{hour:02d}:00:00",
        succeeded_at            = f"{YEAR_MONTH}-20T{hour:02d}:00:00",
        occurred_at             = f"{YEAR_MONTH}-20T{hour:02d}:00:01",
        recorded_at             = f"{YEAR_MONTH}-20T{hour:02d}:00:02",
        external_type           = "CARD",
        timestamp_source        = "CORE_BANKING",
    ).SerializeToString()


def monthly(revision: int, income: int, expense: int, transfer: int) -> bytes:
    return ev.MonthlyCashflowAggregatedEvent(
        event_id        = f"m-{revision}",
        user_id         = USER_ID,
        year_month      = YEAR_MONTH,
        revision        = revision,
        income_amount   = money(income),
        expense_amount  = money(expense),
        transfer_amount = money(transfer),
        calculated_at   = "2026-10-01T00:00:00",
    ).SerializeToString()


def category(revision: int, items: list[tuple[str, int]]) -> bytes:
    return ev.CategoryCashflowAggregatedEvent(
        event_id      = f"c-{revision}",
        user_id       = USER_ID,
        year_month    = YEAR_MONTH,
        revision      = revision,
        categories    = [
            ev.CategoryCashflowAmount(category=c, amount=money(a)) for c, a in items
        ],
        calculated_at = "2026-10-01T00:00:00",
    ).SerializeToString()


async def main() -> int:
    producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    await producer.start()
    print(f"Kafka 연결: {settings.KAFKA_BOOTSTRAP_SERVERS}\n")

    async def send(topic: str, key: str, value: bytes, label: str):
        await producer.send_and_wait(topic, key=key.encode(), value=value)
        print(f"  → {label:<46} ({len(value)} bytes)")

    try:
        print("1. 거래 성공 이벤트 12건")
        print("   (별명 판정 기준이 '10건 이상' 이므로 넘겨서 보낸다)")
        for i in range(12):
            await send(
                settings.KAFKA_TOPIC_TRANSACTION_SUCCEEDED,
                str(9_000_000 + i),
                transaction(9_000_000 + i, 12_000 + i * 1_000, hour=10 + (i % 10)),
                f"tx #{i + 1}  {12_000 + i * 1_000:,}원",
            )

        print("\n2. 같은 거래 재전송 (멱등성 — 중복 적재되면 안 됨)")
        await send(
            settings.KAFKA_TOPIC_TRANSACTION_SUCCEEDED,
            "9000000", transaction(9_000_000, 12_000), "tx #1 재전송",
        )

        print("\n3. 월별 집계")
        await send(settings.KAFKA_TOPIC_MONTHLY_CASHFLOW, USER_ID,
                   monthly(1, 1_200_000, 830_000, 50_000), "rev=1  지출 830,000")
        await send(settings.KAFKA_TOPIC_MONTHLY_CASHFLOW, USER_ID,
                   monthly(1, 999, 999, 999), "rev=1 재전송 (무시돼야 함)")
        await send(settings.KAFKA_TOPIC_MONTHLY_CASHFLOW, USER_ID,
                   monthly(2, 1_250_000, 845_000, 50_000), "rev=2  재집계 (반영돼야 함)")

        print("\n4. 카테고리 집계")
        await send(settings.KAFKA_TOPIC_CATEGORY_CASHFLOW, USER_ID,
                   category(2, [("FOOD", 420_000), ("CAFE", 150_000),
                                ("TRANSPORT", 130_000), ("UNCLASSIFIED", 145_000)]),
                   "rev=2  4개 카테고리")
        await send(settings.KAFKA_TOPIC_CATEGORY_CASHFLOW, USER_ID,
                   category(1, [("FOOD", 1)]), "rev=1 역순 도착 (무시돼야 함)")
    finally:
        await producer.stop()

    print("\n주입 완료. 몇 초 뒤 결과를 확인한다.")
    return 0


sys.exit(asyncio.run(main()))
