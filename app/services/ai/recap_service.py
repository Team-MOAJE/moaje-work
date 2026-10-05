"""
Money Recap — 월별 소비 돌아보기와 소비패턴 별명

기획안 2절 ③:
  단순한 저축액 요약이 아니라 소비패턴에 별명을 붙이고,
  무조건적인 절약 강요 없이 포지티브 넛지로 피드백한다.

기획안 6절이 경고한 것과 그 답:

  "택시비도 통학·안전상 필요할 수 있으므로 카테고리만 보고 낭비로
   단정하지 않습니다."
  → 별명과 피드백 문구에 '낭비', '줄이세요' 같은 표현을 쓰지 않는다.
     카테고리는 소비의 성향을 보여줄 뿐 옳고 그름을 판정하지 않는다.

  "Q8의 가치관과 실제 지출을 함께 설명하고"
  → 온보딩에서 지키고 싶다고 답한 소비가 실제 지출 상위에 있으면
     그 일치를 짚어주고, 없으면 그 사실만 중립적으로 알린다.

  "거래 부족·미분류·더미 데이터일 때는 분석의 범위를 표시합니다."
  → coverage 필드로 분석에 쓴 거래 수, 미분류 비중, 신뢰 수준을
     항상 함께 돌려준다.

데이터 원천은 Asset 이 발행하는 월별·카테고리 집계다.
(moaje.asset.monthly-cashflow-aggregated / category-cashflow-aggregated)
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fds import FdsInferenceLog
from app.models.spending import MonthlyCashflow, CategoryCashflow

logger = logging.getLogger(__name__)

# 분석 신뢰 수준 기준
MIN_TX_FOR_NICKNAME = 10    # 별명을 붙이기 위한 최소 거래 수
LOW_COVERAGE_RATIO  = Decimal("0.3")   # 미분류 비중이 이보다 크면 범위를 알린다

# 미분류로 취급하는 카테고리 코드
UNCLASSIFIED_CODES = {"UNCLASSIFIED", "ETC", ""}

# Banking 이 보낼 카테고리 코드는 아직 확정 전이다.
# 아는 코드는 라벨을 붙이고, 모르는 코드는 코드 그대로 보여준다.
CATEGORY_LABEL = {
    "FOOD"         : "식비",
    "DELIVERY"     : "배달",
    "CAFE"         : "카페",
    "TRANSPORT"    : "교통",
    "SHOPPING"     : "쇼핑",
    "CULTURE"      : "문화·여가",
    "HEALTH"       : "건강",
    "EDUCATION"    : "교육",
    "COMMUNICATION": "통신",
    "HOUSING"      : "주거",
    "TRAVEL"       : "여행",
    "BEAUTY"       : "미용",
    "ETC"          : "기타",
    "UNCLASSIFIED" : "미분류",
}

# 카테고리가 두드러질 때 붙이는 별명.
# 소비를 평가하지 않고 성향을 묘사하는 표현만 쓴다.
NICKNAME_BY_CATEGORY = {
    "FOOD"      : ("미식 탐험가", "맛있는 걸 놓치지 않는 달이었어요"),
    "DELIVERY"  : ("집이 최고 파", "집에서 편하게 보낸 시간이 많았어요"),
    "CAFE"      : ("카페 러버", "카페에서 보낸 시간이 많은 달이었어요"),
    "TRANSPORT" : ("부지런한 이동러", "여기저기 많이 다닌 달이었어요"),
    "SHOPPING"  : ("취향 수집가", "마음에 드는 걸 찾아다닌 달이었어요"),
    "CULTURE"   : ("경험 수집가", "보고 듣는 데 시간을 쓴 달이었어요"),
    "HEALTH"    : ("몸 챙김러", "건강에 투자한 달이었어요"),
    "EDUCATION" : ("배움 욕심러", "배우는 데 시간을 쓴 달이었어요"),
    "TRAVEL"    : ("떠나는 사람", "떠나는 데 마음을 쓴 달이었어요"),
    "BEAUTY"    : ("나를 가꾸는 사람", "나를 단장하는 데 쓴 달이었어요"),
}

# 온보딩 Q8(지키고 싶은 소비) -> 카테고리 코드
MUST_KEEP_TO_CATEGORY = {
    "FOOD"      : {"FOOD", "DELIVERY", "CAFE"},
    "HOBBY"     : {"CULTURE", "SHOPPING"},
    "TRAVEL"    : {"TRAVEL", "TRANSPORT"},
    "APPEARANCE": {"BEAUTY", "SHOPPING"},
    "RELATION"  : {"FOOD", "CULTURE"},
    "LEARNING"  : {"EDUCATION"},
}

MUST_KEEP_LABEL = {
    "FOOD": "먹는 것", "HOBBY": "취미", "TRAVEL": "여행",
    "APPEARANCE": "옷·미용", "RELATION": "사람들과의 시간", "LEARNING": "배움",
}


@dataclass
class CategoryShare:
    code    : str
    label   : str
    amount  : Decimal
    share   : Decimal      # 지출 대비 비중 0~1
    # 계약(CategoryCashflowAmount)은 category 와 amount 만 준다.
    # 카테고리별 거래 건수는 오지 않으므로 두지 않는다.


@dataclass
class RecapCoverage:
    """
    분석의 범위. 결과를 어디까지 믿어도 되는지 알린다.

    total_tx 는 Work 가 그 달에 받은 거래 건수다. Asset 의 카테고리 집계에는
    건수가 없어서, Work 가 직접 받아 적재한 거래 로그에서 센다.
    classified_tx 는 건수가 아니라 '분류된 지출 금액의 비중'으로 환산한
    추정치이므로, 정확한 건수로 읽지 않는다.
    """
    total_tx          : int
    classified_tx     : int
    unclassified_share: Decimal     # 금액 기준
    is_reliable       : bool
    note              : str


@dataclass
class RecapResult:
    year_month     : str
    revision       : int

    total_income   : Decimal
    total_expense  : Decimal
    total_transfer : Decimal
    net_saving     : Decimal

    categories     : list[CategoryShare]
    top_category   : CategoryShare | None

    nickname       : str | None
    nickname_reason: str | None

    coverage       : RecapCoverage
    insights       : list[str] = field(default_factory=list)


class RecapService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_recap(
        self, user_id: int, year_month: str, must_keep: str | None = None
    ) -> RecapResult | None:
        """
        해당 월의 Recap 을 만든다.
        Asset 집계가 아직 도착하지 않았으면 None 을 돌려준다.
        """
        monthly = await self._get_monthly(user_id, year_month)
        if not monthly:
            return None

        cats = await self._get_categories(user_id, year_month)

        total_expense = monthly.total_expense or Decimal("0")
        shares        = self._build_shares(cats, total_expense)
        total_tx      = await self._count_transactions(user_id, year_month)
        coverage      = self._build_coverage(cats, total_expense, total_tx)

        top = shares[0] if shares else None

        nickname, reason = self._build_nickname(top, coverage)
        insights = self._build_insights(monthly, shares, must_keep, coverage)

        return RecapResult(
            year_month      = year_month,
            revision        = monthly.revision,
            total_income    = monthly.total_income or Decimal("0"),
            total_expense   = total_expense,
            total_transfer  = monthly.total_transfer or Decimal("0"),
            net_saving      = (monthly.total_income or Decimal("0")) - total_expense,
            categories      = shares,
            top_category    = top,
            nickname        = nickname,
            nickname_reason = reason,
            coverage        = coverage,
            insights        = insights,
        )

    async def list_months(self, user_id: int, limit: int = 12) -> list[str]:
        """Recap 이 있는 월 목록 (최신순)"""
        result = await self.db.execute(
            select(MonthlyCashflow.year_month)
            .where(MonthlyCashflow.user_id == user_id)
            .order_by(MonthlyCashflow.year_month.desc())
            .limit(limit)
        )
        return [ym for (ym,) in result.all()]

    # 조회

    async def _get_monthly(self, user_id: int, ym: str) -> MonthlyCashflow | None:
        result = await self.db.execute(
            select(MonthlyCashflow).where(
                MonthlyCashflow.user_id    == user_id,
                MonthlyCashflow.year_month == ym,
            )
        )
        return result.scalar_one_or_none()

    async def _count_transactions(self, user_id: int, ym: str) -> int:
        """
        그 달에 Work 가 받은 거래 건수.

        Asset 의 카테고리 집계에는 건수가 없으므로(CategoryCashflowAmount 는
        category 와 amount 뿐), Work 가 거래 이벤트를 받아 적재한
        fds_inference_log 에서 직접 센다.

        기준 시각은 거래 발생 시각이 아니라 Work 가 받은 시각(created_at)이다.
        실시간으로 들어오는 거래는 사실상 같지만, 늦게 보정된 과거 거래는
        집계 월과 어긋날 수 있다. 이 값은 '성향을 말할 만큼 활동이 있었나'를
        가늠하는 용도이므로 그 정도 오차는 감수한다.
        """
        try:
            year, month = (int(x) for x in ym.split("-"))
        except ValueError:
            logger.warning(f"⚠️ year_month 형식 오류 | {ym!r}")
            return 0

        start = datetime(year, month, 1)
        end   = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        result = await self.db.execute(
            select(sa_func.count(FdsInferenceLog.id)).where(
                FdsInferenceLog.user_id    == user_id,
                FdsInferenceLog.created_at >= start,
                FdsInferenceLog.created_at <  end,
            )
        )
        return int(result.scalar_one() or 0)

    async def _get_categories(self, user_id: int, ym: str) -> list[CategoryCashflow]:
        result = await self.db.execute(
            select(CategoryCashflow)
            .where(
                CategoryCashflow.user_id    == user_id,
                CategoryCashflow.year_month == ym,
            )
            .order_by(CategoryCashflow.amount.desc())
        )
        return list(result.scalars().all())

    # 가공

    @staticmethod
    def _build_shares(
        cats: list[CategoryCashflow], total_expense: Decimal
    ) -> list[CategoryShare]:
        out = []
        for c in cats:
            share = (
                (c.amount / total_expense).quantize(Decimal("0.0001"))
                if total_expense > 0 else Decimal("0")
            )
            out.append(CategoryShare(
                code   = c.category_code,
                label  = CATEGORY_LABEL.get(c.category_code, c.category_code),
                amount = c.amount,
                share  = share,
            ))
        return out

    @staticmethod
    def _build_coverage(
        cats: list[CategoryCashflow], total_expense: Decimal, total_tx: int
    ) -> RecapCoverage:
        """
        분석 범위를 계산한다.
        미분류 비중이 크거나 거래가 적으면 결과를 단정적으로 말하지 않는다.

        total_tx 는 Work 가 그 달에 받은 거래 건수다.
        미분류 비중은 금액 기준으로 센다 — 계약에 카테고리별 건수가 없기도 하고,
        별명이 말하려는 것이 '돈이 어디로 갔는가'이므로 금액이 더 맞는 기준이다.
        """
        unclassified_amount = sum(
            (c.amount for c in cats if c.category_code in UNCLASSIFIED_CODES),
            Decimal("0"),
        )
        classified_amount = total_expense - unclassified_amount

        unclassified_share = (
            (unclassified_amount / total_expense).quantize(Decimal("0.01"))
            if total_expense > 0 else Decimal("0")
        )

        # 분류된 거래 '건수'는 알 수 없다. 금액 비중으로 환산한 추정치를 쓴다.
        classified_tx = (
            int(total_tx * (classified_amount / total_expense))
            if total_expense > 0 else 0
        )

        if not cats or total_expense <= 0:
            return RecapCoverage(
                total_tx=total_tx, classified_tx=0,
                unclassified_share=Decimal("0"), is_reliable=False,
                note="카테고리 정보가 아직 없어 소비 성향은 분석하지 않았어요.",
            )

        if total_tx == 0:
            return RecapCoverage(
                total_tx=0, classified_tx=0,
                unclassified_share=unclassified_share, is_reliable=False,
                note="이번 달 거래 기록이 아직 없어 성향은 분석하지 않았어요.",
            )

        if total_tx < MIN_TX_FOR_NICKNAME:
            return RecapCoverage(
                total_tx=total_tx, classified_tx=classified_tx,
                unclassified_share=unclassified_share, is_reliable=False,
                note=f"이번 달 거래가 {total_tx}건이라 성향을 말하기엔 일러요.",
            )

        if unclassified_share > LOW_COVERAGE_RATIO:
            pct = int(unclassified_share * 100)
            return RecapCoverage(
                total_tx=total_tx, classified_tx=classified_tx,
                unclassified_share=unclassified_share, is_reliable=False,
                note=f"지출의 {pct}% 가 분류되지 않아 참고용으로만 봐주세요.",
            )

        return RecapCoverage(
            total_tx=total_tx, classified_tx=classified_tx,
            unclassified_share=unclassified_share, is_reliable=True,
            note=f"거래 {total_tx}건을 분석했어요.",
        )

    @staticmethod
    def _build_nickname(
        top: CategoryShare | None, coverage: RecapCoverage
    ) -> tuple[str | None, str | None]:
        """
        별명은 데이터가 충분할 때만 붙인다.
        거래 몇 건으로 '당신은 이런 사람'이라고 말하지 않기 위해서다.
        """
        if not coverage.is_reliable or not top:
            return None, None

        # 특정 카테고리가 두드러지지 않으면 균형형으로 본다
        if top.share < Decimal("0.3"):
            return "균형 잡힌 생활러", "어느 한쪽에 치우치지 않고 고르게 쓴 달이었어요"

        pair = NICKNAME_BY_CATEGORY.get(top.code)
        if not pair:
            return None, None
        return pair

    @staticmethod
    def _build_insights(
        monthly: MonthlyCashflow,
        shares: list[CategoryShare],
        must_keep: str | None,
        coverage: RecapCoverage,
    ) -> list[str]:
        """
        피드백 문구.
        절약을 권하거나 특정 소비를 낭비로 규정하지 않는다.
        """
        out = []

        income  = monthly.total_income or Decimal("0")
        expense = monthly.total_expense or Decimal("0")
        net     = income - expense

        if income > 0:
            if net > 0:
                rate = int((net / income) * 100)
                out.append(f"수입의 {rate}% 가 남았어요.")
            elif net < 0:
                out.append("이번 달은 수입보다 지출이 많았어요.")
            else:
                out.append("수입과 지출이 거의 같았어요.")

        # Q8 가치관과 실제 지출을 함께 설명한다
        if must_keep and shares and coverage.is_reliable:
            targets = MUST_KEEP_TO_CATEGORY.get(must_keep, set())
            label   = MUST_KEEP_LABEL.get(must_keep, must_keep)
            top3    = {s.code for s in shares[:3]}

            if targets & top3:
                out.append(f"지키고 싶다고 하신 {label} 관련 소비가 상위에 있었어요.")
            else:
                out.append(
                    f"지키고 싶다고 하신 {label} 관련 소비는 이번 달 상위에 없었어요."
                )

        if not coverage.is_reliable:
            out.append(coverage.note)

        return out
