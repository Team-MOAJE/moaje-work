"""
재방문 검증 지표 API

기획안 6절 '재방문 검증' 항목을 측정한다.
문항별 이탈률, 온보딩 완료율, 시뮬레이터 재사용률, Recap 열람률.

개발·운영 확인용 지표다. 개인 식별 정보는 담지 않으며
사용자 수와 비율만 집계한다.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.metrics import MetricsResponse, QuestionDropoffResponse
from app.services.ai.metrics_service import MetricsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["재방문 검증 지표"])


@router.get(
    "/retention",
    response_model=MetricsResponse,
    summary="재방문 검증 지표",
    description=(
        "기획안 6절이 확인하라고 한 네 지표를 집계한다.\n\n"
        "- 문항별 이탈률\n"
        "- 온보딩 완료율\n"
        "- 시뮬레이터 재사용률 (2회 이상 사용)\n"
        "- Recap 열람률 (서로 다른 달 2개 이상)\n\n"
        "표본이 10명 미만이면 `is_reliable` 이 false 가 된다. "
        "지표는 가설을 확인하기 위한 것이며, 더미 데이터만으로 "
        "효과를 주장하지 않는다."
    ),
)
async def get_retention_metrics(
    period_days: int = Query(default=30, ge=1, le=365, description="집계 기간"),
    db: AsyncSession = Depends(get_db),
):
    r = await MetricsService(db).summary(period_days)

    return MetricsResponse(
        period_days          = r.period_days,
        total_users          = r.total_users,
        onboarding_started   = r.onboarding_started,
        onboarding_completed = r.onboarding_completed,
        completion_rate      = str(r.completion_rate),
        question_dropoff     = [
            QuestionDropoffResponse(
                question_no=d.question_no,
                answered=d.answered,
                dropoff_rate=str(d.dropoff_rate),
            )
            for d in r.question_dropoff
        ],
        simulator_users      = r.simulator_users,
        simulator_calls      = r.simulator_calls,
        simulator_reuse_rate = str(r.simulator_reuse_rate),
        recap_users          = r.recap_users,
        recap_views          = r.recap_views,
        recap_repeat_rate    = str(r.recap_repeat_rate),
        is_reliable          = r.is_reliable,
        note                 = r.note,
        warnings             = r.warnings,
    )
