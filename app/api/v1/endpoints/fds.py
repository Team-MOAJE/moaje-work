from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta

from app.api.deps import AuthUserId, ensure_self, require_operator, verify_path_user_id
from app.db.session import get_db
from app.models.fds import FdsBlacklist, FdsInferenceLog, FdsAlertLog, BlacklistReason, RiskLevel
from app.schemas.fds import (
    FdsDetectRequest, FdsDetectResponse,
    BlacklistCreateRequest, BlacklistResponse,
)
from app.services.fds.detector import FdsDetector
from app.services.ai.report_service import SAFETY_MAX, PENALTY_HIGH, PENALTY_MEDIUM

# 본인 데이터용 — 경로의 {user_id} 가 인증 사용자와 같은지 라우터 단위로 확인한다.
router = APIRouter(
    prefix="/fds",
    tags=["FDS 이상거래 탐지"],
    dependencies=[Depends(verify_path_user_id)],
)

# 운영용 — 블랙리스트는 제재 조치이므로 본인이 등록·해제할 수 있으면 안 된다.
# Gateway 헤더 대신 내부 토큰으로만 통과시킨다.
ops_router = APIRouter(
    prefix="/fds",
    tags=["FDS 운영 (내부 전용)"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/detect",
    response_model=FdsDetectResponse,
    summary="이상거래 탐지",
    description="""
    거래 정보를 받아 하이브리드 AI 스코어링으로 이상 여부를 판단합니다.

    탐지 방식 (거래 건수 기반 자동 전환):
    - 0~10건:   Rule-based 100%
    - 11~30건:  Rule 70% + Z-score 개인화 30%
    - 31~70건:  Rule 30% + Z-score 40% + XGBoost ML 30%
    - 70건 이상: Rule 10% + Z-score 10% + XGBoost ML 80%

    risk_score 0.7 이상 시 work.fds.alert Kafka 이벤트 자동 발행
    """,
)
async def detect_fraud(
    req: FdsDetectRequest,
    auth_user_id: AuthUserId,
    db: AsyncSession = Depends(get_db),
):
    # 본문의 user_id 를 그대로 믿지 않고 인증 사용자와 같은지 확인한다.
    # 통과하면 검증된 ID로 덮어써서 이후 탐지·저장이 인증 주체로만 이뤄지게 한다.
    req.user_id = ensure_self(auth_user_id, req.user_id, where="POST /fds/detect")

    detector = FdsDetector(db)
    return await detector.detect(req)


@router.get("/{user_id}/blacklist", summary="블랙리스트 조회")
async def check_blacklist(user_id: int, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    is_blocked = await detector.get_blacklist_status(user_id)
    return {
        "user_id"   : user_id,
        "is_blocked": is_blocked,
        "message"   : "🚫 차단된 사용자입니다." if is_blocked else "✅ 정상 사용자입니다."
    }


@ops_router.post("/blacklist", response_model=BlacklistResponse, status_code=201, summary="블랙리스트 등록 (내부 전용)")
async def register_blacklist(body: BlacklistCreateRequest, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    return await detector.register_blacklist(
        user_id=body.user_id, reason=body.reason, description=body.description,
    )


@ops_router.delete("/{user_id}/blacklist", summary="블랙리스트 해제 (내부 전용)")
async def release_blacklist(user_id: int, db: AsyncSession = Depends(get_db)):
    detector = FdsDetector(db)
    released = await detector.release_blacklist(user_id)
    if not released:
        raise HTTPException(status_code=404, detail="블랙리스트에 등록된 사용자가 없습니다.")
    return {"user_id": user_id, "message": "✅ 블랙리스트가 해제되었습니다."}


@router.get("/{user_id}/logs", summary="FDS 탐지 로그 조회")
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


# ── ✅ 이상거래 알림 API ───────────────────────────

@router.get(
    "/{user_id}/alerts",
    summary="이상거래 알림 조회",
    description="""
    FDS가 탐지한 이상거래 알림 목록을 반환합니다.
    HIGH 위험도 거래 탐지 시 자동 생성됩니다.
    프론트엔드에서 앱 알림 표시에 활용합니다.

    - unread=true: 미확인 알림만 조회
    """,
)
async def get_fds_alerts(
    user_id : int,
    limit   : int  = 20,
    unread  : bool = False,
    db      : AsyncSession = Depends(get_db)
):
    query = select(FdsAlertLog).where(FdsAlertLog.user_id == user_id)
    if unread:
        query = query.where(FdsAlertLog.is_confirmed == False)
    query = query.order_by(FdsAlertLog.created_at.desc()).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()

    return {
        "user_id": user_id,
        "total"  : len(alerts),
        "unread" : sum(1 for a in alerts if not a.is_confirmed),
        "alerts" : [
            {
                "id"            : a.id,
                "transaction_id": a.transaction_id,
                "amount"        : str(a.amount),
                "merchant"      : a.merchant,
                "risk_level"    : a.risk_level,
                "reason_code"   : a.reason_code,
                "message"       : a.message,
                "is_confirmed"  : a.is_confirmed,
                "confirmed_at"  : str(a.confirmed_at) if a.confirmed_at else None,
                "created_at"    : str(a.created_at),
            }
            for a in alerts
        ]
    }


@router.patch(
    "/{user_id}/alerts/{alert_id}/confirm",
    summary="이상거래 알림 확인 처리",
    description="사용자가 이상거래 알림을 확인했을 때 호출합니다.",
)
async def confirm_fds_alert(
    user_id  : int,
    alert_id : int,
    db       : AsyncSession = Depends(get_db)
):
    # alert_id 는 개별 데이터 ID이므로 소유자까지 함께 확인한다.
    # user_id 는 라우터 가드를 통과한 인증 사용자 ID와 같음이 보장되므로,
    # 이 조건이 곧 '내 알림인지' 검사다. 타인의 alert_id 는 404 가 된다.
    result = await db.execute(
        select(FdsAlertLog).where(
            FdsAlertLog.id      == alert_id,
            FdsAlertLog.user_id == user_id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")

    alert.is_confirmed = True
    alert.confirmed_at = datetime.now(timezone.utc)
    await db.flush()

    return {
        "alert_id"    : alert_id,
        "is_confirmed": True,
        "confirmed_at": str(alert.confirmed_at),
        "message"     : "✅ 알림이 확인 처리됐습니다."
    }


# ── ✅ 소비 안전도 점수 API ───────────────────────

@router.get(
    "/{user_id}/safety-score",
    summary="소비 안전도 점수 조회",
    description="""
    최근 30일간의 FDS 탐지 이력을 기반으로
    유저의 소비 안전도 점수(0~100)를 반환합니다.

    점수 산출 (리포트 카드와 동일한 감점 기준, 40점 만점을 100점으로 환산):
    - HIGH   탐지 1건 → -8점
    - MEDIUM 탐지 1건 → -3점

    등급:
    - A (90~100): 🟢 매우 안전
    - B (75~89):  🟡 양호
    - C (55~74):  🟠 주의
    - D (0~54):   🔴 위험
    """,
)
async def get_safety_score(user_id: int, db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=30)

    result = await db.execute(
        select(FdsInferenceLog).where(
            FdsInferenceLog.user_id    == user_id,
            FdsInferenceLog.created_at >= since,
        )
    )
    logs = result.scalars().all()

    total        = len(logs)
    high_count   = sum(1 for l in logs if l.risk_level == RiskLevel.HIGH)
    medium_count = sum(1 for l in logs if l.risk_level == RiskLevel.MEDIUM)

    # 리포트 카드(report_service)와 동일한 감점 기준 사용
    # 안전도 만점 40점 → HIGH -8점, MEDIUM -3점
    raw_score = max(
        0, SAFETY_MAX - (high_count * PENALTY_HIGH) - (medium_count * PENALTY_MEDIUM)
    )
    # 단독 API는 100점 스케일로 환산해 노출
    score = round(raw_score / SAFETY_MAX * 100)

    if score >= 90:   grade, emoji = "A", "🟢"
    elif score >= 75: grade, emoji = "B", "🟡"
    elif score >= 55: grade, emoji = "C", "🟠"
    else:             grade, emoji = "D", "🔴"

    return {
        "user_id"     : user_id,
        "safety_score": score,
        "grade"       : grade,
        "emoji"       : emoji,
        "total_tx"    : total,
        "high_count"  : high_count,
        "medium_count": medium_count,
        "period_days" : 30,
        "message"     : f"{emoji} 소비 안전도 {score}점 ({grade}등급)",
    }
