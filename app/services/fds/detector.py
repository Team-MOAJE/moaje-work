"""
FDS 이상거래 탐지 서비스

MVP:  Rule-based 탐지 엔진
적응형: 거래 건수 기반 하이브리드 스코어링 (Rule + 개인화)
추후:  XGBoost 기반 ML 모델로 업그레이드 예정
"""
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fds import FdsInferenceLog, FdsBlacklist, RiskLevel, BlacklistReason
from app.models.spending import AiSpendingProfile
from app.schemas.fds import FdsDetectRequest, FdsDetectResponse

logger = logging.getLogger(__name__)

# ── Rule-based 임계값 ─────────────────────────────
RISK_SCORE_ALERT_THRESHOLD = Decimal("0.7")
NIGHT_HOUR_START           = 2
NIGHT_HOUR_END             = 5
RAPID_REPEAT_MINUTES       = 10
RAPID_REPEAT_COUNT         = 3

# ── 하이브리드 가중치 단계 (거래 건수 기반) ──────────
# tx_count → (rule_weight, personal_weight)
HYBRID_STAGES = [
    (0,  10,  Decimal("1.0"), Decimal("0.0")),   # 0~10건:   Rule 100%
    (11, 30,  Decimal("0.7"), Decimal("0.3")),   # 11~30건:  Rule 70% + 개인화 30%
    (31, 70,  Decimal("0.3"), Decimal("0.7")),   # 31~70건:  Rule 30% + 개인화 70%
    (71, None,Decimal("0.1"), Decimal("0.9")),   # 70건 이상: Rule 10% + 개인화 90%
]


def _get_hybrid_weights(tx_count: int):
    """거래 건수에 따라 Rule/개인화 가중치 반환"""
    for min_tx, max_tx, rule_w, personal_w in HYBRID_STAGES:
        if max_tx is None or tx_count <= max_tx:
            return rule_w, personal_w
    return Decimal("0.1"), Decimal("0.9")


