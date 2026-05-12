"""
FDS 이상거래 탐지 서비스

하이브리드 3단계 구조:
  1단계 (tx < 10)   : Rule-based 100%
  2단계 (tx 10~70)  : Rule + Z-score 개인화 혼합
  3단계 (tx > 70)   : Rule + XGBoost ML 혼합

XGBoost 피처:
  amount_zscore, amount_ratio, hour, is_night,
  tx_count, recent_tx_10min, day_of_week,
  has_event_7days, days_to_event
"""
import logging
import os
import pickle
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import numpy as np
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fds import FdsInferenceLog, FdsBlacklist, RiskLevel, BlacklistReason
from app.models.spending import AiSpendingProfile
from app.schemas.fds import FdsDetectRequest, FdsDetectResponse

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────
RISK_SCORE_ALERT_THRESHOLD = Decimal("0.7")
NIGHT_HOUR_START           = 2
NIGHT_HOUR_END             = 5
RAPID_REPEAT_MINUTES       = 10
RAPID_REPEAT_COUNT         = 3

# ── 하이브리드 단계 (rule_w, personal_w, ml_w) ────
HYBRID_STAGES = [
    (0,   10,  Decimal("1.0"), Decimal("0.0"), Decimal("0.0")),
    (11,  30,  Decimal("0.7"), Decimal("0.3"), Decimal("0.0")),
    (31,  70,  Decimal("0.3"), Decimal("0.4"), Decimal("0.3")),
    (71, None, Decimal("0.1"), Decimal("0.1"), Decimal("0.8")),
]

# ── XGBoost 모델 로드 ─────────────────────────────
_ml_model    = None
_ml_features = None

def _load_ml_model():
    global _ml_model, _ml_features
    if _ml_model is not None:
        return True
    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds_model.pkl")
    if not os.path.exists(model_path):
        logger.warning(f"⚠️  ML 모델 파일 없음: {model_path}")
        return False
    try:
        with open(model_path, "rb") as f:
            data = pickle.load(f)
        _ml_model    = data["model"]
        _ml_features = data["features"]
        logger.info(f"✅ XGBoost FDS 모델 로드 완료 | AUC: {data.get('auc', 0):.4f}")
        return True
    except Exception as e:
        logger.error(f"❌ ML 모델 로드 실패: {e}")
        return False

_load_ml_model()


def _get_hybrid_weights(tx_count: int):
    for min_tx, max_tx, rule_w, personal_w, ml_w in HYBRID_STAGES:
        if max_tx is None or tx_count <= max_tx:
            return rule_w, personal_w, ml_w
    return Decimal("0.1"), Decimal("0.1"), Decimal("0.8")


