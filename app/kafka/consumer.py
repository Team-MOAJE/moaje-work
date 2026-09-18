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

# 공용 Redis 충돌 방지용 도메인 접두어
KEY_PREFIX = settings.REDIS_KEY_PREFIX


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


async def handle_transaction_succeeded(raw: bytes):
    """
    Asset 거래 성공 이벤트 처리 (Protobuf)

    토픽: transaction_succeeded_events
    2026-09-15 계약 확정 — Banking 은 Asset 에만 발행하므로
    Work 는 Asset 을 통해 거래를 수신한다.

    수행 작업:
      1. Redis 캐시 무효화
      2. FDS 이상거래 자동 분석
      3. 소비 프로필 갱신 (Welford)

    proto: moaje.events.asset.TransactionSucceededEvent
    """
    from app.grpc.events import asset_events_pb2

    try:
        event = asset_events_pb2.TransactionSucceededEvent()
        event.ParseFromString(raw)
    except Exception as e:
        logger.error(f"❌ Protobuf 파싱 실패 | transaction_succeeded_events | {e}")
        return

    user_id = event.user_id
    if not user_id:
        logger.warning("⚠️ 거래 이벤트 user_id 없음")
        return

    # 중복 판정 기준: public_transaction_id 우선, 없으면 내부 transaction_id
    # (어느 값을 정본으로 삼을지 Asset 담당자와 확인 필요)
    transaction_id = event.public_transaction_id or str(event.transaction_id)
    amount         = Decimal(str(event.amount.amount))

    # TransactionSucceededEvent 에는 가맹점 정보가 없다.
    # Banking 이 category_code·merchant_name 을 추가하기로 했으나
    # Asset 이 이를 전달하는지 확인 전까지는 빈 값으로 둔다.
    merchant = ""

    # 발생 시각: succeeded_at 우선, 없으면 occurred_at
    # 통계 날짜를 메시지 수신 시각으로 대체하지 않는다.
    ts = event.succeeded_at or event.occurred_at
    try:
        hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
    except Exception:
        hour = 12

    logger.info(
        f"📥 Asset 거래 수신 (Protobuf) | user_id={user_id} "
        f"| amount={amount} | type={event.transaction_type} | tx={transaction_id}"
    )

    # 1. Redis 캐시 무효화
    try:
        from app.redis.client import get_redis
        redis = await get_redis()
        await redis.delete(f"{KEY_PREFIX}daily_limit:{user_id}")
        await redis.delete(f"{KEY_PREFIX}spending_profile:{user_id}")
    except Exception as e:
        logger.warning(f"⚠️ Redis 캐시 무효화 실패 (무시) | {e}")

    # 2. FDS 이상거래 분석 자동 실행
    try:
        async with AsyncSessionLocal() as session:
            from app.services.fds.detector import FdsDetector
            from app.schemas.fds import FdsDetectRequest
            from app.models.fds import FdsInferenceLog
            from sqlalchemy import select

            # 중복 소비 방지 (멱등성)
            # Kafka 는 at-least-once 전달이므로 같은 메시지가 재전송될 수 있다.
            # 그대로 두면 FDS 로그가 중복 적재되고 tx_count 가 이중 증가해
            # 소비 프로필의 평균·표준편차가 왜곡된다.
            dup = await session.execute(
                select(FdsInferenceLog.id).where(
                    FdsInferenceLog.user_id        == int(user_id),
                    FdsInferenceLog.transaction_id == str(transaction_id),
                ).limit(1)
            )
            if dup.scalar_one_or_none() is not None:
                logger.info(
                    f"⏭️ 이미 처리된 거래 — 건너뜀 | user_id={user_id} "
                    f"| transaction_id={transaction_id}"
                )
                return

            req = FdsDetectRequest(
                user_id        = int(user_id),
                transaction_id = str(transaction_id),
                amount         = amount,
                merchant       = merchant,
                hour           = int(hour),
            )

            detector = FdsDetector(session)
            result   = await detector.detect(req)

            # 3. 소비 프로필 갱신 (FDS 탐지 이후 실행)
            #    detect() 가 현재 프로필의 avg/std 로 Z-score 를 계산하므로
            #    이번 거래를 반영하기 전에 탐지를 먼저 끝내야 한다.
            await SpendingAnalysisService(session).update_profile_from_transaction(
                user_id  = int(user_id),
                amount   = amount,
                hour     = int(hour),
                category = "",
                already_counted = True,   # detect() 가 이미 tx_count 를 증가시킴
            )

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


