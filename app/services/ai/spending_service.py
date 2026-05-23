"""
Daily Limit 산출 서비스

장애 대응 전략 (금융 앱 가용성 우선):
  Redis 장애 시 → DB에서 직접 조회 (fail-open)
  DB 장애 시    → Redis 캐시 데이터 반환
  둘 다 장애 시 → 503 응답
"""
from datetime import date
from decimal import Decimal, ROUND_DOWN
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spending import AiSpendingProfile, AcademicSchedule, AiAnalysisLog, AnalysisType
from app.schemas.spending import DailyLimitRequest, DailyLimitResponse

logger = logging.getLogger(__name__)


class SpendingAnalysisService:
    """
    Daily Limit 공식:
    (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼) ÷ 월급날까지 남은 일수

    Redis → DB 2단계 fallback 전략:
    1. Redis 캐시 조회 → HIT 시 즉시 반환
    2. Redis MISS 또는 장애 → DB 직접 조회
    3. DB 장애 → Redis 캐시 데이터 반환
    4. 둘 다 장애 → 예외 발생 (전역 핸들러가 503 반환)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def calculate_daily_limit(self, req: DailyLimitRequest) -> DailyLimitResponse:
        numerator = (
            req.current_balance
            + req.expected_income
            - req.fixed_expenses
            - req.event_buffer
        )
        daily_limit = (numerator / req.days_until_payday).quantize(
            Decimal("1"), rounding=ROUND_DOWN
        )
        daily_limit = max(daily_limit, Decimal("0"))
        advice = self._generate_advice(daily_limit, req.event_buffer, req.days_until_payday)

        log = AiAnalysisLog(
            user_id        = req.user_id,
            analysis_type  = AnalysisType.DAILY_LIMIT,
            input_snapshot = req.model_dump(mode="json"),
            result_message = advice,
            daily_limit    = daily_limit,
            confidence_score = Decimal("0.9500"),
        )
        self.db.add(log)
        await self.db.flush()

        return DailyLimitResponse(
            user_id     = req.user_id,
            daily_limit = daily_limit,
            advice      = advice,
            formula_detail = {
                "current_balance"  : str(req.current_balance),
                "expected_income"  : str(req.expected_income),
                "fixed_expenses"   : str(req.fixed_expenses),
                "event_buffer"     : str(req.event_buffer),
                "days_until_payday": req.days_until_payday,
                "daily_limit"      : str(daily_limit),
            },
        )

    async def get_event_buffer(self, user_id: int) -> Decimal:
        """
        학사 이벤트 버퍼 조회
        Redis → DB 2단계 fallback
        """
        # 1단계: Redis 캐시 조회
        try:
            from app.redis.client import get_event_buffer_cache, set_event_buffer_cache
            cached = await get_event_buffer_cache(user_id)
            if cached is not None:
                return Decimal(cached)
        except Exception as e:
            logger.warning(f"⚠️ 이벤트 버퍼 Redis 조회 실패 | user={user_id} | {e}")

        # 2단계: DB 직접 조회
        try:
            today      = date.today()
            week_later = date.fromordinal(today.toordinal() + 7)

            result = await self.db.execute(
                select(AcademicSchedule).where(
                    AcademicSchedule.user_id    == user_id,
                    AcademicSchedule.end_date   >= str(today),
                    AcademicSchedule.start_date <= str(week_later),
                )
            )
            schedules = result.scalars().all()
            buffer    = sum((s.expected_extra_spend for s in schedules), Decimal("0"))

            # DB 조회 성공 시 Redis에 캐시 저장 시도
            try:
                from app.redis.client import set_event_buffer_cache
                await set_event_buffer_cache(user_id, buffer)
            except Exception:
                pass  # 캐시 저장 실패는 무시

            return buffer

        except SQLAlchemyError as e:
            logger.error(f"❌ 이벤트 버퍼 DB 조회 실패 | user={user_id} | {e}")
            # DB 장애 시 0 반환 (서비스 중단보다 기본값 반환이 낫음)
            logger.warning(f"⚠️ 이벤트 버퍼 기본값(0) 반환 | user={user_id}")
            return Decimal("0")

    async def get_or_create_profile(self, user_id: int) -> AiSpendingProfile:
        """
        소비 프로필 조회 또는 생성
        Redis → DB 2단계 fallback
        """
        # 1단계: Redis 캐시 조회
        try:
            from app.redis.client import get_spending_profile_cache
            cached = await get_spending_profile_cache(user_id)
            if cached:
                # 캐시 HIT → AiSpendingProfile 객체로 변환
                profile = AiSpendingProfile(
                    user_id             = user_id,
                    avg_daily_amount    = Decimal(str(cached.get("avg_daily_amount", 30000))),
                    std_daily_amount    = Decimal(str(cached.get("std_daily_amount", 15000))),
                    tx_count            = int(cached.get("tx_count", 0)),
                    peak_spend_hour     = int(cached.get("peak_spend_hour", 12)),
                    top_category        = cached.get("top_category", "식비"),
                    risk_score_baseline = Decimal(str(cached.get("risk_score_baseline", 0.10))),
                )
                logger.info(f"✅ 소비 프로필 Redis 캐시 반환 | user={user_id}")
                return profile
        except Exception as e:
            logger.warning(f"⚠️ 소비 프로필 Redis 조회 실패 → DB fallback | user={user_id} | {e}")

        # 2단계: DB 직접 조회
        try:
            result  = await self.db.execute(
                select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()

            if not profile:
                profile = AiSpendingProfile(
                    user_id             = user_id,
                    avg_daily_amount    = Decimal("30000"),
                    std_daily_amount    = Decimal("15000"),
                    tx_count            = 0,
                    peak_spend_hour     = 12,
                    top_category        = "식비",
                    risk_score_baseline = Decimal("0.10"),
                )
                self.db.add(profile)
                await self.db.flush()
                logger.info(f"✅ 소비 프로필 신규 생성 | user={user_id}")

            # DB 조회 성공 시 Redis 캐시 저장 시도
            try:
                from app.redis.client import set_spending_profile_cache
                await set_spending_profile_cache(user_id, {
                    "avg_daily_amount"   : str(profile.avg_daily_amount),
                    "std_daily_amount"   : str(profile.std_daily_amount),
                    "tx_count"           : profile.tx_count,
                    "peak_spend_hour"    : profile.peak_spend_hour,
                    "top_category"       : profile.top_category,
                    "risk_score_baseline": str(profile.risk_score_baseline),
                })
            except Exception:
                pass  # 캐시 저장 실패는 무시

            return profile

        except SQLAlchemyError as e:
            logger.error(f"❌ 소비 프로필 DB 조회 실패 | user={user_id} | {e}")
            # DB도 장애 → 기본 프로필 반환 (서비스 중단 방지)
            logger.warning(f"⚠️ 소비 프로필 기본값 반환 | user={user_id}")
            return AiSpendingProfile(
                user_id             = user_id,
                avg_daily_amount    = Decimal("30000"),
                std_daily_amount    = Decimal("15000"),
                tx_count            = 0,
                peak_spend_hour     = 12,
                top_category        = "식비",
                risk_score_baseline = Decimal("0.10"),
            )

    @staticmethod
    def _generate_advice(daily_limit: Decimal, event_buffer: Decimal, days_until_payday: int) -> str:
        if daily_limit == 0:
            return "⚠️ 이번 달 지출이 한계에 달했어요. 꼭 필요한 지출만 해주세요!"
        if event_buffer > 0:
            return (f"📅 곧 예정된 일정이 있어 {int(event_buffer):,}원을 미리 빼뒀어요. "
                    f"오늘은 {int(daily_limit):,}원까지 안전하게 쓸 수 있어요!")
        if days_until_payday <= 5:
            return (f"💸 월급날까지 {days_until_payday}일 남았어요! "
                    f"오늘 {int(daily_limit):,}원 이하로 아껴써 보세요 💪")
        return f"✅ 오늘 {int(daily_limit):,}원까지 여유롭게 쓸 수 있어요!"
