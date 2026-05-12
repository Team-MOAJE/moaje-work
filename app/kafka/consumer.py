import asyncio
import json
import logging
from decimal import Decimal

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ai.spending_service import SpendingAnalysisService

logger = logging.getLogger(__name__)


async def handle_transaction_created(data: dict):
    """거래 발생 → Redis 캐시 무효화"""
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
    """신규 유저 가입 → 소비 프로필 자동 생성"""
    user_id = data.get("user_id")
    if not user_id:
        return
    logger.info(f"📥 신규 유저 등록 | user_id={user_id}")
    async with AsyncSessionLocal() as session:
        service = SpendingAnalysisService(session)
        await service.get_or_create_profile(int(user_id))
        await session.commit()
        logger.info(f"✅ 소비 프로필 자동 생성 완료 | user_id={user_id}")


async def handle_fds_alert(data: dict):
    """
    ✅ FDS 이상거래 알림 처리
    work.fds.alert 이벤트 수신 시
    fds_alert_log 테이블에 사용자 알림 자동 생성
    """
    user_id        = data.get("user_id")
    transaction_id = data.get("transaction_id", "unknown")
    risk_level     = data.get("risk_level", "HIGH")
    reason_code    = data.get("reason_code", "")
    amount         = data.get("amount", "0")
    merchant       = data.get("merchant", "")

    if not user_id:
        return

    logger.warning(f"🚨 FDS Alert 수신 | user_id={user_id} | risk={risk_level} | reason={reason_code}")

    # 알림 메시지 생성
    message = _generate_alert_message(risk_level, reason_code, amount, merchant)

    async with AsyncSessionLocal() as session:
        from app.models.fds import FdsAlertLog, RiskLevel
        alert = FdsAlertLog(
            user_id        = int(user_id),
            transaction_id = transaction_id,
            amount         = Decimal(str(amount)),
            merchant       = merchant,
            risk_level     = RiskLevel(risk_level),
            reason_code    = reason_code,
            message        = message,
            is_confirmed   = False,
        )
        session.add(alert)
        await session.commit()
        logger.info(f"✅ 이상거래 알림 생성 완료 | user_id={user_id} | alert_id={alert.id}")


def _generate_alert_message(risk_level: str, reason_code: str, amount: str, merchant: str) -> str:
    """사용자에게 표시할 알림 메시지 생성"""
    try:
        amount_str = f"{int(float(amount)):,}원"
    except Exception:
        amount_str = f"{amount}원"

    merchant_str = f" ({merchant})" if merchant else ""

    if "RULE_ABNORMAL_AMOUNT" in reason_code:
        return f"🚨 평소보다 큰 금액의 결제가 감지됐어요! {amount_str}{merchant_str} — 본인 거래가 맞나요?"
    elif "RULE_ABNORMAL_TIME" in reason_code:
        return f"🚨 새벽 시간대에 결제가 감지됐어요! {amount_str}{merchant_str} — 본인 거래가 맞나요?"
    elif "RULE_RAPID_REPEAT" in reason_code:
        return f"🚨 단시간 내 반복 결제가 감지됐어요! {amount_str}{merchant_str} — 본인 거래가 맞나요?"
    elif "ML_HIGH_RISK" in reason_code:
        return f"🚨 AI가 이상거래를 탐지했어요! {amount_str}{merchant_str} — 본인 거래가 맞나요?"
    else:
        return f"🚨 이상거래가 탐지됐어요! {amount_str}{merchant_str} — 본인 거래가 맞나요?"


TOPIC_HANDLERS = {
    settings.KAFKA_TOPIC_BALANCE_DEDUCTED:     handle_transaction_created,
    settings.KAFKA_TOPIC_DAILY_BUDGET_UPDATED: handle_transaction_created,
    settings.KAFKA_TOPIC_USER_REGISTERED:      handle_user_registered,
    # ✅ FDS Alert 토픽 추가
    "work.fds.alert":                          handle_fds_alert,
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