class FdsDetector:
    """
    하이브리드 적응형 FDS 탐지 엔진

    신규 유저 → Rule-based 100%로 즉시 보호
    거래 쌓일수록 → 개인화 비중 자동 증가
    충분한 데이터 → 개인 패턴 기반 탐지 90%

    거래 건수 기준:
        0~10건:   Rule 100% + 개인화 0%
        11~30건:  Rule 70%  + 개인화 30%
        31~70건:  Rule 30%  + 개인화 70%
        70건 이상: Rule 10%  + 개인화 90%

    개인화 스코어 계산:
        Z-score 기반 → (amount - avg) / std
        카테고리별 평균 대비 이상 여부

    TODO: ML 업그레이드 시
        - XGBoost 모델 로드 및 predict() 호출로 교체
        - 학사 일정 변수 결합 (졸업논문 모델과 연동)
        - 피처: amount, hour, avg_daily_amount,
                std_daily_amount, event_type, days_until_exam 등
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect(self, req: FdsDetectRequest) -> FdsDetectResponse:
        """거래 이상 여부 탐지 — 하이브리드 스코어링"""

        # ── 프로필 조회 ───────────────────────────────
        profile = await self._get_profile(req.user_id)
        tx_count = profile.tx_count if profile else 0
        avg_daily = profile.avg_daily_amount if profile else Decimal("30000")
        std_daily = profile.std_daily_amount if profile else Decimal("15000")

        # ── 하이브리드 가중치 산출 ─────────────────────
        rule_w, personal_w = _get_hybrid_weights(tx_count)
        logger.info(
            f"🔀 하이브리드 가중치 | user={req.user_id} | tx={tx_count}건 "
            f"| Rule={rule_w*100:.0f}% | 개인화={personal_w*100:.0f}%"
        )

        # ── Rule-based 스코어 산출 ────────────────────
        rule_score, rule_reasons = await self._calc_rule_score(req, avg_daily)

        # ── 개인화 스코어 산출 (Z-score 기반) ──────────
        personal_score, personal_reasons = self._calc_personal_score(
            req, avg_daily, std_daily, tx_count
        )

        # ── 최종 스코어 합산 ──────────────────────────
        final_score = rule_w * rule_score + personal_w * personal_score
        final_score = min(final_score, Decimal("1.0"))

        all_reasons = rule_reasons + personal_reasons
        reason_code = " | ".join(all_reasons) if all_reasons else "NORMAL"
        risk_level  = self._calc_risk_level(final_score)
        is_alerted  = final_score >= RISK_SCORE_ALERT_THRESHOLD

        # ── DB 저장 ───────────────────────────────────
        log = FdsInferenceLog(
            user_id        = req.user_id,
            transaction_id = req.transaction_id,
            amount         = req.amount,
            merchant       = req.merchant,
            risk_score     = final_score,
            risk_level     = risk_level,
            reason_code    = reason_code,
            is_alerted     = is_alerted,
        )
        self.db.add(log)
        await self.db.flush()

        # ── 프로필 tx_count 업데이트 ──────────────────
        await self._increment_tx_count(req.user_id)

        # ── Kafka alert 발행 (HIGH만) ─────────────────
        if is_alerted:
            from app.kafka.producer import publish_fds_alert
            await publish_fds_alert(
                user_id     = req.user_id,
                risk_level  = risk_level.value,
                reason_code = reason_code,
            )
            logger.warning(
                f"🚨 FDS Alert | user={req.user_id} | score={final_score} | {reason_code}"
            )

        return FdsDetectResponse(
            user_id        = req.user_id,
            transaction_id = req.transaction_id,
            risk_score     = final_score,
            risk_level     = risk_level,
            reason_code    = reason_code,
            is_alerted     = is_alerted,
            message        = self._generate_message(risk_level, reason_code),
        )

    # ── Rule-based 스코어 ─────────────────────────────

    async def _calc_rule_score(
        self, req: FdsDetectRequest, avg_daily: Decimal
    ) -> tuple[Decimal, list[str]]:
        """Rule 1~3 적용하여 스코어 산출"""
        score   = Decimal("0.0")
        reasons = []

        # Rule 1: 이상 금액 (평균 대비 3배)
        if avg_daily > 0 and req.amount >= avg_daily * Decimal("3.0"):
            s = min(Decimal("0.5"), req.amount / (avg_daily * Decimal("3.0")) * Decimal("0.3"))
            score += s
            reasons.append(f"RULE_ABNORMAL_AMOUNT(avg:{int(avg_daily):,}원 대비 {int(req.amount):,}원)")
            logger.info(f"🚨 Rule1 이상금액 | user={req.user_id}")

        # Rule 2: 이상 시간대 (새벽 02~05시)
        if NIGHT_HOUR_START <= req.hour < NIGHT_HOUR_END:
            score += Decimal("0.3")
            reasons.append(f"RULE_ABNORMAL_TIME(hour:{req.hour}시)")
            logger.info(f"🚨 Rule2 이상시간대 | user={req.user_id}")

        # Rule 3: 단시간 반복 거래 (10분 내 3회)
        recent_count = await self._get_recent_tx_count(req.user_id)
        if recent_count >= RAPID_REPEAT_COUNT:
            score += Decimal("0.4")
            reasons.append(f"RULE_RAPID_REPEAT({recent_count}건/{RAPID_REPEAT_MINUTES}분)")
            logger.info(f"🚨 Rule3 반복거래 | user={req.user_id}")

        return min(score, Decimal("1.0")), reasons

    # ── 개인화 스코어 (Z-score 기반) ──────────────────

    def _calc_personal_score(
        self, req: FdsDetectRequest,
        avg_daily: Decimal, std_daily: Decimal,
        tx_count: int
    ) -> tuple[Decimal, list[str]]:
        """
        Z-score 기반 개인화 스코어 산출
        (amount - avg) / std → 표준편차 몇 배 벗어났는지
        """
        score   = Decimal("0.0")
        reasons = []

        # 데이터 부족 시 개인화 스코어 0 반환
        if tx_count < 10 or std_daily <= 0:
            return score, reasons

        try:
            z_score = abs(req.amount - avg_daily) / std_daily

            # Z-score 2σ 이상 → 이상 수준별 가중치 부여
            if z_score >= Decimal("4.0"):
                score += Decimal("0.7")
                reasons.append(f"PERSONAL_EXTREME_AMOUNT(z={float(z_score):.1f}σ)")
            elif z_score >= Decimal("3.0"):
                score += Decimal("0.5")
                reasons.append(f"PERSONAL_HIGH_AMOUNT(z={float(z_score):.1f}σ)")
            elif z_score >= Decimal("2.0"):
                score += Decimal("0.3")
                reasons.append(f"PERSONAL_UNUSUAL_AMOUNT(z={float(z_score):.1f}σ)")

            if score > 0:
                logger.info(
                    f"📊 개인화 스코어 | user={req.user_id} "
                    f"| z={float(z_score):.2f}σ | score={score}"
                )
        except Exception as e:
            logger.warning(f"개인화 스코어 계산 실패 | {e}")

        return min(score, Decimal("1.0")), reasons

    # ── 블랙리스트 관리 ───────────────────────────────

    async def get_blacklist_status(self, user_id: int) -> bool:
        result = await self.db.execute(
            select(FdsBlacklist).where(
                FdsBlacklist.user_id   == user_id,
                FdsBlacklist.is_active == True,
            )
        )
        return result.scalar_one_or_none() is not None

    async def register_blacklist(
        self, user_id: int, reason: BlacklistReason, description: str = ""
    ) -> FdsBlacklist:
        entry = FdsBlacklist(
            user_id=user_id, reason=reason,
            description=description, is_active=True,
        )
        self.db.add(entry)
        await self.db.flush()
        logger.warning(f"🚫 블랙리스트 등록 | user={user_id} | reason={reason}")
        return entry

    async def release_blacklist(self, user_id: int) -> bool:
        result = await self.db.execute(
            select(FdsBlacklist).where(
                FdsBlacklist.user_id   == user_id,
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

    # ── Private 헬퍼 ──────────────────────────────────

    async def _get_profile(self, user_id: int):
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_recent_tx_count(self, user_id: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=RAPID_REPEAT_MINUTES)
        result = await self.db.execute(
            select(sqlfunc.count(FdsInferenceLog.id)).where(
                FdsInferenceLog.user_id    == user_id,
                FdsInferenceLog.created_at >= cutoff,
            )
        )
        return result.scalar() or 0

    async def _increment_tx_count(self, user_id: int):
        """거래 발생 시 tx_count 자동 증가"""
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if profile:
            profile.tx_count = (profile.tx_count or 0) + 1
            logger.info(f"📈 tx_count 업데이트 | user={user_id} | count={profile.tx_count}")

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
