from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.spending import AcademicSchedule
from app.schemas.spending import (
    DailyLimitRequest, DailyLimitResponse,
    SpendingProfileResponse,
    AcademicScheduleCreate, AcademicScheduleResponse,
)
from app.services.ai.spending_service import SpendingAnalysisService

router = APIRouter(prefix="/spending", tags=["AI 소비 패턴 분석"])


@router.post("/daily-limit", response_model=DailyLimitResponse, summary="일일 가용 생활비 계산")
async def calculate_daily_limit(req: DailyLimitRequest, db: AsyncSession = Depends(get_db)):
    return await SpendingAnalysisService(db).calculate_daily_limit(req)


@router.get("/{user_id}/event-buffer", summary="학사 이벤트 버퍼 조회")
async def get_event_buffer(user_id: int, db: AsyncSession = Depends(get_db)):
    buffer = await SpendingAnalysisService(db).get_event_buffer(user_id)
    return {"user_id": user_id, "event_buffer": str(buffer)}


@router.get("/{user_id}/profile", response_model=SpendingProfileResponse, summary="AI 소비 프로필 조회")
async def get_spending_profile(user_id: int, db: AsyncSession = Depends(get_db)):
    return await SpendingAnalysisService(db).get_or_create_profile(user_id)


@router.post("/schedule", response_model=AcademicScheduleResponse, status_code=201, summary="학사 일정 등록")
async def create_academic_schedule(body: AcademicScheduleCreate, db: AsyncSession = Depends(get_db)):
    schedule = AcademicSchedule(**body.model_dump())
    db.add(schedule)
    await db.flush()
    return schedule


@router.get("/{user_id}/schedules", response_model=list[AcademicScheduleResponse], summary="학사 일정 목록")
async def list_academic_schedules(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AcademicSchedule)
        .where(AcademicSchedule.user_id == user_id)
        .order_by(AcademicSchedule.start_date)
    )
    return result.scalars().all()
