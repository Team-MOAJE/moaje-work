from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.spending import AcademicSchedule
from app.schemas.spending import (
    SpendingProfileResponse,
    AcademicScheduleCreate, AcademicScheduleResponse,
    SemesterReportResponse,
)
from app.services.ai.spending_service import SpendingAnalysisService
from app.services.ai.report_service import ReportService
from app.redis.client import (
    get_spending_profile_cache, set_spending_profile_cache,
    get_event_buffer_cache, set_event_buffer_cache,
)
from app.kafka.producer import publish_schedule_updated

router = APIRouter(prefix="/spending", tags=["AI 소비 패턴 분석"])


@router.get("/{user_id}/event-buffer", summary="학사 이벤트 버퍼 조회")
async def get_event_buffer(user_id: int, db: AsyncSession = Depends(get_db)):
    cached = await get_event_buffer_cache(user_id)
    if cached:
        return {"user_id": user_id, "event_buffer": cached}

    service = SpendingAnalysisService(db)
    buffer = await service.get_event_buffer(user_id)
    await set_event_buffer_cache(user_id, buffer)

    return {"user_id": user_id, "event_buffer": str(buffer)}


@router.get("/{user_id}/profile", response_model=SpendingProfileResponse, summary="AI 소비 프로필 조회")
async def get_spending_profile(user_id: int, db: AsyncSession = Depends(get_db)):
    cached = await get_spending_profile_cache(user_id)
    if cached:
        return SpendingProfileResponse(**cached)

    service = SpendingAnalysisService(db)
    profile = await service.get_or_create_profile(user_id)
    await set_spending_profile_cache(user_id, {
        "user_id"            : profile.user_id,
        "avg_daily_amount"   : str(profile.avg_daily_amount),
        "peak_spend_hour"    : profile.peak_spend_hour,
        "top_category"       : profile.top_category,
        "risk_score_baseline": str(profile.risk_score_baseline),
        "last_analyzed_at"   : str(profile.last_analyzed_at),
    })
    return profile


@router.post("/schedule", response_model=AcademicScheduleResponse, status_code=201, summary="학사 일정 등록")
async def create_academic_schedule(body: AcademicScheduleCreate, db: AsyncSession = Depends(get_db)):
    schedule = AcademicSchedule(**body.model_dump())
    db.add(schedule)
    await db.flush()

    await publish_schedule_updated(
        user_id     = body.user_id,
        event_type  = body.event_type.value,
        event_buffer= str(body.expected_extra_spend),
    )
    return schedule


@router.get("/{user_id}/schedules", response_model=list[AcademicScheduleResponse], summary="학사 일정 목록")
async def list_academic_schedules(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AcademicSchedule)
        .where(AcademicSchedule.user_id == user_id)
        .order_by(AcademicSchedule.start_date)
    )
    return result.scalars().all()


@router.get(
    "/{user_id}/report",
    response_model=SemesterReportResponse,
    summary="학기 소비 리포트 카드",
    description="학기별 소비 통계, FDS 요약, 학사 이벤트별 지출 분석을 종합한 리포트 카드를 반환합니다.",
)
async def get_semester_report(
    user_id : int          = Path(..., description="사용자 ID"),
    year    : int          = Query(default=2026, ge=2020, le=2030, description="연도"),
    semester: int          = Query(default=1, ge=1, le=2, description="학기 (1 or 2)"),
    db      : AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.get_semester_report(user_id, year, semester)
