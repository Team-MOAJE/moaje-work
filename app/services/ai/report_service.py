"""
소비 리포트 카드 서비스
학기별 소비 통계, FDS 요약, 학사 이벤트별 지출 분석
"""
from datetime import date
from decimal import Decimal
import math
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.spending import AiSpendingProfile, AcademicSchedule, AiAnalysisLog, EventType
from app.models.fds import FdsInferenceLog, FdsBlacklist, RiskLevel
from app.schemas.spending import (
    SemesterReportResponse, SpendingSummary,
    FdsSummary, EventSpendingStat, ScoreBreakdown,
)

# ── 점수 상수 (100점 만점) ────────────────────────
SAFETY_MAX        = 40   # ① 안전도
PENALTY_HIGH      = 8    #    HIGH 탐지 1건당
PENALTY_MEDIUM    = 3    #    MEDIUM 탐지 1건당
PENALTY_BLACKLIST = 10   #    블랙리스트 등록 이력

DAILY_LIMIT_MAX   = 30   # ② Daily Limit 준수율
EVENT_MAX         = 20   # ③ 이벤트 대비
REGULARITY_MAX    = 10   # ④ 소비 규칙성

# 등급 기준: A(90~100) B(75~89) C(55~74) D(54 이하)
GRADE_THRESHOLDS = [(90, "A"), (75, "B"), (55, "C"), (0, "D")]


