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
from app.redis.client import (
    get_daily_limit_cache, set_daily_limit_cache,
    get_spending_profile_cache, set_spending_profile_cache,
    get_event_buffer_cache, set_event_buffer_cache,
)
from app.kafka.producer import publish_spending_analyzed, publish_schedule_updated

router = APIRouter(prefix="/spending", tags=["AI 소비 패턴 분석"])


@router.post("/daily-limit", response_model=DailyLimitResponse, summary="일일 가용 생활비 계산")
async def calculate_daily_limit(req: DailyLimitRequest, db: AsyncSession = Depends(get_db)):
    # 1. Redis 캐시 확인
    cached = await get_daily_limit_cache(req.user_id)
    if cached:
        return DailyLimitResponse(**cached)

    # 2. 캐시 미스 → DB 계산
    service = SpendingAnalysisService(db)
    result = await service.calculate_daily_limit(req)

    # 3. Redis 캐시 저장
    await set_daily_limit_cache(req.user_id, result.model_dump(mode="json"))

    # 4. Kafka 이벤트 발행
    await publish_spending_analyzed(
        user_id=req.user_id,
        daily_limit=str(result.daily_limit),
        advice=result.advice,
    )

    return result


@router.get("/{user_id}/event-buffer", summary="학사 이벤트 버퍼 조회")
async def get_event_buffer(user_id: int, db: AsyncSession = Depends(get_db)):
    # 1. Redis 캐시 확인
    cached = await get_event_buffer_cache(user_id)
    if cached:
        return {"user_id": user_id, "event_buffer": cached}

    # 2. 캐시 미스 → DB 조회
    service = SpendingAnalysisService(db)
    buffer = await service.get_event_buffer(user_id)

    # 3. Redis 캐시 저장
    await set_event_buffer_cache(user_id, buffer)

    return {"user_id": user_id, "event_buffer": str(buffer)}


@router.get("/{user_id}/profile", response_model=SpendingProfileResponse, summary="AI 소비 프로필 조회")
async def get_spending_profile(user_id: int, db: AsyncSession = Depends(get_db)):
    # 1. Redis 캐시 확인
    cached = await get_spending_profile_cache(user_id)
    if cached:
        return SpendingProfileResponse(**cached)

    # 2. 캐시 미스 → DB 조회
    service = SpendingAnalysisService(db)
    profile = await service.get_or_create_profile(user_id)

    # 3. Redis 캐시 저장
    await set_spending_profile_cache(user_id, {
        "user_id": profile.user_id,
        "avg_daily_amount": str(profile.avg_daily_amount),
        "peak_spend_hour": profile.peak_spend_hour,
        "top_category": profile.top_category,
        "risk_score_baseline": str(profile.risk_score_baseline),
        "last_analyzed_at": str(profile.last_analyzed_at),
    })

    return profile


@router.post("/schedule", response_model=AcademicScheduleResponse, status_code=201, summary="학사 일정 등록")
async def create_academic_schedule(body: AcademicScheduleCreate, db: AsyncSession = Depends(get_db)):
    schedule = AcademicSchedule(**body.model_dump())
    db.add(schedule)
    await db.flush()

    # Kafka 이벤트 발행
    await publish_schedule_updated(
        user_id=body.user_id,
        event_type=body.event_type.value,
        event_buffer=str(body.expected_extra_spend),
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
