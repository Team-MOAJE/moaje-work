"""
FDS 이상거래 탐지 서비스

하이브리드 3단계 구조:
  1단계 (tx < 10)   : Rule-based 100%
  2단계 (tx 10~70)  : Rule + Z-score 개인화 혼합
  3단계 (tx > 70)   : Rule + XGBoost ML 혼합

Rule 1 기준: JPMorgan Chase 3-sigma rule (2025)
  - 정상 분포에서 99.7%는 ±3σ 이내에 포함
  - |Z-score| >= 3σ 이상이면 이상거래로 판정
  - 참고: JPMorgan Chase Engineering Blog (2025)
          FATF Guidance on Risk-Based Approach (2023)

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import kst_now, utc_naive_now
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

# Rule 1: Z-score 3σ 임계값 (JPMorgan Chase 3-sigma rule)
ZSCORE_THRESHOLD = Decimal("3.0")

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

        # 같은 거래를 다시 받으면 이전 판정을 그대로 돌려준다.
        #
        # (user_id, transaction_id) 에 유니크 제약이 걸려 있어 그냥 두면
        # 두 번째 요청이 IntegrityError 로 500 이 된다. 클라이언트가
        # 타임아웃으로 재시도하는 것은 흔한 일인데 그때마다 500 을 주면
        # 재시도할수록 상황이 나빠진다.
        #
        # 다시 채점하지 않는 이유는, 그 사이 프로필이 갱신돼 같은 거래에
        # 다른 점수가 나오면 어느 쪽이 맞는지 알 수 없기 때문이다.
        # 판정은 거래 시점의 것이 정본이다.
        prev = await self._find_verdict(req.user_id, req.transaction_id)
        if prev is not None:
            logger.info(
                f"⏭️ 이미 판정한 거래 — 기존 결과 반환 | user={req.user_id} "
                f"| tx={req.transaction_id} | risk={prev.risk_level}"
            )
            return self._verdict_response(prev)

        profile    = await self._get_profile(req.user_id)
        tx_count   = profile.tx_count if profile else 0
        avg_daily  = profile.avg_daily_amount if profile else Decimal("30000")
        std_daily  = profile.std_daily_amount if profile else Decimal("15000")

        rule_w, personal_w, ml_w = _get_hybrid_weights(tx_count)
        logger.info(
            f"🔀 하이브리드 | user={req.user_id} | tx={tx_count}건 "
            f"| Rule={int(rule_w*100)}% Z={int(personal_w*100)}% ML={int(ml_w*100)}%"
        )

        rule_score,     rule_reasons     = await self._calc_rule_score(req, avg_daily, std_daily)
        personal_score, personal_reasons = self._calc_personal_score(req, avg_daily, std_daily, tx_count)
        ml_score,       ml_reasons       = await self._calc_ml_score(req, avg_daily, std_daily, tx_count)

        final_score = min(
            rule_w * rule_score + personal_w * personal_score + ml_w * ml_score,
            Decimal("1.0")
        )
        # 저장 칼럼이 Numeric(5,4) 이므로 여기서 같은 자리수로 맞춘다.
        #
        # 맞추지 않으면 처음 계산한 요청은 가중치 곱셈 결과를 그대로 받고,
        # 재요청은 DB 에서 반올림된 값을 받아 서로 다른 숫자가 나간다.
        # "같은 거래는 같은 판정" 이라고 해놓고 점수가 달라지는 셈이다.
        final_score = final_score.quantize(Decimal("0.0001"))

        all_reasons = rule_reasons + personal_reasons + ml_reasons
        reason_code = " | ".join(all_reasons) if all_reasons else "NORMAL"
        risk_level  = self._calc_risk_level(final_score)
        is_alerted  = final_score >= RISK_SCORE_ALERT_THRESHOLD

        log = FdsInferenceLog(
            user_id=req.user_id, transaction_id=req.transaction_id,
            amount=req.amount, merchant=req.merchant,
            risk_score=final_score, risk_level=risk_level,
            reason_code=reason_code, is_alerted=is_alerted,
            occurred_at=req.occurred_at,
        )
        # 같은 거래가 '동시에' 들어오면 위의 조회를 둘 다 통과한다.
        # (user_id, transaction_id) 유니크 제약이 둘 중 하나를 거절하는데,
        # 그대로 두면 500 이 된다. 클라이언트 타임아웃 재시도가 겹치면
        # 실제로 일어나는 상황이다.
        #
        # SAVEPOINT 안에서 저장해, 거절당해도 바깥 트랜잭션은 살려 둔다.
        # 진 쪽은 먼저 저장된 판정을 다시 읽어 그대로 돌려준다. 순차
        # 재요청과 같은 답이 나가고, 거래 건수도 한 번만 는다.
        try:
            async with self.db.begin_nested():
                self.db.add(log)
                await self.db.flush()
        except IntegrityError:
            # 이 시점에는 이긴 쪽이 이미 커밋을 마쳤다. InnoDB 가 유니크 키
            # 잠금 때문에 여기까지 기다리게 하기 때문이다. 다만 스냅샷으로는
            # 안 보이므로 잠금 읽기로 가져온다.
            winner = await self._find_verdict(req.user_id, req.transaction_id, fresh=True)
            if winner is None:
                raise
            logger.info(
                f"⏭️ 동시 요청 — 먼저 저장된 판정 반환 | user={req.user_id} "
                f"| tx={req.transaction_id} | risk={winner.risk_level}"
            )
            return self._verdict_response(winner)

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

    async def _find_verdict(self, user_id: int, transaction_id: str, *, fresh: bool = False):
        """
        이미 저장된 판정이 있으면 가져온다.

        fresh=True 면 잠금 읽기(FOR SHARE)로 읽는다.

        MySQL 기본 격리 수준(REPEATABLE READ)에서 일반 SELECT 는 그 트랜잭션이
        처음 읽은 시점의 스냅샷을 계속 본다. 그래서 경합에 진 쪽이 그냥 다시
        읽으면, 이긴 쪽이 방금 커밋한 행이 보이지 않는다. 잠금 읽기는 스냅샷을
        건너뛰고 최신 커밋본을 읽으므로 이때만 쓴다.
        """
        stmt = select(FdsInferenceLog).where(
            FdsInferenceLog.user_id        == user_id,
            FdsInferenceLog.transaction_id == transaction_id,
        ).limit(1)
        if fresh:
            stmt = stmt.with_for_update(read=True)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _verdict_response(self, log: FdsInferenceLog) -> FdsDetectResponse:
        """저장된 판정을 그대로 응답으로 옮긴다. 다시 채점하지 않는다."""
        return FdsDetectResponse(
            user_id        = log.user_id,
            transaction_id = log.transaction_id,
            risk_score     = log.risk_score,
            risk_level     = log.risk_level,
            reason_code    = log.reason_code or "NORMAL",
            is_alerted     = log.is_alerted,
            message        = self._generate_message(log.risk_level, log.reason_code or ""),
        )

    async def _calc_rule_score(self, req, avg_daily, std_daily):
        """
        Rule-based 이상거래 탐지

        Rule 1: Z-score 3σ 기반 이상 금액 탐지
          - JPMorgan Chase 3-sigma rule 적용 (2025)
          - 정상 분포의 99.7%는 ±3σ 이내
          - 3σ 초과 시 이상거래로 판정 (상위 0.3%)
          - std_daily 없는 신규 유저는 평균 3배 fallback

        Rule 2: 이상 시간대 (새벽 02~05시)
        Rule 3: 단시간 반복 거래 (10분내 3회)
        """
        score, reasons = Decimal("0.0"), []

        # ── Rule 1: Z-score 3σ 기반 이상 금액 ────────
        if std_daily > 0:
            # 데이터 충분 → JPMorgan·FATF 국제 표준 Z-score 3σ
            z_score = abs(req.amount - avg_daily) / std_daily
            if z_score >= ZSCORE_THRESHOLD:
                # Z-score 크기에 비례해 가중치 부여 (최대 0.5)
                s = min(
                    Decimal("0.5"),
                    Decimal("0.3") + (z_score - ZSCORE_THRESHOLD) / ZSCORE_THRESHOLD * Decimal("0.2")
                )
                score += s
                reasons.append(
                    f"RULE_ABNORMAL_AMOUNT(z={float(z_score):.1f}σ | {int(req.amount):,}원)"
                )
                logger.info(
                    f"🚨 Rule1 이상금액(3σ) | user={req.user_id} "
                    f"| z={float(z_score):.2f}σ | amount={int(req.amount):,}원"
                )
        elif avg_daily > 0 and req.amount >= avg_daily * Decimal("3.0"):
            # 신규 유저 fallback: 표준편차 없을 시 평균 3배 기준
            s = min(Decimal("0.5"), req.amount / (avg_daily * Decimal("3.0")) * Decimal("0.3"))
            score += s
            reasons.append(
                f"RULE_ABNORMAL_AMOUNT_FALLBACK(avg:{int(avg_daily):,}원 대비 {int(req.amount):,}원)"
            )
            logger.info(f"🚨 Rule1 이상금액(fallback) | user={req.user_id}")

        # ── Rule 2: 이상 시간대 (새벽 02~05시) ────────
        if NIGHT_HOUR_START <= req.hour < NIGHT_HOUR_END:
            score += Decimal("0.3")
            reasons.append(f"RULE_ABNORMAL_TIME(hour:{req.hour}시)")
            logger.info(f"🚨 Rule2 이상시간대 | user={req.user_id} | hour={req.hour}")

        # ── Rule 3: 단시간 반복 거래 (10분내 3회) ──────
        recent = await self._get_recent_tx_count(req.user_id)
        if recent >= RAPID_REPEAT_COUNT:
            score += Decimal("0.4")
            reasons.append(f"RULE_RAPID_REPEAT({recent}건/{RAPID_REPEAT_MINUTES}분)")
            logger.info(f"🚨 Rule3 반복거래 | user={req.user_id} | count={recent}")

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
                kst_now().weekday(),   # 시각 피처(req.hour)가 한국시간이므로 요일도 맞춘다
                0, 30
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
            e.released_at = utc_naive_now()
        return True

    async def _get_profile(self, user_id: int):
        result = await self.db.execute(
            select(AiSpendingProfile).where(AiSpendingProfile.user_id==user_id)
        )
        return result.scalar_one_or_none()

    async def _get_recent_tx_count(self, user_id: int) -> int:
        cutoff = utc_naive_now() - timedelta(minutes=RAPID_REPEAT_MINUTES)
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
