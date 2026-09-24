"""
사회인 준비도 — 목표 대비 준비 상태를 하나의 지표로 보여준다

기획안 2절 ③: "사회인 준비도 64%" 같은 직관적 레벨링.

기획안 6절이 정하라고 한 사항과 그 답:

  Q. 어떤 항목을 어떤 가중치로 계산하는가
  A. 아래 4항목. 목표 자금 충족률에 가장 큰 비중을 둔다.
     '준비됐다'는 말에 가장 가까운 것이 목표 금액을 얼마나 모았는가이기
     때문이다. 나머지는 그 속도를 지탱하는 요소로 본다.

  Q. 목표가 바뀌면 어떻게 재계산하는가
  A. 점수를 저장하지 않고 조회할 때마다 계산한다. 목표 금액은 온보딩
     답변과 입력값에서 매번 도출되므로, 목표가 바뀌면 다음 조회에서
     자동으로 반영된다.

  Q. 개인 평가로 오해되지 않게 하려면
  A. 응답에 disclaimer 를 싣는다. 이 수치는 앱 내부 준비 지표이며
     개인의 성숙도나 신용도에 대한 평가가 아니다.

배점

  | 항목             | 배점 | 무엇을 보는가                     |
  |------------------|------|-----------------------------------|
  | 목표 자금 충족률 |  50  | 목표 금액 대비 현재 자산          |
  | 소비 관리 습관   |  25  | Daily Limit 을 지킨 비율          |
  | 미래 계획 구체성 |  15  | 온보딩 답변 진행도                |
  | 안전한 금융 습관 |  10  | FDS 이상거래 탐지 이력            |

데이터가 없어 평가할 수 없는 항목은 0점 처리하지 않고 배점에서 빼고
남은 항목으로 환산한다. 거래가 없는 신규 사용자가 '소비 관리 0점'을
받는 것은 습관이 나쁜 것이 아니라 아직 쓰지 않은 것이기 때문이다.
어느 항목이 평가됐고 어느 항목이 빠졌는지는 응답에 표시한다.

다만 목표 자금 충족률과 미래 계획은 항상 평가한다. 아무것도 하지 않은
상태의 준비도는 0% 가 맞는 해석이고, 이를 제외하면 준비도가 실제보다
높게 나오기 때문이다.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fds import FdsInferenceLog, RiskLevel
from app.models.spending import AiAnalysisLog
from app.services.ai.onboarding_catalog import TOTAL_QUESTIONS

logger = logging.getLogger(__name__)

# 배점
W_GOAL     = 50   # 목표 자금 충족률
W_SPENDING = 25   # 소비 관리 습관
W_PLAN     = 15   # 미래 계획 구체성
W_SAFETY   = 10   # 안전한 금융 습관

# 소비·안전도 평가에 쓸 최근 기간
LOOKBACK_DAYS = 90

# 안전도 감점 (리포트 카드와 같은 기준)
PENALTY_HIGH   = 8
PENALTY_MEDIUM = 3

DISCLAIMER = (
    "이 수치는 목표 대비 준비 상태를 보여주는 앱 내부 지표입니다. "
    "개인의 성숙도나 신용도에 대한 평가가 아닙니다."
)


@dataclass
class ReadinessItem:
    key        : str
    label      : str
    weight     : int
    score      : int | None    # 평가 불가면 None
    detail     : str


@dataclass
class ReadinessResult:
    percent      : int
    level        : int
    level_label  : str
    items        : list[ReadinessItem]
    evaluated    : list[str]
    excluded     : list[str]
    message      : str
    disclaimer   : str = DISCLAIMER


class ReadinessService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def calculate(
        self,
        user_id: int,
        goal_progress: Decimal,      # Future Simulator 의 progress_rate (0~1)
        answered_count: int,         # 온보딩 답변 수
    ) -> ReadinessResult:
        items: list[ReadinessItem] = []

        # ① 목표 자금 충족률 (항상 평가)
        goal_score = int(
            (min(goal_progress, Decimal("1")) * W_GOAL).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        items.append(ReadinessItem(
            key="goal", label="목표 자금 충족률", weight=W_GOAL, score=goal_score,
            detail=f"목표 금액의 {float(min(goal_progress, Decimal('1')))*100:.1f}% 를 모았어요.",
        ))

        # ② 소비 관리 습관 (기록 있을 때만)
        spend_score, spend_detail = await self._spending_score(user_id)
        items.append(ReadinessItem(
            key="spending", label="소비 관리 습관", weight=W_SPENDING,
            score=spend_score, detail=spend_detail,
        ))

        # ③ 미래 계획 구체성 (항상 평가)
        plan_score = int(
            (Decimal(min(answered_count, TOTAL_QUESTIONS)) / TOTAL_QUESTIONS * W_PLAN)
            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        items.append(ReadinessItem(
            key="plan", label="미래 계획 구체성", weight=W_PLAN, score=plan_score,
            detail=f"온보딩 {answered_count}/{TOTAL_QUESTIONS} 문항에 답했어요.",
        ))

        # ④ 안전한 금융 습관 (탐지 이력 있을 때만)
        safety_score, safety_detail = await self._safety_score(user_id)
        items.append(ReadinessItem(
            key="safety", label="안전한 금융 습관", weight=W_SAFETY,
            score=safety_score, detail=safety_detail,
        ))

        # 평가 가능한 항목만으로 환산
        evaluated = [i for i in items if i.score is not None]
        excluded  = [i for i in items if i.score is None]

        total_weight = sum(i.weight for i in evaluated)
        total_score  = sum(i.score for i in evaluated)   # type: ignore[misc]

        percent = (
            int(Decimal(total_score) / Decimal(total_weight) * 100)
            if total_weight > 0 else 0
        )
        percent = max(0, min(100, percent))

        level, level_label = self._level(percent)

        return ReadinessResult(
            percent     = percent,
            level       = level,
            level_label = level_label,
            items       = items,
            evaluated   = [i.key for i in evaluated],
            excluded    = [i.key for i in excluded],
            message     = self._message(percent, excluded, goal_progress),
        )

    # 소비 관리 습관

    async def _spending_score(self, user_id: int) -> tuple[int | None, str]:
        """
        최근 90일 Daily Limit 기록에서 한도가 0원을 넘긴 날의 비율을 본다.
        기록이 없으면 평가하지 않는다.
        """
        since = date.today() - timedelta(days=LOOKBACK_DAYS)

        result = await self.db.execute(
            select(AiAnalysisLog.daily_limit).where(
                and_(
                    AiAnalysisLog.user_id       == user_id,
                    AiAnalysisLog.analysis_type == "DAILY_LIMIT",
                    AiAnalysisLog.daily_limit.isnot(None),
                    func.date(AiAnalysisLog.created_at) >= since,
                )
            )
        )
        values = [v for (v,) in result.all() if v is not None]

        if not values:
            return None, "아직 소비 기록이 없어 평가에서 제외했어요."

        ok_days = sum(1 for v in values if v > 0)
        rate    = Decimal(ok_days) / Decimal(len(values))
        score   = int((rate * W_SPENDING).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        return score, f"최근 {len(values)}일 중 {ok_days}일을 한도 안에서 보냈어요."

    # 안전한 금융 습관

    async def _safety_score(self, user_id: int) -> tuple[int | None, str]:
        """
        최근 90일 FDS 탐지 이력. 탐지 기록 자체가 없으면 평가하지 않는다.
        거래가 없어서 탐지가 없는 것과 거래가 안전한 것은 다르기 때문이다.
        """
        since = date.today() - timedelta(days=LOOKBACK_DAYS)

        result = await self.db.execute(
            select(FdsInferenceLog.risk_level).where(
                and_(
                    FdsInferenceLog.user_id == user_id,
                    func.date(FdsInferenceLog.created_at) >= since,
                )
            )
        )
        levels = [lv for (lv,) in result.all()]

        if not levels:
            return None, "아직 거래 기록이 없어 평가에서 제외했어요."

        high   = sum(1 for lv in levels if lv == RiskLevel.HIGH)
        medium = sum(1 for lv in levels if lv == RiskLevel.MEDIUM)

        score = max(0, W_SAFETY - high * PENALTY_HIGH - medium * PENALTY_MEDIUM)

        if high == 0 and medium == 0:
            detail = f"최근 거래 {len(levels)}건 모두 안전했어요."
        else:
            detail = f"최근 이상거래 탐지 {high + medium}건이 있었어요."

        return score, detail

    # 레벨

    @staticmethod
    def _level(percent: int) -> tuple[int, str]:
        """
        RPG 스테이터스 창에 쓸 레벨. 준비도 구간을 5단계로 나눈다.

        라벨은 종합 점수의 단계를 뜻하며 목표 금액 도달을 뜻하지 않는다.
        습관·계획 점수가 높으면 자금이 부족해도 상위 레벨이 나올 수 있어,
        '거의 다 왔어요' 같은 문구는 목표 도달로 오해될 수 있기 때문이다.
        목표 금액 상태는 _message() 가 항상 따로 알린다.
        """
        if percent >= 90: return 5, "모든 항목이 탄탄해요"
        if percent >= 70: return 4, "습관과 계획이 자리잡았어요"
        if percent >= 45: return 3, "차근차근 쌓는 중"
        if percent >= 20: return 2, "첫 걸음을 뗐어요"
        return 1, "이제 시작이에요"

    @staticmethod
    def _message(
        percent: int, excluded: list[ReadinessItem], goal_progress: Decimal
    ) -> str:
        """
        종합 점수만 말하면 자금 상태를 오해할 수 있다.
        습관 점수가 높아 총점이 70% 여도 목표 금액은 45% 일 수 있기 때문에,
        목표 금액이 얼마나 모였는지를 항상 함께 알린다.
        """
        base = {
            5: "준비가 고르게 갖춰졌어요.",
            4: "습관과 계획이 자리잡았어요.",
            3: "방향은 잡혔어요. 매달 조금씩 좁혀가는 중이에요.",
            2: "시작이 가장 어려운데 이미 넘었어요.",
            1: "목표를 정하는 것부터가 준비의 시작이에요.",
        }[ReadinessService._level(percent)[0]]

        goal_pct = float(min(goal_progress, Decimal("1"))) * 100
        if goal_pct >= 100:
            base += " 목표 금액은 모두 모았어요."
        elif goal_pct >= 1:
            base += f" 목표 금액은 {goal_pct:.0f}% 모았어요."
        else:
            base += " 목표 금액은 이제 모으기 시작하는 단계예요."

        if excluded:
            labels = ", ".join(i.label for i in excluded)
            base += f" ({labels} 항목은 데이터가 쌓이면 함께 반영돼요)"

        return base
