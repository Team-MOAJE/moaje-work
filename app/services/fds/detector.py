"""
FDS 이상거래 탐지 서비스

MVP: Rule-based 탐지 엔진
추후: XGBoost 기반 ML 모델로 업그레이드 예정
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fds import FdsInferenceLog, FdsBlacklist, RiskLevel, BlacklistReason
from app.models.spending import AiSpendingProfile
from app.schemas.fds import FdsDetectRequest, FdsDetectResponse

logger = logging.getLogger(__name__)

# ── Rule-based 임계값 설정 ────────────────────────
RISK_SCORE_ALERT_THRESHOLD = Decimal("0.7")   # 이 점수 이상이면 Kafka alert 발행
AMOUNT_MULTIPLIER          = Decimal("3.0")   # 평균 대비 N배 이상이면 이상 거래
NIGHT_HOUR_START           = 2                # 새벽 이상 시간대 시작 (02시)
NIGHT_HOUR_END             = 5                # 새벽 이상 시간대 종료 (05시)
RAPID_REPEAT_MINUTES       = 10              # 단시간 반복 거래 기준 (분)
RAPID_REPEAT_COUNT         = 3               # 단시간 반복 거래 기준 (건수)


class FdsDetector:
    """
    Rule-based FDS 탐지 엔진

    탐지 규칙:
    1. 이상 금액: 유저 평균 일별 지출 대비 3배 이상
    2. 이상 시간대: 새벽 02:00 ~ 05:00 거래
    3. 단시간 반복: 10분 내 3회 이상 거래

    TODO: ML 업그레이드 시
    - XGBoost 모델 로드 및 predict() 호출로 교체
    - 학사 일정 변수 결합 (졸업논문 모델과 연동)
    - 피처: amount, hour, avg_daily_amount, event_type, days_until_exam 등
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect(self, req: FdsDetectRequest) -> FdsDetectResponse:
        """거래 이상 여부 탐지 및 risk_score 산출"""

        risk_score  = Decimal("0.0")
        reason_list = []

        # ── Rule 1: 이상 금액 탐지 ───────────────────
        avg_daily = await self._get_avg_daily_amount(req.user_id)
        if avg_daily > 0 and req.amount >= avg_daily * AMOUNT_MULTIPLIER:
            score = min(Decimal("0.5"), req.amount / (avg_daily * AMOUNT_MULTIPLIER) * Decimal("0.3"))
            risk_score  += score
            reason_list.append(f"ABNORMAL_AMOUNT(avg:{int(avg_daily):,}원 대비 {int(req.amount):,}원)")
            logger.info(f"🚨 이상 금액 탐지 | user={req.user_id} | amount={req.amount} | avg={avg_daily}")

        # ── Rule 2: 이상 시간대 탐지 ─────────────────
        if NIGHT_HOUR_START <= req.hour < NIGHT_HOUR_END:
            risk_score  += Decimal("0.3")
            reason_list.append(f"ABNORMAL_TIME(hour:{req.hour}시)")
            logger.info(f"🚨 이상 시간대 탐지 | user={req.user_id} | hour={req.hour}")

        # ── Rule 3: 단시간 반복 거래 탐지 ────────────
        recent_count = await self._get_recent_tx_count(req.user_id)
        if recent_count >= RAPID_REPEAT_COUNT:
            risk_score  += Decimal("0.4")
            reason_list.append(f"RAPID_REPEAT({recent_count}건/{RAPID_REPEAT_MINUTES}분)")
            logger.info(f"🚨 반복 거래 탐지 | user={req.user_id} | count={recent_count}")

        # ── Risk Level 산출 ───────────────────────────
        risk_score = min(risk_score, Decimal("1.0"))
        risk_level = self._calc_risk_level(risk_score)
        reason_code = " | ".join(reason_list) if reason_list else "NORMAL"
        is_alerted = risk_score >= RISK_SCORE_ALERT_THRESHOLD

        # ── DB 저장 ───────────────────────────────────
        log = FdsInferenceLog(
            user_id        = req.user_id,
            transaction_id = req.transaction_id,
            amount         = req.amount,
            merchant       = req.merchant,
            risk_score     = risk_score,
            risk_level     = risk_level,
            reason_code    = reason_code,
            is_alerted     = is_alerted,
        )
        self.db.add(log)
        await self.db.flush()

        # ── Kafka alert 발행 (HIGH 위험도만) ──────────
        if is_alerted:
            from app.kafka.producer import publish_fds_alert
            await publish_fds_alert(
                user_id     = req.user_id,
                risk_level  = risk_level.value,
                reason_code = reason_code,
            )
            logger.warning(f"🚨 FDS Alert 발행 | user={req.user_id} | score={risk_score} | reason={reason_code}")

        message = self._generate_message(risk_level, reason_code)

        return FdsDetectResponse(
            user_id        = req.user_id,
            transaction_id = req.transaction_id,
            risk_score     = risk_score,
            risk_level     = risk_level,
            reason_code    = reason_code,
            is_alerted     = is_alerted,
            message        = message,
        )

    async def get_blacklist_status(self, user_id: int) -> bool:
        """유저가 블랙리스트에 있는지 확인"""
        result = await self.db.execute(
            select(FdsBlacklist).where(
                FdsBlacklist.user_id  == user_id,
                FdsBlacklist.is_active == True,
            )
        )
        return result.scalar_one_or_none() is not None

    async def register_blacklist(
        self, user_id: int, reason: BlacklistReason, description: str = ""
    ) -> FdsBlacklist:
        """블랙리스트 등록"""
        entry = FdsBlacklist(
            user_id     = user_id,
            reason      = reason,
            description = description,
            is_active   = True,
        )
        self.db.add(entry)
        await self.db.flush()
        logger.warning(f"🚫 블랙리스트 등록 | user={user_id} | reason={reason}")
        return entry

    async def release_blacklist(self, user_id: int) -> bool:
        """블랙리스트 해제"""
        result = await self.db.execute(
            select(FdsBlacklist).where(
                FdsBlacklist.user_id  == user_id,
                FdsBlacklist.is_active == True,
            )
        )
        entries = result.scalars().all()
        if not entries:
            return False
        for entry in entries:
            entry.is_active   = False
            entry.released_at = datetime.now(timezone.utc)
        logger.info(f"✅ 블랙리스트 해제 | user={user_id}")
        return True

    # ── Private 헬퍼 ─────────────────────────────────

    async def _get_avg_daily_amount(self, user_id: int) -> Decimal:
        """유저 평균 일별 지출액 조회"""
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        return profile.avg_daily_amount if profile else Decimal("30000")

    async def _get_recent_tx_count(self, user_id: int) -> int:
        """최근 N분 내 탐지 로그 건수 조회"""
        from sqlalchemy import func as sqlfunc
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=RAPID_REPEAT_MINUTES)
        result = await self.db.execute(
            select(sqlfunc.count(FdsInferenceLog.id)).where(
                FdsInferenceLog.user_id    == user_id,
                FdsInferenceLog.created_at >= cutoff,
            )
        )
        return result.scalar() or 0

    @staticmethod
    def _calc_risk_level(score: Decimal) -> RiskLevel:
        if score >= Decimal("0.7"):
            return RiskLevel.HIGH
        elif score >= Decimal("0.4"):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _generate_message(risk_level: RiskLevel, reason_code: str) -> str:
        if risk_level == RiskLevel.HIGH:
            return f"🚨 이상거래가 탐지되었습니다. 즉시 확인이 필요합니다. ({reason_code})"
        elif risk_level == RiskLevel.MEDIUM:
            return f"⚠️ 주의가 필요한 거래 패턴이 감지되었습니다. ({reason_code})"
        return "✅ 정상 거래입니다."