async def handle_monthly_cashflow(data: dict):
    """
    Asset 월별 집계 수신 → Money Recap 원천 저장

    토픽: moaje.asset.monthly-cashflow-aggregated

    revision 규칙 (kafka-topics.md):
      늦게 도착한 거래로 Asset 이 재집계하면 같은 (userId, yearMonth) 에
      더 큰 revision 으로 다시 발행된다. Work 는 가장 큰 revision 만
      최종 결과로 사용해야 하므로, 더 작거나 같은 revision 은 무시한다.
      (Kafka 재전송으로 같은 메시지가 다시 와도 이 규칙이 멱등성을 보장한다)
    """
    from app.models.spending import MonthlyCashflow
    from sqlalchemy import select

    user_id    = data.get("userId") or data.get("user_id")
    year_month = data.get("yearMonth") or data.get("year_month")
    revision   = int(data.get("revision", 0))

    if not user_id or not year_month:
        logger.warning("⚠️ 월별 집계 이벤트 필수 필드 누락 (userId / yearMonth)")
        return

    user_id = int(user_id)

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MonthlyCashflow).where(
                    MonthlyCashflow.user_id    == user_id,
                    MonthlyCashflow.year_month == year_month,
                )
            )
            row = result.scalar_one_or_none()

            if row and row.revision >= revision:
                logger.info(
                    f"⏭️ 이전 revision — 무시 | user={user_id} | {year_month} "
                    f"| 수신 rev={revision} ≤ 보관 rev={row.revision}"
                )
                return

            income   = Decimal(str(data.get("totalIncome",   data.get("total_income",   0))))
            expense  = Decimal(str(data.get("totalExpense",  data.get("total_expense",  0))))
            transfer = Decimal(str(data.get("totalTransfer", data.get("total_transfer", 0))))

            if row:
                row.revision       = revision
                row.total_income   = income
                row.total_expense  = expense
                row.total_transfer = transfer
            else:
                session.add(MonthlyCashflow(
                    user_id        = user_id,
                    year_month     = year_month,
                    revision       = revision,
                    total_income   = income,
                    total_expense  = expense,
                    total_transfer = transfer,
                ))

            await session.commit()
            logger.info(
                f"✅ 월별 집계 저장 | user={user_id} | {year_month} | rev={revision} "
                f"| 수입={income} 지출={expense}"
            )

    except Exception as e:
        logger.error(f"❌ 월별 집계 처리 실패 | user={user_id} | {e}")


async def handle_category_cashflow(data: dict):
    """
    Asset 카테고리 집계 수신 → 소비패턴 별명 · Recap 원천 저장

    토픽: moaje.asset.category-cashflow-aggregated

    한 메시지에 여러 카테고리가 담겨 오는 경우와
    카테고리 하나씩 오는 경우를 모두 처리한다.
    revision 규칙은 월별 집계와 동일하다.
    """
    from app.models.spending import CategoryCashflow
    from sqlalchemy import select

    user_id    = data.get("userId") or data.get("user_id")
    year_month = data.get("yearMonth") or data.get("year_month")
    revision   = int(data.get("revision", 0))

    if not user_id or not year_month:
        logger.warning("⚠️ 카테고리 집계 이벤트 필수 필드 누락 (userId / yearMonth)")
        return

    user_id = int(user_id)

    # payload 형태 흡수: categories 배열 또는 단일 카테고리
    categories = data.get("categories")
    if not categories:
        code = data.get("categoryCode") or data.get("category_code")
        if not code:
            logger.warning("⚠️ 카테고리 집계 이벤트에 카테고리 정보 없음")
            return
        categories = [{
            "categoryCode": code,
            "amount"      : data.get("amount", 0),
            "txCount"     : data.get("txCount", data.get("tx_count", 0)),
        }]

    try:
        async with AsyncSessionLocal() as session:
            saved = skipped = 0

            for c in categories:
                code = c.get("categoryCode") or c.get("category_code")
                if not code:
                    continue

                amount   = Decimal(str(c.get("amount", 0)))
                tx_count = int(c.get("txCount", c.get("tx_count", 0)))

                result = await session.execute(
                    select(CategoryCashflow).where(
                        CategoryCashflow.user_id       == user_id,
                        CategoryCashflow.year_month    == year_month,
                        CategoryCashflow.category_code == code,
                    )
                )
                row = result.scalar_one_or_none()

                if row and row.revision >= revision:
                    skipped += 1
                    continue

                if row:
                    row.revision = revision
                    row.amount   = amount
                    row.tx_count = tx_count
                else:
                    session.add(CategoryCashflow(
                        user_id       = user_id,
                        year_month    = year_month,
                        category_code = code,
                        revision      = revision,
                        amount        = amount,
                        tx_count      = tx_count,
                    ))
                saved += 1

            await session.commit()
            logger.info(
                f"✅ 카테고리 집계 저장 | user={user_id} | {year_month} "
                f"| rev={revision} | 저장 {saved}건 / 무시 {skipped}건"
            )

    except Exception as e:
        logger.error(f"❌ 카테고리 집계 처리 실패 | user={user_id} | {e}")


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


# Protobuf 로 수신하는 토픽
# 이 집합에 포함된 토픽은 raw bytes 그대로 핸들러에 전달되고,
# 나머지는 JSON 으로 파싱되어 dict 로 전달된다.
PROTOBUF_TOPICS = {
    settings.KAFKA_TOPIC_TRANSACTION_SUCCEEDED,
}

# 구독 토픽 → 핸들러
#
# 2026-09-15 회의 확정 계약 (kafka-topics.md) 기준으로 정리했다.
# 제거된 토픽:
#   banking.transaction.created  — Banking 은 Asset 에만 발행한다
#   asset.balance.deducted       — Asset 발행 목록에 없음
#   asset.daily.budget.updated   — Asset 발행 목록에 없음
# 거래 수신 경로는 Asset 의 transaction_succeeded_events 로 일원화되었다.
TOPIC_HANDLERS = {
    # Asset → Work
    settings.KAFKA_TOPIC_TRANSACTION_SUCCEEDED: handle_transaction_succeeded,  # Protobuf
    settings.KAFKA_TOPIC_MONTHLY_CASHFLOW     : handle_monthly_cashflow,
    settings.KAFKA_TOPIC_CATEGORY_CASHFLOW    : handle_category_cashflow,

    # Auth → Work (Auth 측 발행 미구현 — 수신되지 않아도 무방)
    settings.KAFKA_TOPIC_USER_REGISTERED      : handle_user_registered,

    # Work → Work (자기 구독)
    settings.KAFKA_TOPIC_FDS_ALERT            : handle_fds_alert,
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
            group_id=settings.KAFKA_CONSUMER_GROUP,
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
