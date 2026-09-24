"""
Future Simulator — 미래 필요 자금 역산과 Gap 계산

기획안 2절 ②:
  사용자가 원하는 미래에 필요한 예상 자금을 구체적인 숫자로 도출하고
  현재 자산과의 Gap 을 보여준다.

기획안 4-A ④:
  미래 자산의 계산 책임은 Work 에 있다. Asset 은 '현재 자산' 수치만
  제공하고, Work 가 미래 필요 자금을 도출해 Gap 을 계산한다.

기획안 6절이 요구한 투명성:
  - 계산에 쓰인 모든 값의 출처·기준일·범위를 응답에 함께 싣는다.
  - 사용자가 입력하지 않아 가정값을 쓴 항목을 따로 표시한다.
  - "커피를 줄이면 3개월 단축" 같은 문구는 같은 산식으로 계산된
    경우에만 표시한다. 이 서비스의 simulate_adjustment() 가
    본 계산과 동일한 함수를 재사용하는 이유다.
"""
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.ai import cost_baseline as CB
from app.services.ai.cost_baseline import Baseline, ValueKind

logger = logging.getLogger(__name__)


def _won(v: Decimal) -> Decimal:
    """원 단위로 반올림"""
    return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass
class SimulationInput:
    """
    계산 입력.

    사용자가 직접 넣은 값은 그대로 쓰고, 비어 있으면 온보딩 답변으로
    기준값을 고른다. 어느 쪽이었는지는 assumed_keys 로 추적한다.
    """
    user_id          : int
    housing_type     : str | None = None   # 온보딩 Q2
    work_env         : str | None = None   # 온보딩 Q3
    leisure_style    : str | None = None   # 온보딩 Q5

    monthly_income   : Decimal | None = None   # 세후 월수입
    monthly_housing  : Decimal | None = None   # 월 주거비
    monthly_living   : Decimal | None = None   # 월 기본 생활비
    monthly_leisure  : Decimal | None = None   # 월 여가비
    deposit          : Decimal | None = None   # 보증금
    move_in_cost     : Decimal | None = None   # 초기 정착 비용

    current_asset    : Decimal = Decimal("0")  # Asset 에서 조회한 현재 자산
    target_months    : int | None = None       # 목표 시점 (개월). 없으면 역산만


@dataclass
class SimulationResult:
    # 목표 금액 구성
    deposit            : Decimal
    move_in_cost       : Decimal
    emergency_fund     : Decimal
    target_amount      : Decimal

    # 월 현금흐름
    monthly_income     : Decimal
    monthly_housing    : Decimal
    monthly_living     : Decimal
    monthly_leisure    : Decimal
    monthly_expense    : Decimal
    monthly_savable    : Decimal

    # 달성 전망
    current_asset      : Decimal
    gap                : Decimal
    months_to_goal     : int | None   # 월 저축액이 0 이하면 None
    progress_rate      : Decimal      # 0~1

    # 투명성
    assumed_keys       : list[str]    # 가정값을 쓴 항목
    baselines_used     : list[dict]   # 계산에 쓰인 기준값 전체


