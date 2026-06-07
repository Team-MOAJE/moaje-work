"""
소비 리포트 카드 서비스
학기별 소비 통계, FDS 요약, 학사 이벤트별 지출 분석
"""
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spending import AiSpendingProfile, AcademicSchedule, AiAnalysisLog, EventType
from app.models.fds import FdsInferenceLog, RiskLevel
from app.schemas.spending import (
    SemesterReportResponse, SpendingSummary,
    FdsSummary, EventSpendingStat,
)

# 학기 기간 정의
SEMESTER_PERIODS: dict[tuple[int, int], tuple[date, date]] = {
    (2026, 1): (date(2026, 3, 2),  date(2026, 6, 20)),
    (2026, 2): (date(2026, 9, 1),  date(2026, 12, 20)),
    (2025, 1): (date(2025, 3, 3),  date(2025, 6, 20)),
    (2025, 2): (date(2025, 9, 1),  date(2025, 12, 19)),
}

EXAM_TYPES = {EventType.MIDTERM, EventType.FINAL}
EVENT_TYPES_FOR_REPORT = {
    EventType.MIDTERM, EventType.FINAL,
    EventType.MT, EventType.FESTIVAL, EventType.EMPLOYMENT,
}


class ReportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_semester_report(
        self, user_id: int, year: int, semester: int
    ) -> SemesterReportResponse:

        # 학기 기간 확정
        key = (year, semester)
        if key in SEMESTER_PERIODS:
            period_start, period_end = SEMESTER_PERIODS[key]
        else:
            period_start = date(year, 3, 1) if semester == 1 else date(year, 9, 1)
            period_end   = date(year, 6, 30) if semester == 1 else date(year, 12, 31)

        spending = await self._get_spending_summary(user_id, period_start, period_end)
        fds      = await self._get_fds_summary(user_id, period_start, period_end)
        events   = await self._get_event_stats(user_id, period_start, period_end, spending.avg_daily_limit)

        overall_score, overall_grade = self._calc_overall(fds, spending)
        summary_message = self._generate_summary(overall_grade, spending, fds, events)
        badges          = self._generate_badges(spending, fds, events)

        return SemesterReportResponse(
            user_id         = user_id,
            year            = year,
            semester        = semester,
            period_label    = f"{year}년 {semester}학기",
            period_start    = period_start,
            period_end      = period_end,
            spending        = spending,
            fds             = fds,
            events          = events,
            overall_grade   = overall_grade,
            overall_score   = overall_score,
            summary_message = summary_message,
            badges          = badges,
        )

    # ── 소비 통계 ────────────────────────────────────

    async def _get_spending_summary(
        self, user_id: int, start: date, end: date
    ) -> SpendingSummary:

        result = await self.db.execute(
            select(AiAnalysisLog).where(
                and_(
                    AiAnalysisLog.user_id       == user_id,
                    AiAnalysisLog.analysis_type == "DAILY_LIMIT",
                    func.date(AiAnalysisLog.created_at) >= start,
                    func.date(AiAnalysisLog.created_at) <= end,
                )
            ).order_by(AiAnalysisLog.created_at)
        )
        logs = result.scalars().all()

        # 소비 프로필 조회
        profile_r = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
        )
        profile = profile_r.scalar_one_or_none()

        if not logs:
            return SpendingSummary(
                total_tx_count      = profile.tx_count if profile else 0,
                avg_daily_limit     = Decimal("0"),
                peak_spend_date     = str(start),
                peak_spend_amount   = Decimal("0"),
                lowest_spend_date   = str(start),
                lowest_spend_amount = Decimal("0"),
                peak_spend_hour     = profile.peak_spend_hour if profile else 12,
                top_category        = profile.top_category if profile else "없음",
            )

        daily = [
            (str(log.created_at.date()), log.daily_limit)
            for log in logs if log.daily_limit is not None
        ]

        if daily:
            peak   = max(daily, key=lambda x: x[1])
            lowest = min(daily, key=lambda x: x[1])
            avg    = sum(v for _, v in daily) / len(daily)
        else:
            peak = lowest = (str(start), Decimal("0"))
            avg  = Decimal("0")

        return SpendingSummary(
            total_tx_count      = profile.tx_count if profile else len(logs),
            avg_daily_limit     = Decimal(str(round(avg, 0))),
            peak_spend_date     = peak[0],
            peak_spend_amount   = peak[1],
            lowest_spend_date   = lowest[0],
            lowest_spend_amount = lowest[1],
            peak_spend_hour     = profile.peak_spend_hour if profile else 12,
            top_category        = profile.top_category if profile else "없음",
        )

    # ── FDS 요약 ─────────────────────────────────────

    async def _get_fds_summary(
        self, user_id: int, start: date, end: date
    ) -> FdsSummary:

        result = await self.db.execute(
            select(FdsInferenceLog).where(
                and_(
                    FdsInferenceLog.user_id == user_id,
                    func.date(FdsInferenceLog.created_at) >= start,
                    func.date(FdsInferenceLog.created_at) <= end,
                )
            )
        )
        logs = result.scalars().all()

        high_count   = sum(1 for l in logs if l.risk_level == RiskLevel.HIGH)
        medium_count = sum(1 for l in logs if l.risk_level == RiskLevel.MEDIUM)

        safety_score = max(0, 100 - (high_count * 20) - (medium_count * 5))
        safety_grade = (
            "A" if safety_score >= 90 else
            "B" if safety_score >= 70 else
            "C" if safety_score >= 50 else "D"
        )

        reason_map: dict[str, int] = {}
        for log in logs:
            if log.reason_code:
                key = log.reason_code.split("(")[0].strip()
                reason_map[key] = reason_map.get(key, 0) + 1
        top_reason = max(reason_map, key=reason_map.get) if reason_map else "없음"

        return FdsSummary(
            total_detected = len(logs),
            high_count     = high_count,
            medium_count   = medium_count,
            safety_score   = safety_score,
            safety_grade   = safety_grade,
            top_reason     = top_reason,
        )

    # ── 학사 이벤트별 지출 ────────────────────────────

    async def _get_event_stats(
        self, user_id: int, start: date, end: date, avg_daily: Decimal
    ) -> list[EventSpendingStat]:

        result = await self.db.execute(
            select(AcademicSchedule).where(
                and_(
                    AcademicSchedule.user_id    == user_id,
                    AcademicSchedule.start_date >= str(start),
                    AcademicSchedule.end_date   <= str(end),
                    AcademicSchedule.event_type.in_(EVENT_TYPES_FOR_REPORT),
                )
            ).order_by(AcademicSchedule.start_date)
        )
        schedules = result.scalars().all()

        stats = []
        for s in schedules:
            ev_start = s.start_date.date() if hasattr(s.start_date, "date") else s.start_date
            ev_end   = s.end_date.date()   if hasattr(s.end_date,   "date") else s.end_date
            days     = max((ev_end - ev_start).days + 1, 1)

            log_r = await self.db.execute(
                select(func.avg(AiAnalysisLog.daily_limit)).where(
                    and_(
                        AiAnalysisLog.user_id == user_id,
                        func.date(AiAnalysisLog.created_at) >= ev_start,
                        func.date(AiAnalysisLog.created_at) <= ev_end,
                        AiAnalysisLog.daily_limit.isnot(None),
                    )
                )
            )
            event_avg = Decimal(str(log_r.scalar() or 0))
            total     = event_avg * days
            ratio     = (event_avg / avg_daily).quantize(Decimal("0.01")) if avg_daily > 0 else Decimal("1.00")

            stats.append(EventSpendingStat(
                event_type      = s.event_type.value,
                event_name      = s.event_name,
                start_date      = ev_start,
                end_date        = ev_end,
                period_days     = days,
                avg_daily_spend = event_avg.quantize(Decimal("1")),
                total_spend     = total.quantize(Decimal("1")),
                vs_normal_ratio = ratio,
            ))

        return stats

    # ── 종합 점수 ─────────────────────────────────────

    def _calc_overall(self, fds: FdsSummary, spending: SpendingSummary) -> tuple[int, str]:
        activity   = min(100, spending.total_tx_count * 2)
        overall    = int(fds.safety_score * 0.6 + activity * 0.4)
        overall    = max(0, min(100, overall))
        grade      = (
            "A" if overall >= 90 else
            "B" if overall >= 70 else
            "C" if overall >= 50 else "D"
        )
        return overall, grade

    # ── 한 줄 총평 ────────────────────────────────────

    def _generate_summary(
        self,
        grade  : str,
        spending: SpendingSummary,
        fds    : FdsSummary,
        events : list[EventSpendingStat],
    ) -> str:
        exam_ratio = max(
            (e.vs_normal_ratio for e in events if e.event_type in ("MIDTERM", "FINAL")),
            default=Decimal("1.0")
        )
        if grade == "A":
            return "🏆 이번 학기 소비를 완벽하게 관리했어요! 안전한 학기였어요."
        elif grade == "B":
            if exam_ratio > Decimal("1.5"):
                return f"📚 시험기간 지출이 평소의 {exam_ratio}배였어요. 다음 학기엔 미리 대비해봐요!"
            return "✅ 안정적인 소비 습관을 유지했어요. 조금만 더 다듬으면 A등급이에요!"
        elif grade == "C":
            if fds.high_count > 0:
                return f"⚠️ 이번 학기 이상거래가 {fds.high_count}건 탐지됐어요. 소비 패턴을 점검해보세요."
            return "💪 소비 관리가 아직 아쉬워요. Daily Limit을 더 활용해봐요!"
        else:
            return "🔴 이번 학기 소비 관리가 많이 아쉬웠어요. 다음 학기엔 모아제와 함께 계획해봐요!"

    # ── 뱃지 ─────────────────────────────────────────

    def _generate_badges(
        self,
        spending: SpendingSummary,
        fds     : FdsSummary,
        events  : list[EventSpendingStat],
    ) -> list[str]:
        badges = []

        if fds.high_count == 0 and fds.medium_count == 0:
            badges.append("🛡️ 완벽 안전 — 이상거래 0건")
        if fds.safety_grade == "A":
            badges.append("⭐ 소비 안전도 A등급")
        if spending.total_tx_count >= 50:
            badges.append("🔥 활발한 소비 습관 — 50건 이상")

        exam_events = [e for e in events if e.event_type in ("MIDTERM", "FINAL")]
        if exam_events:
            avg_ratio = sum(e.vs_normal_ratio for e in exam_events) / len(exam_events)
            if avg_ratio <= Decimal("1.2"):
                badges.append("📚 시험기간 절약왕 — 평소와 비슷한 지출")
            elif avg_ratio >= Decimal("2.0"):
                badges.append("☕ 시험기간 카페 단골")

        hour = spending.peak_spend_hour
        if 0 <= hour <= 5:
            badges.append("🦉 야행성 소비자")
        elif 11 <= hour <= 13:
            badges.append("🍱 점심시간 소비왕")
        elif 18 <= hour <= 22:
            badges.append("🌆 저녁 소비 패턴")

        if not badges:
            badges.append("📊 첫 학기 리포트 완성!")

        return badges
