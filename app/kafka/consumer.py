import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ai.spending_service import SpendingAnalysisService

logger = logging.getLogger(__name__)


async def handle_transaction_created(data: dict):
    """거래 발생 → Redis 캐시 무효화"""
    user_id = int(data.get("user_id", 0)) if data.get("user_id") else 0
    if not user_id:
        return
    logger.info(f"📥 거래 수신 | user_id={user_id} | amount={data.get('amount')}")
    try:
        from app.redis.client import get_redis
        redis = await get_redis()
        await redis.delete(f"daily_limit:{user_id}")
        await redis.delete(f"spending_profile:{user_id}")
        logger.info(f"🗑️ Redis 캐시 무효화 | user_id={user_id}")
    except Exception as e:
        logger.warning(f"⚠️ Redis 캐시 무효화 실패 (무시) | {e}")


async def handle_user_registered(data: dict):
    """신규 유저 가입 → 소비 프로필 자동 생성"""
    user_id = data.get("user_id")
    if not user_id:
        return
    logger.info(f"📥 신규 유저 등록 | user_id={user_id}")
    try:
        async with AsyncSessionLocal() as session:
            service = SpendingAnalysisService(session)
            await service.get_or_create_profile(int(user_id))
            await session.commit()
            logger.info(f"✅ 소비 프로필 자동 생성 완료 | user_id={user_id}")
    except Exception as e:
        logger.error(f"❌ 소비 프로필 생성 실패 | user_id={user_id} | {e}")


async def handle_fds_alert(data: dict):
    """FDS 이상거래 알림 → fds_alert_log 자동 생성"""
    user_id        = data.get("user_id")
    transaction_id = data.get("transaction_id", "unknown")
    risk_level     = data.get("risk_level", "HIGH")
    reason_code    = data.get("reason_code", "")
    amount         = data.get("amount", "0")
    merchant       = data.get("merchant", "")

    if not user_id:
        return

    logger.warning(f"🚨 FDS Alert 수신 | user_id={user_id} | risk={risk_level}")
    message = _generate_alert_message(risk_level, reason_code, amount, merchant)

    try:
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
    except Exception as e:
        logger.error(f"❌ 이상거래 알림 생성 실패 | user_id={user_id} | {e}")


async def handle_banking_transaction(raw: bytes):
    """
    ✅ Asset 거래 완료 이벤트 처리 (Protobuf)
    transaction_succeeded_events 토픽 수신 시
    자동으로 FDS 이상거래 분석 실행

    Asset이 발행하는 TransactionSucceededEvent (Protobuf):
      event_id, transaction_id, user_id, account_id,
      amount, transaction_type, occurred_at
    """
    try:
        from app.grpc.events.asset_events_pb2 import TransactionSucceededEvent
        event          = TransactionSucceededEvent()
        event.ParseFromString(raw)
        user_id        = str(event.user_id)
        transaction_id = str(event.transaction_id)
        amount         = event.amount.amount
        merchant       = event.transaction_type
        try:
            hour = datetime.fromisoformat(event.occurred_at).hour
        except Exception:
            hour = 12
    except Exception as e:
        logger.error(f"❌ Protobuf 파싱 실패 | {e}")
        return

    if not user_id or user_id == "0":
        logger.warning("⚠️ Banking 거래 이벤트 user_id 없음")
        return

    logger.info(
        f"📥 Asset 거래 수신 (Protobuf) | user_id={user_id} "
        f"| amount={amount} | type={merchant}"
    )

    # 1. Redis 캐시 무효화
    try:
        from app.redis.client import get_redis
        redis = await get_redis()
        await redis.delete(f"daily_limit:{user_id}")
        await redis.delete(f"spending_profile:{user_id}")
    except Exception as e:
        logger.warning(f"⚠️ Redis 캐시 무효화 실패 (무시) | {e}")

    # 2. FDS 이상거래 분석 자동 실행
    try:
        async with AsyncSessionLocal() as session:
            from app.services.fds.detector import FdsDetector
            from app.schemas.fds import FdsDetectRequest

            req = FdsDetectRequest(
                user_id        = int(user_id),
                transaction_id = transaction_id,
                amount         = Decimal(str(amount)),
                merchant       = merchant,
                hour           = int(hour),
            )

            detector = FdsDetector(session)
            result   = await detector.detect(req)
            await session.commit()

            logger.info(
                f"✅ FDS 자동 분석 완료 | user_id={user_id} "
                f"| risk={result.risk_level} | score={result.risk_score}"
            )

            if result.is_alerted:
                logger.warning(
                    f"🚨 이상거래 탐지! | user_id={user_id} | {result.reason_code}"
                )

    except Exception as e:
        logger.error(f"❌ FDS 자동 분석 실패 | user_id={user_id} | {e}")


def _generate_alert_message(
    risk_level: str, reason_code: str, amount: str, merchant: str
) -> str:
    try:
        amount_str = f"{int(float(amount)):,}원"
    except Exception:
        amount_str = f"{amount}원"

    merchant_str = " (개인 간 거래)" if merchant == "anonymous" else (f" ({merchant})" if merchant else "")

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


# Protobuf 형식으로 수신하는 토픽 목록
PROTOBUF_TOPICS = {
    settings.KAFKA_TOPIC_BANKING_TRANSACTION,
}

TOPIC_HANDLERS = {
    settings.KAFKA_TOPIC_BALANCE_DEDUCTED    : handle_transaction_created,
    settings.KAFKA_TOPIC_DAILY_BUDGET_UPDATED: handle_transaction_created,
    settings.KAFKA_TOPIC_USER_REGISTERED     : handle_user_registered,
    "work.fds.alert"                         : handle_fds_alert,
    # ✅ Asset 거래 완료 이벤트 → FDS 자동 분석 (Protobuf)
    settings.KAFKA_TOPIC_BANKING_TRANSACTION: handle_banking_transaction,
}


async def start_consumer():
    """
    Kafka Consumer — 연결 실패 시 자동 재시도
    토픽별 파싱 방식 분기:
      - PROTOBUF_TOPICS → Protobuf raw bytes 그대로 전달
      - 나머지 토픽    → JSON 파싱 후 dict 전달
    """
    topics = list(TOPIC_HANDLERS.keys())
    logger.info(f"🎧 Kafka Consumer 시작 시도 | topics={topics}")

    while True:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="moaje-work-group",
            value_deserializer=lambda v: v,  # raw bytes — 토픽별로 내부에서 파싱
            auto_offset_reset="earliest",
        )
        try:
            await consumer.start()
            logger.info("✅ Kafka Consumer 연결 성공!")
            try:
                async for msg in consumer:
                    try:
                        handler = TOPIC_HANDLERS.get(msg.topic)
                        if not handler:
                            continue

                        # 토픽별 파싱 방식 분기
                        if msg.topic in PROTOBUF_TOPICS:
                            # Protobuf: raw bytes 그대로 전달
                            await handler(msg.value)
                        else:
                            # JSON: dict로 파싱 후 전달
                            data = json.loads(msg.value.decode("utf-8"))
                            await handler(data)

                    except Exception as e:
                        logger.error(
                            f"❌ 메시지 처리 오류 | topic={msg.topic} | error={e}"
                        )
            finally:
                await consumer.stop()
        except (KafkaConnectionError, Exception) as e:
            logger.warning(f"⚠️ Kafka 연결 실패, 10초 후 재시도... | error={e}")
            await asyncio.sleep(10)
