from datetime import date
from decimal import Decimal, ROUND_DOWN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spending import AiSpendingProfile, AcademicSchedule, AiAnalysisLog, AnalysisType
from app.schemas.spending import DailyLimitRequest, DailyLimitResponse


class SpendingAnalysisService:
    """
    Daily Limit 공식:
    (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼) ÷ 월급날까지 남은 일수
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
            user_id=req.user_id,
            analysis_type=AnalysisType.DAILY_LIMIT,
            input_snapshot=req.model_dump(mode="json"),
            result_message=advice,
            daily_limit=daily_limit,
            confidence_score=Decimal("0.9500"),
        )
        self.db.add(log)
        await self.db.flush()

        return DailyLimitResponse(
            user_id=req.user_id,
            daily_limit=daily_limit,
            advice=advice,
            formula_detail={
                "current_balance"  : str(req.current_balance),
                "expected_income"  : str(req.expected_income),
                "fixed_expenses"   : str(req.fixed_expenses),
                "event_buffer"     : str(req.event_buffer),
                "days_until_payday": req.days_until_payday,
                "daily_limit"      : str(daily_limit),
            },
        )

    async def get_event_buffer(self, user_id: int) -> Decimal:
        today = date.today()
        week_later = date.fromordinal(today.toordinal() + 7)

        result = await self.db.execute(
            select(AcademicSchedule).where(
                AcademicSchedule.user_id == user_id,
                AcademicSchedule.end_date >= str(today),
                AcademicSchedule.start_date <= str(week_later),
            )
        )
        schedules = result.scalars().all()
        return sum((s.expected_extra_spend for s in schedules), Decimal("0"))

    async def get_or_create_profile(self, user_id: int) -> AiSpendingProfile:
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        if not profile:
            profile = AiSpendingProfile(
                user_id=user_id,
                avg_daily_amount=Decimal("30000"),
                peak_spend_hour=12,
                top_category="식비",
                risk_score_baseline=Decimal("0.10"),
            )
            self.db.add(profile)
            await self.db.flush()

        return profile

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
