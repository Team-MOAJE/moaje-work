"""
Kafka Producer
Work 서비스에서 발행하는 이벤트 처리
"""
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from app.core.config import settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await _producer.start()
        logger.info("✅ Kafka Producer 시작")
    return _producer


async def stop_producer():
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
        logger.info("🛑 Kafka Producer 종료")


async def publish_spending_analyzed(user_id: int, daily_limit: str, advice: str):
    """
    Daily Limit 계산 완료 이벤트 발행
    토픽: work.spending.analyzed
    Consumer: Asset Service, 알림 서비스
    """
    producer = await get_producer()
    payload = {
        "transaction_id": f"work-spending-{user_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "user_id": user_id,
        "daily_limit": daily_limit,
        "advice": advice,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    await producer.send_and_wait(
        topic=settings.KAFKA_TOPIC_SPENDING_ANALYZED,
        key=str(user_id),
        value=payload,
    )
    logger.info(f"📤 Kafka 발행 완료 | topic={settings.KAFKA_TOPIC_SPENDING_ANALYZED} | user_id={user_id}")


async def publish_schedule_updated(user_id: int, event_type: str, event_buffer: str):
    """
    학사 일정 등록/수정 이벤트 발행
    토픽: work.schedule.updated
    Consumer: Asset Service
    """
    producer = await get_producer()
    payload = {
        "transaction_id": f"work-schedule-{user_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "user_id": user_id,
        "event_type": event_type,
        "event_buffer": event_buffer,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    await producer.send_and_wait(
        topic=settings.KAFKA_TOPIC_SCHEDULE_UPDATED,
        key=str(user_id),
        value=payload,
    )
    logger.info(f"📤 Kafka 발행 완료 | topic={settings.KAFKA_TOPIC_SCHEDULE_UPDATED} | user_id={user_id}")


async def publish_fds_alert(user_id: int, risk_level: str, reason_code: str, amount: str = "0", merchant: str = ""):
    """
    FDS 이상거래 탐지 알림 발행
    토픽: work.fds.alert
    Consumer: Gateway, 알림 서비스
    """
    producer = await get_producer()
    payload = {
        "transaction_id": f"work-fds-{user_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "user_id": user_id,
        "risk_level": risk_level,
        "reason_code": reason_code,
        "amount": amount,
        "merchant": merchant,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    await producer.send_and_wait(
        topic=settings.KAFKA_TOPIC_FDS_ALERT,
        key=str(user_id),
        value=payload,
    )
    logger.info(f"📤 Kafka 발행 완료 | topic={settings.KAFKA_TOPIC_FDS_ALERT} | user_id={user_id}")
