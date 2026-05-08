from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.fds import FdsBlacklist, FdsInferenceLog, BlacklistReason
from app.schemas.fds import (
    FdsDetectRequest, FdsDetectResponse,
    BlacklistCreateRequest, BlacklistResponse,
)
from app.services.fds.detector import FdsDetector

router = APIRouter(prefix="/fds", tags=["FDS 이상거래 탐지"])


@router.post(
    "/detect",
    response_model=FdsDetectResponse,
    summary="이상거래 탐지",
    description="""
    거래 정보를 받아 Rule-based AI 스코어링으로 이상 여부를 판단합니다.

    탐지 규칙:
    - 이상 금액: 유저 평균 대비 3배 이상
    - 이상 시간대: 새벽 02:00 ~ 05:00
    - 단시간 반복: 10분 내 3회 이상

    risk_score 0.7 이상 시 work.fds.alert Kafka 이벤트 자동 발행
    """,
)
async def detect_fraud(req: FdsDetectRequest, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    return await detector.detect(req)


@router.get(
    "/{user_id}/blacklist",
    summary="블랙리스트 조회",
    description="유저가 현재 블랙리스트에 등록되어 있는지 확인합니다.",
)
async def check_blacklist(user_id: int, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    is_blocked = await detector.get_blacklist_status(user_id)
    return {
        "user_id"    : user_id,
        "is_blocked" : is_blocked,
        "message"    : "🚫 차단된 사용자입니다." if is_blocked else "✅ 정상 사용자입니다."
    }


@router.post(
    "/blacklist",
    response_model=BlacklistResponse,
    status_code=201,
    summary="블랙리스트 등록",
    description="이상거래 확정 시 유저를 블랙리스트에 등록합니다.",
)
async def register_blacklist(body: BlacklistCreateRequest, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    entry = await detector.register_blacklist(
        user_id     = body.user_id,
        reason      = body.reason,
        description = body.description,
    )
    return entry


@router.delete(
    "/{user_id}/blacklist",
    summary="블랙리스트 해제",
    description="오탐 확인 시 블랙리스트를 해제합니다.",
)
async def release_blacklist(user_id: int, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    released = await detector.release_blacklist(user_id)
    if not released:
        raise HTTPException(status_code=404, detail="블랙리스트에 등록된 사용자가 없습니다.")
    return {"user_id": user_id, "message": "✅ 블랙리스트가 해제되었습니다."}


@router.get(
    "/{user_id}/logs",
    summary="FDS 탐지 로그 조회",
    description="유저의 최근 FDS 탐지 이력을 조회합니다.",
)
async def get_fds_logs(user_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FdsInferenceLog)
        .where(FdsInferenceLog.user_id == user_id)
        .order_by(FdsInferenceLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id"            : log.id,
            "transaction_id": log.transaction_id,
            "amount"        : str(log.amount),
            "risk_score"    : str(log.risk_score),
            "risk_level"    : log.risk_level,
            "reason_code"   : log.reason_code,
            "is_alerted"    : log.is_alerted,
            "created_at"    : str(log.created_at),
        }
        for log in logs
    ]
