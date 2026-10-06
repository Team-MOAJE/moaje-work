from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import date
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.api.deps import AuthUserId, ensure_self, verify_path_user_id
from app.db.session import get_db
from app.models.spending import (
    AcademicSchedule, AiSpendingProfile,
    University, AcademicCalendar, EventType
)
from app.schemas.spending import AcademicScheduleResponse
from app.kafka.producer import publish_schedule_updated
from app.redis.client import delete_event_buffer_cache

# /universities 처럼 {user_id} 가 없는 공용 조회는 401 만 거치고 소유자 확인은 건너뛴다.
router = APIRouter(
    prefix="/calendar",
    tags=["학사 일정 자동 연동"],
    dependencies=[Depends(verify_path_user_id)],
)


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
    auth_user_id: AuthUserId,
    db : AsyncSession = Depends(get_db)
):
    # 동기화는 기존 자동 일정을 삭제하고 다시 쓰는 작업이라,
    # 본문의 user_id 를 검증하지 않으면 타인의 일정을 지울 수 있다.
    req.user_id = ensure_self(auth_user_id, req.user_id, where="POST /calendar/sync")

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

    # ── 이 학기 자동 등록 일정만 삭제 ─────────────
    #
    # 전에는 is_auto=True 를 전부 지웠다. 그러면 2학기를 동기화하는 순간
    # 1학기 자동 일정이 같이 날아가고, 지난 학기 리포트는 "이벤트 없음"이 된다.
    # 학기를 거듭해 쓰는 서비스에서는 처음 한 번만 멀쩡하고 그 뒤로 계속
    # 과거가 사라지는 셈이다.
    #
    # academic_schedule 에는 학기 칸이 없으므로, 지금 넣으려는 일정이
    # 덮는 기간만큼만 범위를 잡아 그 안의 자동 일정을 치운다.
    sync_start = min(cal.start_date for cal in calendars)
    sync_end   = max(cal.end_date   for cal in calendars)

    await db.execute(
        delete(AcademicSchedule).where(
            AcademicSchedule.user_id    == req.user_id,
            AcademicSchedule.is_auto    == True,   # noqa: E712
            AcademicSchedule.start_date >= sync_start,
            AcademicSchedule.start_date <= sync_end,
        )
    )

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

    # 이 학기 자동 일정을 갈아엎었으므로 버퍼 캐시도 버린다.
    await delete_event_buffer_cache(req.user_id)

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
    description="""
    자동 등록된 학사 일정과 사용자가 직접 등록한 일정을 모두 반환합니다.

    학기마다 쌓이므로 한 번에 내려주는 양에 상한을 둡니다.
    - year: 그 해에 걸치는 일정만
    - limit / offset: 나눠 받기 (limit 기본 200, 최대 500)
    """,
)
async def get_all_schedules(
    user_id : int,
    year    : int | None = Query(default=None, ge=2020, le=2030, description="해당 연도에 걸치는 일정만"),
    limit   : int        = Query(default=200, ge=1, le=500),
    offset  : int        = Query(default=0, ge=0),
    db      : AsyncSession = Depends(get_db),
):
    query = select(AcademicSchedule).where(AcademicSchedule.user_id == user_id)

    if year is not None:
        # 해를 넘기는 일정도 빠지지 않게 '그 해와 겹치는' 으로 잡는다.
        query = query.where(
            AcademicSchedule.start_date <= date(year, 12, 31),
            AcademicSchedule.end_date   >= date(year, 1, 1),
        )

    result = await db.execute(
        query.order_by(AcademicSchedule.start_date).offset(offset).limit(limit)
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
