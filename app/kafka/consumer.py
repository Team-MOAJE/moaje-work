import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ai.spending_service import SpendingAnalysisService

logger = logging.getLogger(__name__)


async def handle_transaction_created(data: dict):
    user_id = int(data.get("user_id", 0))
    if not user_id:
        return
    logger.info(f"📥 거래 수신 | user_id={user_id} | amount={data.get('amount')}")
    from app.redis.client import get_redis
    redis = await get_redis()
    await redis.delete(f"daily_limit:{user_id}")
    await redis.delete(f"spending_profile:{user_id}")
    logger.info(f"🗑️ Redis 캐시 무효화 | user_id={user_id}")


async def handle_user_registered(data: dict):
    user_id = data.get("user_id")
    if not user_id:
        return
    logger.info(f"📥 신규 유저 등록 | user_id={user_id}")
    async with AsyncSessionLocal() as session:
        service = SpendingAnalysisService(session)
        await service.get_or_create_profile(int(user_id))
        await session.commit()
        logger.info(f"✅ 소비 프로필 자동 생성 완료 | user_id={user_id}")


TOPIC_HANDLERS = {
    settings.KAFKA_TOPIC_BALANCE_DEDUCTED:     handle_transaction_created,
    settings.KAFKA_TOPIC_DAILY_BUDGET_UPDATED: handle_transaction_created,
    settings.KAFKA_TOPIC_USER_REGISTERED:      handle_user_registered,
}


async def start_consumer():
    """
    Kafka Consumer — Kafka 연결 실패 시 재시도
    서비스가 죽지 않고 백그라운드에서 계속 재시도
    """
    topics = list(TOPIC_HANDLERS.keys())
    logger.info(f"🎧 Kafka Consumer 시작 시도 | topics={topics}")

    while True:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="moaje-work-group",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
        )
        try:
            await consumer.start()
            logger.info("✅ Kafka Consumer 연결 성공!")
            try:
                async for msg in consumer:
                    try:
                        handler = TOPIC_HANDLERS.get(msg.topic)
                        if handler:
                            await handler(msg.value)
                    except Exception as e:
                        logger.error(f"❌ 메시지 처리 오류 | topic={msg.topic} | error={e}")
            finally:
                await consumer.stop()
        except (KafkaConnectionError, Exception) as e:
            logger.warning(f"⚠️ Kafka 연결 실패, 10초 후 재시도... | error={e}")
            await asyncio.sleep(10)
