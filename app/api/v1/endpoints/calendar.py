from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.models.spending import (
    AcademicSchedule, AiSpendingProfile,
    University, AcademicCalendar, EventType
)
from app.schemas.spending import AcademicScheduleResponse
from app.kafka.producer import publish_schedule_updated

router = APIRouter(prefix="/calendar", tags=["학사 일정 자동 연동"])


# ── 스키마 ────────────────────────────────────────
class UniversityResponse(BaseModel):
    id        : int
    name      : str
    short_name: str
    region    : Optional[str]
    model_config = {"from_attributes": True}


class CalendarSyncRequest(BaseModel):
    user_id      : int
    university_id: int
    year         : int
    semester     : int   # 1 or 2


class CalendarSyncResponse(BaseModel):
    user_id         : int
    university_name : str
    year            : int
    semester        : int
    synced_count    : int
    schedules       : list[AcademicScheduleResponse]


# ── API ───────────────────────────────────────────

@router.get(
    "/universities",
    response_model=list[UniversityResponse],
    summary="학교 목록 조회",
    description="지원하는 대학교 목록을 반환합니다."
)
async def list_universities(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(University).where(University.is_active == True).order_by(University.name)
    )
    return result.scalars().all()


@router.post(
    "/sync",
    response_model=CalendarSyncResponse,
    summary="학사 일정 자동 동기화",
    description="""
    학교와 학기를 선택하면 해당 학교의 학사 일정을
    사용자의 academic_schedule 에 자동으로 등록합니다.

    이미 등록된 자동 일정(is_auto=True)은 덮어씁니다.
    사용자가 직접 등록한 일정(is_auto=False)은 유지됩니다.
    """
)
async def sync_calendar(
    req: CalendarSyncRequest,
    db : AsyncSession = Depends(get_db)
):
    # ── 학교 확인 ─────────────────────────────────
    univ_result = await db.execute(
        select(University).where(University.id == req.university_id)
    )
    university = univ_result.scalar_one_or_none()
    if not university:
        raise HTTPException(status_code=404, detail="등록되지 않은 학교입니다.")

    # ── 학사 캘린더 조회 ──────────────────────────
    cal_result = await db.execute(
        select(AcademicCalendar).where(
            AcademicCalendar.university_id == req.university_id,
            AcademicCalendar.year          == req.year,
            AcademicCalendar.semester      == req.semester,
        ).order_by(AcademicCalendar.start_date)
    )
    calendars = cal_result.scalars().all()
    if not calendars:
        raise HTTPException(
            status_code=404,
            detail=f"{university.name} {req.year}년 {req.semester}학기 학사 일정이 없습니다."
        )

    # ── 기존 자동 등록 일정 삭제 ──────────────────
    existing = await db.execute(
        select(AcademicSchedule).where(
            AcademicSchedule.user_id   == req.user_id,
            AcademicSchedule.is_auto   == True,
        )
    )
    for old in existing.scalars().all():
        await db.delete(old)

    # ── 새 일정 자동 등록 ─────────────────────────
    new_schedules = []
    for cal in calendars:
        schedule = AcademicSchedule(
            user_id             = req.user_id,
            event_type          = cal.event_type,
            event_name          = cal.event_name,
            start_date          = cal.start_date,
            end_date            = cal.end_date,
            expected_extra_spend= cal.default_extra_spend,
            is_auto             = True,
        )
        db.add(schedule)
        new_schedules.append(schedule)

    await db.flush()

    # ── 프로필에 학교 정보 저장 ───────────────────
    profile_result = await db.execute(
        select(AiSpendingProfile).where(AiSpendingProfile.user_id == req.user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile:
        profile.university_id = req.university_id

    # ── Kafka 이벤트 발행 ─────────────────────────
    await publish_schedule_updated(
        user_id     = req.user_id,
        event_type  = "CALENDAR_SYNC",
        event_buffer= str(sum(s.expected_extra_spend for s in new_schedules)),
    )

    return CalendarSyncResponse(
        user_id         = req.user_id,
        university_name = university.name,
        year            = req.year,
        semester        = req.semester,
        synced_count    = len(new_schedules),
        schedules       = new_schedules,
    )


@router.get(
    "/{user_id}/schedules",
    summary="학사 일정 전체 조회 (자동 + 수동)",
    description="자동 등록된 학사 일정과 사용자가 직접 등록한 일정을 모두 반환합니다."
)
async def get_all_schedules(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AcademicSchedule)
        .where(AcademicSchedule.user_id == user_id)
        .order_by(AcademicSchedule.start_date)
    )
    schedules = result.scalars().all()
    return [
        {
            "id"                  : s.id,
            "event_type"          : s.event_type,
            "event_name"          : s.event_name,
            "start_date"          : str(s.start_date),
            "end_date"            : str(s.end_date),
            "expected_extra_spend": str(s.expected_extra_spend),
            "is_auto"             : s.is_auto,
        }
        for s in schedules
    ]