class FdsDetector:
    """
    하이브리드 적응형 FDS 탐지 엔진

    tx_count 기반 자동 전환:
      0~10건   → Rule 100%
      11~30건  → Rule 70% + Z-score 30%
      31~70건  → Rule 30% + Z-score 40% + ML 30%
      70건 이상 → Rule 10% + Z-score 10% + ML 80%
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect(self, req: FdsDetectRequest) -> FdsDetectResponse:

        profile    = await self._get_profile(req.user_id)
        tx_count   = profile.tx_count if profile else 0
        avg_daily  = profile.avg_daily_amount if profile else Decimal("30000")
        std_daily  = profile.std_daily_amount if profile else Decimal("15000")

        rule_w, personal_w, ml_w = _get_hybrid_weights(tx_count)
        logger.info(
            f"🔀 하이브리드 | user={req.user_id} | tx={tx_count}건 "
            f"| Rule={int(rule_w*100)}% Z={int(personal_w*100)}% ML={int(ml_w*100)}%"
        )

        rule_score,     rule_reasons     = await self._calc_rule_score(req, avg_daily)
        personal_score, personal_reasons = self._calc_personal_score(req, avg_daily, std_daily, tx_count)
        ml_score,       ml_reasons       = await self._calc_ml_score(req, avg_daily, std_daily, tx_count)

        final_score = min(
            rule_w * rule_score + personal_w * personal_score + ml_w * ml_score,
            Decimal("1.0")
        )

        all_reasons = rule_reasons + personal_reasons + ml_reasons
        reason_code = " | ".join(all_reasons) if all_reasons else "NORMAL"
        risk_level  = self._calc_risk_level(final_score)
        is_alerted  = final_score >= RISK_SCORE_ALERT_THRESHOLD

        log = FdsInferenceLog(
            user_id=req.user_id, transaction_id=req.transaction_id,
            amount=req.amount, merchant=req.merchant,
            risk_score=final_score, risk_level=risk_level,
            reason_code=reason_code, is_alerted=is_alerted,
        )
        self.db.add(log)
        await self.db.flush()
        await self._increment_tx_count(req.user_id)

        if is_alerted:
            from app.kafka.producer import publish_fds_alert
            await publish_fds_alert(
                user_id=req.user_id,
                risk_level=risk_level.value,
                reason_code=reason_code,
                amount=str(req.amount),
                merchant=req.merchant or "",
            )
            logger.warning(f"🚨 FDS Alert | user={req.user_id} | score={final_score:.2f}")

        return FdsDetectResponse(
            user_id=req.user_id, transaction_id=req.transaction_id,
            risk_score=final_score, risk_level=risk_level,
            reason_code=reason_code, is_alerted=is_alerted,
            message=self._generate_message(risk_level, reason_code),
        )

    async def _calc_rule_score(self, req, avg_daily):
        score, reasons = Decimal("0.0"), []
        if avg_daily > 0 and req.amount >= avg_daily * Decimal("3.0"):
            s = min(Decimal("0.5"), req.amount / (avg_daily * Decimal("3.0")) * Decimal("0.3"))
            score += s
            reasons.append(f"RULE_ABNORMAL_AMOUNT(avg:{int(avg_daily):,}원 대비 {int(req.amount):,}원)")
        if NIGHT_HOUR_START <= req.hour < NIGHT_HOUR_END:
            score += Decimal("0.3")
            reasons.append(f"RULE_ABNORMAL_TIME(hour:{req.hour}시)")
        recent = await self._get_recent_tx_count(req.user_id)
        if recent >= RAPID_REPEAT_COUNT:
            score += Decimal("0.4")
            reasons.append(f"RULE_RAPID_REPEAT({recent}건/{RAPID_REPEAT_MINUTES}분)")
        return min(score, Decimal("1.0")), reasons

    def _calc_personal_score(self, req, avg_daily, std_daily, tx_count):
        score, reasons = Decimal("0.0"), []
        if tx_count < 10 or std_daily <= 0:
            return score, reasons
        try:
            z = abs(req.amount - avg_daily) / std_daily
            if   z >= Decimal("4.0"): score += Decimal("0.7"); reasons.append(f"PERSONAL_EXTREME(z={float(z):.1f}σ)")
            elif z >= Decimal("3.0"): score += Decimal("0.5"); reasons.append(f"PERSONAL_HIGH(z={float(z):.1f}σ)")
            elif z >= Decimal("2.0"): score += Decimal("0.3"); reasons.append(f"PERSONAL_UNUSUAL(z={float(z):.1f}σ)")
        except Exception as e:
            logger.warning(f"Z-score 계산 실패: {e}")
        return min(score, Decimal("1.0")), reasons

    async def _calc_ml_score(self, req, avg_daily, std_daily, tx_count):
        score, reasons = Decimal("0.0"), []
        if not _ml_model:
            return score, reasons
        try:
            recent        = await self._get_recent_tx_count(req.user_id)
            amount_zscore = float(abs(req.amount - avg_daily) / std_daily) if std_daily > 0 else 0.0
            amount_ratio  = float(req.amount / avg_daily) if avg_daily > 0 else 1.0
            is_night      = 1 if NIGHT_HOUR_START <= req.hour < NIGHT_HOUR_END else 0

            features = np.array([[
                amount_zscore, amount_ratio, req.hour, is_night,
                tx_count, recent,
                datetime.now(timezone.utc).weekday(),
                0, 30  # has_event_7days, days_to_event (기본값)
            ]])

            prob  = float(_ml_model.predict_proba(features)[0][1])
            score = Decimal(str(round(prob, 4)))

            if prob >= 0.7:
                reasons.append(f"ML_HIGH_RISK(prob={prob:.2f})")
            elif prob >= 0.4:
                reasons.append(f"ML_MEDIUM_RISK(prob={prob:.2f})")

            logger.info(f"🤖 ML 스코어 | user={req.user_id} | prob={prob:.3f}")
        except Exception as e:
            logger.error(f"❌ ML 스코어 계산 실패: {e}")
        return min(score, Decimal("1.0")), reasons

    async def get_blacklist_status(self, user_id: int) -> bool:
        result = await self.db.execute(
            select(FdsBlacklist).where(FdsBlacklist.user_id==user_id, FdsBlacklist.is_active==True)
        )
        return result.scalar_one_or_none() is not None

    async def register_blacklist(self, user_id, reason, description=""):
        entry = FdsBlacklist(user_id=user_id, reason=reason, description=description, is_active=True)
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def release_blacklist(self, user_id: int) -> bool:
        result = await self.db.execute(
            select(FdsBlacklist).where(FdsBlacklist.user_id==user_id, FdsBlacklist.is_active==True)
        )
        entries = result.scalars().all()
        if not entries:
            return False
        for e in entries:
            e.is_active   = False
            e.released_at = datetime.now(timezone.utc)
        return True

    async def _get_profile(self, user_id: int):
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id==user_id)
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
        profile = await self._get_profile(user_id)
        if profile:
            profile.tx_count = (profile.tx_count or 0) + 1

    @staticmethod
    def _calc_risk_level(score: Decimal) -> RiskLevel:
        if score >= Decimal("0.7"):   return RiskLevel.HIGH
        elif score >= Decimal("0.4"): return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _generate_message(risk_level: RiskLevel, reason_code: str) -> str:
        if risk_level == RiskLevel.HIGH:
            return f"🚨 이상거래가 탐지되었습니다. 즉시 확인이 필요합니다. ({reason_code})"
        elif risk_level == RiskLevel.MEDIUM:
            return f"⚠️ 주의가 필요한 거래 패턴이 감지되었습니다. ({reason_code})"
        return "✅ 정상 거래입니다."
