import logging
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthUserId, ensure_self, verify_path_user_id
from app.db.session import get_db
from app.models.spending import AcademicSchedule
from app.schemas.spending import (
    SpendingProfileResponse,
    AcademicScheduleCreate, AcademicScheduleResponse,
    SemesterReportResponse,
)
from pydantic import ValidationError

from app.services.ai.spending_service import (
    SpendingAnalysisService, profile_cache_payload,
)
from app.services.ai.report_service import ReportService
from app.redis.client import (
    get_spending_profile_cache, set_spending_profile_cache,
    get_event_buffer_cache, set_event_buffer_cache, delete_event_buffer_cache,
)
from app.kafka.producer import publish_schedule_updated

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/spending",
    tags=["AI 소비 패턴 분석"],
    dependencies=[Depends(verify_path_user_id)],
)


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
        try:
            return SpendingProfileResponse(**cached)
        except ValidationError as e:
            # 예전 모양으로 저장된 항목이 TTL(1800초) 동안 남아 있을 수 있다.
            # 캐시 때문에 500 을 돌려주는 대신 버리고 DB 에서 다시 만든다.
            logger.warning(
                f"⚠️ 소비 프로필 캐시 형식 불일치 → 폐기하고 DB 재조회 "
                f"| user={user_id} | {e.error_count()}개 필드"
            )

    service = SpendingAnalysisService(db)
    profile = await service.get_or_create_profile(user_id)
    # 캐시 모양은 서비스와 같은 함수로 만든다. (profile_cache_payload)
    await set_spending_profile_cache(user_id, profile_cache_payload(profile))
    return profile


@router.post("/schedule", response_model=AcademicScheduleResponse, status_code=201, summary="학사 일정 등록")
async def create_academic_schedule(
    body: AcademicScheduleCreate,
    auth_user_id: AuthUserId,
    db: AsyncSession = Depends(get_db),
):
    # 본문의 user_id 로 남의 달력에 일정을 끼워 넣지 못하게 막는다.
    body.user_id = ensure_self(auth_user_id, body.user_id, where="POST /spending/schedule")

    schedule = AcademicSchedule(**body.model_dump())
    db.add(schedule)
    await db.flush()

    # 일정이 늘면 이벤트 버퍼도 늘어난다. 캐시를 지우지 않으면 TTL(1시간) 동안
    # 방금 등록한 예비비가 빠진 한도가 나간다.
    await delete_event_buffer_cache(body.user_id)

    # Kafka 발행 실패가 학사 일정 등록 자체를 롤백시키지 않도록 분리
    # (발행 실패 시 로그만 남기고 등록은 성공 처리)
    try:
        await publish_schedule_updated(
            user_id     = body.user_id,
            event_type  = body.event_type.value,
            event_buffer= str(body.expected_extra_spend),
        )
    except Exception as e:
        logger.warning(
            f"⚠️ 학사 일정 Kafka 발행 실패 (등록은 정상 완료) "
            f"| user={body.user_id} | {e}"
        )

    return schedule


@router.get(
    "/{user_id}/schedules",
    response_model=list[AcademicScheduleResponse],
    summary="학사 일정 목록",
    description="""
    등록된 학사 일정을 시작일 순서로 반환합니다.

    학사 일정은 지우지 않으면 학기마다 쌓이므로, 몇 해를 쓰면
    한 번에 전부 내려주기엔 양이 많아집니다.
    - year: 그 해에 걸치는 일정만 (학기 화면은 이 값을 넣는 쪽을 권장)
    - limit / offset: 나눠 받기 (limit 기본 200, 최대 500)
    """,
)
async def list_academic_schedules(
    user_id : int,
    year    : int | None = Query(default=None, ge=2020, le=2030, description="해당 연도에 걸치는 일정만"),
    limit   : int        = Query(default=200, ge=1, le=500),
    offset  : int        = Query(default=0, ge=0),
    db      : AsyncSession = Depends(get_db),
):
    query = select(AcademicSchedule).where(AcademicSchedule.user_id == user_id)

    if year is not None:
        # 학기가 해를 넘기는 일정(예: 겨울 계절학기)도 빠지지 않게
        # '그 해 안에 시작한' 이 아니라 '그 해와 겹치는' 으로 잡는다.
        query = query.where(
            AcademicSchedule.start_date <= date(year, 12, 31),
            AcademicSchedule.end_date   >= date(year, 1, 1),
        )

    result = await db.execute(
        query.order_by(AcademicSchedule.start_date).offset(offset).limit(limit)
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