def _grade(score: int) -> str:
    for threshold, g in GRADE_THRESHOLDS:
        if score >= threshold:
            return g
    return "D"

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

        score_breakdown = await self._calc_score_breakdown(user_id, fds, spending, events)
        overall_score   = score_breakdown.total

        # Daily Limit 기록이 없으면 100점 중 40점(준수율·규칙성)을 평가할 수 없다.
        # 이때 총점을 그대로 등급화하면 "앱을 안 써서" 낮은 점수가 나온 것을
        # "소비 관리를 못해서"로 오해하게 되므로 미평가(N/A)로 표시한다.
        is_evaluable    = spending.total_log_days > 0
        overall_grade   = _grade(overall_score) if is_evaluable else "N/A"
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
            score_breakdown = score_breakdown,
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
                total_log_days              = 0,
                daily_limit_compliance_rate = Decimal("0"),
                daily_limit_cv              = None,
            )

        daily = [
            (str(log.created_at.date()), log.daily_limit)
            for log in logs if log.daily_limit is not None
        ]

        if daily:
            values = [v for _, v in daily]
            peak   = max(daily, key=lambda x: x[1])
            lowest = min(daily, key=lambda x: x[1])
            avg    = sum(values) / len(values)

            # Daily Limit 준수율: 한도가 0원을 넘긴(= 여유가 있었던) 날 비율
            compliant_days  = sum(1 for v in values if v > 0)
            compliance_rate = Decimal(str(round(compliant_days / len(values), 4)))

            # 소비 규칙성: CV(변동계수) = 표준편차 / 평균
            if avg > 0 and len(values) > 1:
                variance = sum((v - avg) ** 2 for v in values) / len(values)
                std_dev  = Decimal(str(math.sqrt(float(variance))))
                cv       = (std_dev / Decimal(str(avg))).quantize(Decimal("0.001"))
            else:
                cv = None
        else:
            peak = lowest    = (str(start), Decimal("0"))
            avg              = Decimal("0")
            compliance_rate  = Decimal("0")
            cv               = None

        return SpendingSummary(
            total_tx_count      = profile.tx_count if profile else len(logs),
            avg_daily_limit     = Decimal(str(round(avg, 0))),
            peak_spend_date     = peak[0],
            peak_spend_amount   = peak[1],
            lowest_spend_date   = lowest[0],
            lowest_spend_amount = lowest[1],
            peak_spend_hour     = profile.peak_spend_hour if profile else 12,
            top_category        = profile.top_category if profile else "없음",
            total_log_days              = len(daily),
            daily_limit_compliance_rate = compliance_rate,
            daily_limit_cv              = cv,
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

        # ① 안전도 점수 (만점 40) — HIGH -8점, MEDIUM -3점
        # 블랙리스트 패널티(-10)는 _calc_score_breakdown 에서 별도 반영
        safety_score = max(
            0, SAFETY_MAX - (high_count * PENALTY_HIGH) - (medium_count * PENALTY_MEDIUM)
        )
        # 40점 만점을 100점 비율로 환산해 등급 산출
        safety_grade = _grade(round(safety_score / SAFETY_MAX * 100))

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

    # ── 종합 점수 (4항목 100점 채점) ──────────────────

    async def _calc_score_breakdown(
        self,
        user_id : int,
        fds     : FdsSummary,
        spending: SpendingSummary,
        events  : list[EventSpendingStat],
    ) -> ScoreBreakdown:
        safety      = await self._calc_safety_score(user_id, fds)
        daily_limit = self._calc_daily_limit_score(spending)
        event       = self._calc_event_score(events)
        regularity  = self._calc_regularity_score(spending)

        total = max(0, min(100, safety + daily_limit + event + regularity))

        return ScoreBreakdown(
            safety_score      = safety,
            daily_limit_score = daily_limit,
            event_score       = event,
            regularity_score  = regularity,
            total             = total,
        )

    async def _calc_safety_score(self, user_id: int, fds: FdsSummary) -> int:
        """① 안전도 (만점 40) — HIGH -8, MEDIUM -3, 블랙리스트 이력 -10"""
        score = fds.safety_score  # 이미 HIGH/MEDIUM 패널티 반영됨

        bl = await self.db.execute(
            select(FdsBlacklist).where(FdsBlacklist.user_id == user_id).limit(1)
        )
        if bl.scalar_one_or_none():
            score -= PENALTY_BLACKLIST

        return max(0, score)

    @staticmethod
    def _calc_daily_limit_score(spending: SpendingSummary) -> int:
        """② Daily Limit 준수율 (만점 30) — 90%↑=30 / 70~89%=22 / 50~69%=15 / 50%↓=7"""
        if spending.total_log_days == 0:
            return 0
        rate = spending.daily_limit_compliance_rate
        if rate >= Decimal("0.90"):   return 30
        elif rate >= Decimal("0.70"): return 22
        elif rate >= Decimal("0.50"): return 15
        else:                         return 7

    @staticmethod
    def _calc_event_score(events: list[EventSpendingStat]) -> int:
        """
        ③ 이벤트 대비 (만점 20) — 학기 전체 이벤트의 평균 지출 배율 기준
        1.2배↓=20 / 1.5배↓=15 / 2.0배↓=10 / 2.0배↑=5

        최댓값이 아닌 평균을 쓰는 이유:
        MT 한 번 과소비했다고 시험기간 내내 아낀 노력까지 묻히면
        "학기 전체 관리 능력"을 평가한다는 취지와 어긋난다.
        등록된 이벤트가 없으면 감점 사유가 없으므로 만점.
        """
        if not events:
            return EVENT_MAX
        avg_ratio = sum(e.vs_normal_ratio for e in events) / Decimal(len(events))
        if avg_ratio <= Decimal("1.2"):   return 20
        elif avg_ratio <= Decimal("1.5"): return 15
        elif avg_ratio <= Decimal("2.0"): return 10
        else:                             return 5

    @staticmethod
    def _calc_regularity_score(spending: SpendingSummary) -> int:
        """④ 소비 규칙성 (만점 10) — CV 0.5↓=10 / 0.8↓=7 / 1.2↓=5 / 1.2↑=3"""
        cv = spending.daily_limit_cv
        if cv is None:
            return 0
        if cv <= Decimal("0.5"):   return 10
        elif cv <= Decimal("0.8"): return 7
        elif cv <= Decimal("1.2"): return 5
        else:                      return 3

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
        # 평가 불가(N/A) — Daily Limit 기록이 없어 채점 자체가 성립하지 않는 경우
        if grade == "N/A":
            return "📊 아직 분석할 소비 기록이 부족해요. Daily Limit을 사용해보면 리포트가 채워져요!"

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

        # 거래·기록이 전혀 없으면 "탐지 0건"은 안전한 게 아니라 데이터가 없는 것.
        # 기본값(peak_spend_hour=12)으로 시간대 뱃지가 붙는 것도 막는다.
        has_activity = spending.total_log_days > 0 or spending.total_tx_count > 0
        if not has_activity:
            return ["📊 첫 학기 리포트 완성! — 소비 기록이 쌓이면 뱃지가 열려요"]

        if fds.high_count == 0 and fds.medium_count == 0:
            badges.append("🛡️ 완벽 안전 — 이상거래 0건")
        if fds.safety_grade == "A":
            badges.append("⭐ 소비 안전도 A등급")
        if spending.total_log_days > 0 and spending.daily_limit_compliance_rate >= Decimal("0.9"):
            badges.append("💰 Daily Limit 준수왕 — 90% 이상 달성")
        if spending.daily_limit_cv is not None and spending.daily_limit_cv <= Decimal("0.5"):
            badges.append("📐 소비 규칙성 최우수 — 기복 없는 소비")
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
