"""
Money Recap API

Asset 이 발행하는 월별·카테고리 집계를 바탕으로 한 달 소비를 돌아본다.
집계가 아직 도착하지 않은 달은 404 를 돌려준다.
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.recap import (
    RecapResponse, RecapMonthsResponse,
    CategoryShareResponse, RecapCoverageResponse,
)
from app.services.ai.onboarding_service import OnboardingService
from app.services.ai.recap_service import RecapService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recap", tags=["Money Recap"])

_YM = re.compile(r"^\d{4}-\d{2}$")


@router.get(
    "/{user_id}/months",
    response_model=RecapMonthsResponse,
    summary="Recap 이 있는 월 목록",
)
async def list_months(
    user_id: int = Path(..., description="사용자 ID"),
    limit: int   = Query(default=12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
):
    months = await RecapService(db).list_months(user_id, limit)
    return RecapMonthsResponse(user_id=user_id, months=months)


@router.get(
    "/{user_id}/{year_month}",
    response_model=RecapResponse,
    summary="월별 Money Recap",
    description=(
        "한 달 소비를 돌아보고 소비패턴 별명을 붙인다.\n\n"
        "- 데이터 원천은 Asset 의 월별·카테고리 집계다\n"
        "- 거래가 적거나 미분류 비중이 크면 별명을 붙이지 않고 "
        "coverage 로 그 사실을 알린다\n"
        "- 온보딩에서 지키고 싶다고 답한 소비는 줄이라고 말하지 않는다"
    ),
)
async def get_recap(
    user_id: int    = Path(..., description="사용자 ID"),
    year_month: str = Path(..., description="조회할 달 (예: 2026-09)"),
    db: AsyncSession = Depends(get_db),
):
    if not _YM.match(year_month):
        raise HTTPException(
            status_code=400,
            detail="year_month 형식이 올바르지 않습니다. 예: 2026-09",
        )

    profile   = await OnboardingService(db).get_profile(user_id)
    must_keep = profile.must_keep_category if profile else None

    result = await RecapService(db).get_recap(user_id, year_month, must_keep)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"{year_month} 의 집계 데이터가 아직 없습니다.",
        )

    return RecapResponse(
        user_id        = user_id,
        year_month     = result.year_month,
        revision       = result.revision,
        total_income   = str(result.total_income),
        total_expense  = str(result.total_expense),
        total_transfer = str(result.total_transfer),
        net_saving     = str(result.net_saving),
        categories     = [
            CategoryShareResponse(
                code=c.code, label=c.label, amount=str(c.amount),
                tx_count=c.tx_count, share=str(c.share),
            )
            for c in result.categories
        ],
        top_category   = (
            CategoryShareResponse(
                code=result.top_category.code,
                label=result.top_category.label,
                amount=str(result.top_category.amount),
                tx_count=result.top_category.tx_count,
                share=str(result.top_category.share),
            ) if result.top_category else None
        ),
        nickname        = result.nickname,
        nickname_reason = result.nickname_reason,
        coverage        = RecapCoverageResponse(
            total_tx           = result.coverage.total_tx,
            classified_tx      = result.coverage.classified_tx,
            unclassified_share = str(result.coverage.unclassified_share),
            is_reliable        = result.coverage.is_reliable,
            note               = result.coverage.note,
        ),
        insights        = result.insights,
    )