class SimulatorService:
    """
    미래 필요 자금 = 보증금 + 초기 정착 비용 + 비상금
    비상금        = 월 생활비 × 비상금 개월 수
    월 저축 가능액 = 월수입 − 월 지출
    달성 개월 수   = ceil(Gap ÷ 월 저축 가능액)
    """

    # 입력값 결정

    @staticmethod
    def _resolve(
        user_value: Decimal | None,
        baseline: Baseline,
        assumed: list[str],
    ) -> Decimal:
        """
        사용자 입력이 있으면 그 값을, 없으면 기준값을 쓴다.
        기준값이 ASSUMPTION 이면 가정을 썼다고 기록한다.
        """
        if user_value is not None:
            return user_value
        if baseline.kind == ValueKind.ASSUMPTION:
            assumed.append(baseline.key)
        return baseline.amount

    def build_result(self, inp: SimulationInput) -> SimulationResult:
        assumed  : list[str] = []
        used     : list[Baseline] = []

        # 월수입 — 사용자 입력 우선, 없으면 근무 환경 가정값
        income_bl = CB.EXPECTED_INCOME.get(
            inp.work_env or "", CB.EXPECTED_INCOME["STARTUP"]
        )
        used.append(income_bl)
        monthly_income = self._resolve(inp.monthly_income, income_bl, assumed)

        # 보증금 — 주거 형태별
        deposit_bl = CB.HOUSING_DEPOSIT.get(
            inp.housing_type or "", CB.HOUSING_DEPOSIT["MONTHLY_RENT"]
        )
        used.append(deposit_bl)
        deposit = self._resolve(inp.deposit, deposit_bl, assumed)

        # 월 주거비 — 월수입 × RIR (본가 거주는 0)
        used.append(CB.RIR_RATIO)
        if inp.monthly_housing is not None:
            monthly_housing = inp.monthly_housing
        elif inp.housing_type == "WITH_FAMILY":
            monthly_housing = Decimal("0")
        else:
            monthly_housing = _won(monthly_income * CB.RIR_RATIO.amount)

        # 월 기본 생활비
        used.append(CB.LIVING_BASE_MONTHLY)
        monthly_living = self._resolve(inp.monthly_living, CB.LIVING_BASE_MONTHLY, assumed)

        # 월 여가비 — 전체 평균 × 주말 활동 계수
        used.append(CB.LEISURE_MONTHLY)
        if inp.monthly_leisure is not None:
            monthly_leisure = inp.monthly_leisure
        else:
            factor = CB.LEISURE_FACTOR.get(inp.leisure_style or "", Decimal("1.0"))
            monthly_leisure = _won(CB.LEISURE_MONTHLY.amount * factor)

        # 초기 정착 비용 — 본가 거주면 들지 않는다
        used.append(CB.MOVE_IN_COST)
        if inp.move_in_cost is not None:
            move_in = inp.move_in_cost
        elif inp.housing_type == "WITH_FAMILY":
            move_in = Decimal("0")
        else:
            move_in = self._resolve(None, CB.MOVE_IN_COST, assumed)

        # 비상금 = 월 생활비 × 개월 수
        used.append(CB.EMERGENCY_MONTHS)
        if CB.EMERGENCY_MONTHS.kind == ValueKind.ASSUMPTION:
            assumed.append(CB.EMERGENCY_MONTHS.key)
        monthly_expense = monthly_housing + monthly_living + monthly_leisure
        emergency_fund  = _won(monthly_expense * CB.EMERGENCY_MONTHS.amount)

        # 목표 금액과 Gap
        target_amount = _won(deposit + move_in + emergency_fund)
        gap           = max(target_amount - inp.current_asset, Decimal("0"))

        monthly_savable = monthly_income - monthly_expense
        months_to_goal  = self._months_to_goal(gap, monthly_savable)

        progress = (
            (inp.current_asset / target_amount).quantize(Decimal("0.0001"))
            if target_amount > 0 else Decimal("1.0")
        )
        progress = min(progress, Decimal("1.0"))

        return SimulationResult(
            deposit         = _won(deposit),
            move_in_cost    = _won(move_in),
            emergency_fund  = emergency_fund,
            target_amount   = target_amount,
            monthly_income  = _won(monthly_income),
            monthly_housing = _won(monthly_housing),
            monthly_living  = _won(monthly_living),
            monthly_leisure = _won(monthly_leisure),
            monthly_expense = _won(monthly_expense),
            monthly_savable = _won(monthly_savable),
            current_asset   = _won(inp.current_asset),
            gap             = gap,
            months_to_goal  = months_to_goal,
            progress_rate   = progress,
            assumed_keys    = sorted(set(assumed)),
            baselines_used  = [b.to_dict() for b in used],
        )

    @staticmethod
    def _months_to_goal(gap: Decimal, savable: Decimal) -> int | None:
        """
        달성까지 걸리는 개월 수.
        월 저축 가능액이 0 이하면 계산이 성립하지 않으므로 None 을 돌려준다.
        (달성 불가를 '매우 큰 숫자'로 표시하면 잘못된 정보가 된다)
        """
        if gap <= 0:
            return 0
        if savable <= 0:
            return None
        return math.ceil(float(gap) / float(savable))

    # 조건부 시뮬레이션

    def simulate_adjustment(
        self, inp: SimulationInput, monthly_delta: Decimal
    ) -> dict:
        """
        월 지출을 조정했을 때의 변화를 계산한다.

        기획안 6절: "'커피를 줄이면 3개월 단축'도 같은 산식으로 계산된
        경우에만 표시합니다." — 그래서 build_result() 와 동일한
        _months_to_goal() 을 그대로 재사용한다.

        monthly_delta: 월 지출 변화량. 음수면 절약, 양수면 지출 증가.
        """
        base = self.build_result(inp)

        adjusted_savable = base.monthly_savable - monthly_delta
        adjusted_months  = self._months_to_goal(base.gap, adjusted_savable)

        if base.months_to_goal is None or adjusted_months is None:
            diff = None
        else:
            diff = base.months_to_goal - adjusted_months

        return {
            "monthly_delta"         : str(_won(monthly_delta)),
            "base_monthly_savable"  : str(base.monthly_savable),
            "adjusted_monthly_savable": str(_won(adjusted_savable)),
            "base_months_to_goal"   : base.months_to_goal,
            "adjusted_months_to_goal": adjusted_months,
            "months_saved"          : diff,
            "message"               : self._adjustment_message(diff, adjusted_months),
        }

    @staticmethod
    def _adjustment_message(diff: int | None, adjusted: int | None) -> str:
        if adjusted is None:
            return "월 지출이 수입을 넘어서 달성 시점을 계산할 수 없어요."
        if diff is None:
            return f"조정하면 약 {adjusted}개월 뒤에 도달해요."
        if diff > 0:
            return f"달성 시점이 {diff}개월 앞당겨져요."
        if diff < 0:
            return f"달성 시점이 {abs(diff)}개월 늦어져요."
        return "달성 시점에는 큰 변화가 없어요."

    # 조언 문구

    @staticmethod
    def build_advice(result: SimulationResult, must_keep: str | None) -> list[str]:
        """
        조언 문구.

        기획안 6절: "택시비도 통학·안전상 필요할 수 있으므로 카테고리만
        보고 낭비로 단정하지 않습니다." — 절약을 일반론으로 권하지 않고,
        Q8 에서 지키고 싶다고 답한 소비는 줄이라고 말하지 않는다.
        """
        tips = []

        if result.monthly_savable <= 0:
            tips.append(
                "지금 계산으로는 월 지출이 수입과 같거나 많아요. "
                "수입이나 고정비를 먼저 조정해야 목표 계산이 의미를 가져요."
            )
        elif result.months_to_goal == 0:
            tips.append("이미 목표 금액을 모았어요.")
        elif result.months_to_goal is not None:
            years  = result.months_to_goal // 12
            months = result.months_to_goal % 12
            # 12개월은 '1년 0개월' 이 아니라 '1년' 으로 읽히는 게 자연스럽다
            if years > 0 and months > 0:
                period = f"{years}년 {months}개월"
            elif years > 0:
                period = f"{years}년"
            else:
                period = f"{months}개월"
            tips.append(f"지금 속도면 약 {period} 뒤에 목표에 도달해요.")

        if result.assumed_keys:
            tips.append(
                f"{len(result.assumed_keys)}개 항목은 아직 입력하지 않아 "
                "모아제의 가정값으로 계산했어요. 실제 값을 넣으면 더 정확해져요."
            )

        if must_keep:
            label = {
                "FOOD": "먹는 것", "HOBBY": "취미", "TRAVEL": "여행",
                "APPEARANCE": "옷·미용", "RELATION": "사람들과의 시간",
                "LEARNING": "배움",
            }.get(must_keep)
            if label:
                tips.append(f"{label}은 지키고 싶다고 하셨으니 계산에서 줄이지 않았어요.")

        return tips
