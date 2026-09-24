"""
사회인 준비도 API

목표 대비 준비 상태를 하나의 백분율로 보여준다.
점수를 저장하지 않고 조회할 때마다 계산하므로, 목표나 자산이 바뀌면
다음 조회에서 자동으로 반영된다.
"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.grpc.asset_client import AssetClient
from app.schemas.readiness import (
    ReadinessRequest, ReadinessResponse, ReadinessItemResponse,
)
from app.schemas.simulator import SimulateRequest
from app.services.ai.onboarding_service import OnboardingService
from app.services.ai.metrics_service import MetricsService, EventType
from app.services.ai.readiness_service import ReadinessService
from app.services.ai.simulator_service import SimulatorService, SimulationInput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/readiness", tags=["사회인 준비도"])


@router.post(
    "/{user_id}",
    response_model=ReadinessResponse,
    summary="사회인 준비도 조회",
    description=(
        "목표 대비 준비 상태를 백분율로 계산한다.\n\n"
        "**배점**\n"
        "- 목표 자금 충족률 50 — 목표 금액 대비 현재 자산\n"
        "- 소비 관리 습관 25 — 최근 90일 Daily Limit 준수 비율\n"
        "- 미래 계획 구체성 15 — 온보딩 답변 진행도\n"
        "- 안전한 금융 습관 10 — 최근 90일 FDS 탐지 이력\n\n"
        "데이터가 없어 평가할 수 없는 항목은 0점 처리하지 않고 배점에서 "
        "제외한 뒤 남은 항목으로 환산한다. 제외된 항목은 excluded 에 표시된다.\n\n"
        "점수는 저장하지 않고 매번 계산하므로 목표가 바뀌면 바로 반영된다."
    ),
)
async def get_readiness(
    body: ReadinessRequest,
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    onboarding = OnboardingService(db)
    profile    = await onboarding.get_profile(user_id)
    answers    = await onboarding.get_answers(user_id)

    # 주말 활동(Q5)은 프로필 컬럼에 없어 답변에서 찾는다
    leisure_style = next(
        (a.answer_code for a in answers if a.question_no == 5), None
    )

    # 현재 자산: 입력이 없으면 Asset 에 조회한다.
    # Asset 이 응답하지 않아도 준비도 계산은 멈추지 않는다.
    if body.current_asset is not None:
        current_asset, asset_source = body.current_asset, "INPUT"
    else:
        fetched = await AssetClient().get_current_balance(user_id)
        if fetched is None:
            current_asset, asset_source = Decimal("0"), "UNAVAILABLE"
        else:
            current_asset, asset_source = fetched, "ASSET_SERVICE"

    # 목표 자금 충족률은 Future Simulator 와 같은 계산을 쓴다
    sim_input = SimulationInput(
        user_id         = user_id,
        housing_type    = profile.housing_type if profile else None,
        work_env        = profile.work_env if profile else None,
        leisure_style   = leisure_style,
        monthly_income  = body.monthly_income,
        monthly_housing = body.monthly_housing,
        monthly_living  = body.monthly_living,
        monthly_leisure = body.monthly_leisure,
        deposit         = body.deposit,
        move_in_cost    = body.move_in_cost,
        current_asset   = current_asset,
    )
    sim = SimulatorService().build_result(sim_input)

    result = await ReadinessService(db).calculate(
        user_id        = user_id,
        goal_progress  = sim.progress_rate,
        answered_count = len(answers),
    )

    await MetricsService(db).record(user_id, EventType.READINESS_VIEW)

    return ReadinessResponse(
        user_id     = user_id,
        percent     = result.percent,
        level       = result.level,
        level_label = result.level_label,
        items       = [
            ReadinessItemResponse(
                key=i.key, label=i.label, weight=i.weight,
                score=i.score, detail=i.detail,
            )
            for i in result.items
        ],
        asset_source= asset_source,
        evaluated   = result.evaluated,
        excluded    = result.excluded,
        message     = result.message,
        disclaimer  = result.disclaimer,
    )
