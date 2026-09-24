"""
Future Simulator API

미래 필요 자금을 역산하고 현재 자산과의 Gap 을 계산한다.
온보딩 답변(Future Profile)을 계산의 기본 가정으로 쓰고,
사용자가 값을 직접 넣으면 그 값을 우선한다.
"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.simulator import (
    SimulateRequest, SimulateResponse,
    AdjustRequest, AdjustResponse,
    TargetBreakdown, CashflowBreakdown, BaselineInfo,
)
from app.grpc.asset_client import AssetClient
from app.services.ai.cost_baseline import all_baselines
from app.services.ai.onboarding_service import OnboardingService
from app.services.ai.simulator_service import SimulatorService, SimulationInput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulator", tags=["Future Simulator"])


async def _build_input(
    db: AsyncSession, user_id: int, body: SimulateRequest
) -> tuple[SimulationInput, str | None, str]:
    """
    온보딩 답변과 사용자 입력을 합쳐 계산 입력을 만든다.

    현재 자산은 사용자가 넣으면 그 값을 쓰고, 넣지 않으면 Asset 에
    조회한다. Asset 이 응답하지 않아도 계산을 멈추지 않고 0 으로 이어가되
    그 사실을 출처로 표시한다.

    반환: (계산 입력, Q8 지키고 싶은 소비, 자산 출처)
    """
    profile = await OnboardingService(db).get_profile(user_id)

    leisure_style = None
    must_keep     = None
    if profile:
        must_keep = profile.must_keep_category
        # 주말 활동(Q5)은 프로필 컬럼에 없으므로 답변에서 직접 찾는다
        answers = await OnboardingService(db).get_answers(user_id)
        for a in answers:
            if a.question_no == 5:
                leisure_style = a.answer_code
                break

    # 현재 자산 확보
    if body.current_asset is not None:
        current_asset, asset_source = body.current_asset, "INPUT"
    else:
        fetched = await AssetClient().get_current_balance(user_id)
        if fetched is None:
            current_asset, asset_source = Decimal("0"), "UNAVAILABLE"
        else:
            current_asset, asset_source = fetched, "ASSET_SERVICE"

    inp = SimulationInput(
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
    return inp, must_keep, asset_source


@router.get(
    "/baselines",
    response_model=list[BaselineInfo],
    summary="비용 기준값 목록",
    description=(
        "계산에 쓰이는 모든 기준값과 그 출처·기준일·적용 범위를 반환한다. "
        "kind 가 ASSUMPTION 인 값은 공표 통계가 아닌 모아제의 가정값이므로 "
        "사용자 입력으로 대체하는 것을 권한다."
    ),
)
async def list_baselines():
    return [BaselineInfo(**b.to_dict()) for b in all_baselines()]


@router.post(
    "/{user_id}/simulate",
    response_model=SimulateResponse,
    summary="미래 자금 시뮬레이션",
    description=(
        "미래에 필요한 자금을 역산하고 현재 자산과의 Gap 을 계산한다.\n\n"
        "- 값을 넣지 않은 항목은 온보딩 답변과 기준값으로 계산한다\n"
        "- 가정값을 쓴 항목은 assumed_keys 에 표시된다\n"
        "- 월 저축 가능액이 0 이하면 months_to_goal 은 null 이다"
    ),
)
async def simulate(
    body: SimulateRequest,
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    inp, must_keep, asset_source = await _build_input(db, user_id, body)

    service = SimulatorService()
    result  = service.build_result(inp)
    advice  = service.build_advice(result, must_keep)

    return SimulateResponse(
        user_id = user_id,
        target = TargetBreakdown(
            deposit        = str(result.deposit),
            move_in_cost   = str(result.move_in_cost),
            emergency_fund = str(result.emergency_fund),
            total          = str(result.target_amount),
        ),
        cashflow = CashflowBreakdown(
            income  = str(result.monthly_income),
            housing = str(result.monthly_housing),
            living  = str(result.monthly_living),
            leisure = str(result.monthly_leisure),
            expense = str(result.monthly_expense),
            savable = str(result.monthly_savable),
        ),
        current_asset  = str(result.current_asset),
        gap            = str(result.gap),
        months_to_goal = result.months_to_goal,
        progress_rate  = str(result.progress_rate),
        asset_source   = asset_source,
        assumed_keys   = result.assumed_keys,
        baselines      = [BaselineInfo(**b) for b in result.baselines_used],
        advice         = advice,
    )


@router.post(
    "/{user_id}/adjust",
    response_model=AdjustResponse,
    summary="조건부 시뮬레이션",
    description=(
        "월 지출을 조정했을 때 달성 시점이 어떻게 달라지는지 계산한다.\n\n"
        "본 시뮬레이션과 동일한 산식을 재사용하므로, "
        "'몇 개월 단축' 문구는 실제 계산 결과다."
    ),
)
async def adjust(
    body: AdjustRequest,
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    inp, _, _ = await _build_input(db, user_id, body)

    result = SimulatorService().simulate_adjustment(inp, body.monthly_delta)
    return AdjustResponse(user_id=user_id, **result)
