"""
재방문 검증 지표

기획안 6절:
  "문항별 이탈률·온보딩 완료율·시뮬레이터 재사용률·다음 달 Recap
   열람률을 확인합니다. 시제품에서는 사용성을, 실제 이용 데이터에서는
   재방문을 검증합니다. 더미 거래만으로 이탈 감소를 주장하지 않습니다."

마지막 문장이 중요하다. 지표는 가설을 검증하기 위한 것이지
성과를 주장하기 위한 것이 아니다. 그래서 이 서비스는
표본 수(sample_size)를 항상 함께 돌려주고, 표본이 적으면
해석에 주의가 필요하다고 표시한다.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding import FeatureEvent, OnboardingAnswer, FutureProfile
from app.services.ai.onboarding_catalog import TOTAL_QUESTIONS

logger = logging.getLogger(__name__)

# 이 표본 수 미만이면 지표를 참고용으로만 보도록 표시한다
MIN_SAMPLE = 10


class EventType:
    ONBOARDING_VIEW     = "ONBOARDING_VIEW"
    ONBOARDING_ANSWER   = "ONBOARDING_ANSWER"
    ONBOARDING_COMPLETE = "ONBOARDING_COMPLETE"
    SIMULATE            = "SIMULATE"
    SIMULATE_ADJUST     = "SIMULATE_ADJUST"
    READINESS_VIEW      = "READINESS_VIEW"
    RECAP_VIEW          = "RECAP_VIEW"


@dataclass
class QuestionDropoff:
    question_no : int
    reached     : int     # 이 문항까지 도달한 사용자 수
    answered    : int     # 실제로 답한 사용자 수
    dropoff_rate: Decimal # 도달했으나 답하지 않은 비율


@dataclass
class MetricsResult:
    period_days          : int
    total_users          : int

    # 온보딩
    onboarding_started   : int
    onboarding_completed : int
    completion_rate      : Decimal
    question_dropoff     : list[QuestionDropoff]

    # 시뮬레이터 재사용
    simulator_users      : int
    simulator_calls      : int
    simulator_reuse_rate : Decimal   # 2회 이상 쓴 사용자 비율

    # Recap 열람
    recap_users          : int
    recap_views          : int
    recap_repeat_rate    : Decimal   # 서로 다른 달을 2개 이상 본 사용자 비율

    is_reliable          : bool
    note                 : str
    warnings             : list[str] = field(default_factory=list)


class MetricsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # 기록

    async def record(
        self, user_id: int, event_type: str, ref_key: str | None = None
    ) -> None:
        """
        사용 이벤트를 남긴다.

        지표 수집이 실패해도 사용자 요청은 성공해야 하므로
        예외를 밖으로 던지지 않는다.
        """
        try:
            self.db.add(FeatureEvent(
                user_id=user_id, event_type=event_type, ref_key=ref_key
            ))
            await self.db.flush()
        except Exception as e:
            logger.warning(f"⚠️ 지표 기록 실패 (무시) | {event_type} | {e}")

    # 집계

    async def summary(self, period_days: int = 30) -> MetricsResult:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=period_days)

        total_users = await self._scalar(
            select(func.count(distinct(FeatureEvent.user_id)))
            .where(FeatureEvent.created_at >= since)
        )

        started, completed, dropoff = await self._onboarding(since)
        sim_users, sim_calls, sim_reuse = await self._simulator(since)
        rc_users, rc_views, rc_repeat  = await self._recap(since)

        warnings = []
        if total_users < MIN_SAMPLE:
            warnings.append(
                f"표본이 {total_users}명으로 적어 비율을 일반화하기 어려워요."
            )
        if started == 0:
            warnings.append("온보딩 시작 기록이 없어 완료율은 0으로 표시됩니다.")

        is_reliable = total_users >= MIN_SAMPLE and started > 0

        return MetricsResult(
            period_days          = period_days,
            total_users          = total_users,
            onboarding_started   = started,
            onboarding_completed = completed,
            completion_rate      = self._rate(completed, started),
            question_dropoff     = dropoff,
            simulator_users      = sim_users,
            simulator_calls      = sim_calls,
            simulator_reuse_rate = sim_reuse,
            recap_users          = rc_users,
            recap_views          = rc_views,
            recap_repeat_rate    = rc_repeat,
            is_reliable          = is_reliable,
            note                 = (
                f"최근 {period_days}일 사용 기록을 집계했어요."
                if is_reliable else
                "표본이 적어 참고용으로만 봐주세요."
            ),
            warnings             = warnings,
        )

    # 온보딩

    async def _onboarding(self, since: datetime):
        started = await self._scalar(
            select(func.count(distinct(OnboardingAnswer.user_id)))
            .where(OnboardingAnswer.answered_at >= since)
        )
        completed = await self._scalar(
            select(func.count(distinct(FutureProfile.user_id)))
            .where(
                FutureProfile.is_completed == True,   # noqa: E712
                FutureProfile.completed_at >= since,
            )
        )

        # 문항별 이탈: N번을 답한 사람 중 N+1번으로 넘어가지 않은 비율
        per_q = {}
        result = await self.db.execute(
            select(OnboardingAnswer.question_no,
                   func.count(distinct(OnboardingAnswer.user_id)))
            .where(OnboardingAnswer.answered_at >= since)
            .group_by(OnboardingAnswer.question_no)
        )
        for q_no, cnt in result.all():
            per_q[q_no] = cnt

        dropoff = []
        for no in range(1, TOTAL_QUESTIONS + 1):
            answered = per_q.get(no, 0)
            nxt      = per_q.get(no + 1, 0) if no < TOTAL_QUESTIONS else answered
            lost     = max(answered - nxt, 0)
            dropoff.append(QuestionDropoff(
                question_no  = no,
                reached      = answered,
                answered     = answered,
                dropoff_rate = self._rate(lost, answered),
            ))
        return started, completed, dropoff

    # 시뮬레이터

    async def _simulator(self, since: datetime):
        types = (EventType.SIMULATE, EventType.SIMULATE_ADJUST)

        users = await self._scalar(
            select(func.count(distinct(FeatureEvent.user_id)))
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type.in_(types))
        )
        calls = await self._scalar(
            select(func.count(FeatureEvent.id))
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type.in_(types))
        )

        # 2회 이상 사용한 사용자 수
        sub = (
            select(FeatureEvent.user_id)
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type.in_(types))
            .group_by(FeatureEvent.user_id)
            .having(func.count(FeatureEvent.id) >= 2)
            .subquery()
        )
        repeat = await self._scalar(select(func.count()).select_from(sub))

        return users, calls, self._rate(repeat, users)

    # Recap

    async def _recap(self, since: datetime):
        users = await self._scalar(
            select(func.count(distinct(FeatureEvent.user_id)))
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type == EventType.RECAP_VIEW)
        )
        views = await self._scalar(
            select(func.count(FeatureEvent.id))
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type == EventType.RECAP_VIEW)
        )

        # '다음 달 Recap 열람률' — 서로 다른 달을 2개 이상 본 사용자
        sub = (
            select(FeatureEvent.user_id)
            .where(FeatureEvent.created_at >= since,
                   FeatureEvent.event_type == EventType.RECAP_VIEW)
            .group_by(FeatureEvent.user_id)
            .having(func.count(distinct(FeatureEvent.ref_key)) >= 2)
            .subquery()
        )
        repeat = await self._scalar(select(func.count()).select_from(sub))

        return users, views, self._rate(repeat, users)

    # 유틸

    async def _scalar(self, stmt) -> int:
        result = await self.db.execute(stmt)
        return int(result.scalar() or 0)

    @staticmethod
    def _rate(part: int, whole: int) -> Decimal:
        if whole <= 0:
            return Decimal("0")
        return (Decimal(part) / Decimal(whole)).quantize(Decimal("0.0001"))
