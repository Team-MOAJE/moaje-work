"""
Future Simulator 비용 기준값

기획안 6절 요구사항:
  "기준 가격의 출처·기준일·범위를 표시하고, AI는 확인된 수치와
   계산 결과를 설명하도록 합니다."

따라서 모든 기준값은 금액만 두지 않고 출처(source)·기준일(as_of)·
적용 범위와 주의사항(scope)을 함께 보관한다. API 응답에도 그대로 실어
사용자가 어떤 가정으로 계산됐는지 확인할 수 있게 한다.

값의 성격은 셋으로 나뉜다.

  STATISTIC : 공표된 통계에서 가져온 값. 출처와 기준일이 명확하다.
  DERIVED   : 통계에서 계산으로 끌어낸 값 (예: 월수입 × RIR).
  ASSUMPTION: 모아제가 정한 가정값. 통계가 아니며 사용자 입력으로
              대체하는 것을 전제로 한다.

ASSUMPTION 은 반드시 그 사실을 사용자에게 표시한다.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class ValueKind(str, Enum):
    STATISTIC  = "STATISTIC"
    DERIVED    = "DERIVED"
    ASSUMPTION = "ASSUMPTION"


@dataclass(frozen=True)
class Baseline:
    """기준값 하나. 금액과 함께 근거를 보관한다."""
    key      : str
    label    : str
    amount   : Decimal
    kind     : ValueKind
    source   : str          # 출처 기관·조사명
    as_of    : str          # 기준 시점
    scope    : str          # 적용 범위와 주의사항
    url      : str = ""

    def to_dict(self) -> dict:
        return {
            "key"    : self.key,
            "label"  : self.label,
            "amount" : str(self.amount),
            "kind"   : self.kind.value,
            "source" : self.source,
            "as_of"  : self.as_of,
            "scope"  : self.scope,
            "url"    : self.url,
        }


# ── 주거 ─────────────────────────────────────────────────────
#
# 월 주거비는 고정 금액이 아니라 월수입 대비 비율(RIR)로 계산한다.
# 지역·주거 규모에 따라 실제 금액이 크게 달라지므로, 비율을 쓰면
# 사용자가 입력한 월수입에 맞춰 조정되기 때문이다.

RIR_RATIO = Baseline(
    key    = "rir_ratio",
    label  = "월소득 대비 월임대료 비율(RIR)",
    amount = Decimal("0.158"),
    kind   = ValueKind.STATISTIC,
    source = "국토교통부 2023년도 주거실태조사 (2024-12 발표)",
    as_of  = "2023년 조사",
    scope  = "전체 임차가구 평균. 지역·주거 형태별로 차이가 크며 "
             "수도권·청년 1인가구는 이보다 높을 수 있다.",
    url    = "https://eiec.kdi.re.kr/policy/materialView.do?num=261725",
)

# 보증금은 주거 형태별로 다르다.
# 전체 가구 평균 임대보증금(2,739만원)은 상가·토지 보증금까지 포함한
# 값이라 청년 첫 독립의 기준으로 그대로 쓰기 어렵다. 참고 지표로만 두고,
# 형태별 금액은 가정값으로 명시한다.
DEPOSIT_REFERENCE = Baseline(
    key    = "deposit_reference",
    label  = "전체 가구 평균 임대보증금",
    amount = Decimal("27390000"),
    kind   = ValueKind.STATISTIC,
    source = "2025년 가계금융복지조사 (국가데이터처·한국은행·금융감독원)",
    as_of  = "2025년 3월 말",
    scope  = "주거용뿐 아니라 상가·토지 보증금을 포함한 전체 가구 평균. "
             "청년 1인가구 첫 독립 기준과는 다르므로 참고용으로만 본다.",
    url    = "https://biz.heraldcorp.com/article/10629759",
)

# 주거 형태별 가정값 (사용자 입력으로 대체 권장)
HOUSING_DEPOSIT: dict[str, Baseline] = {
    "WITH_FAMILY": Baseline(
        key="housing_deposit_with_family", label="본가 거주 보증금",
        amount=Decimal("0"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="본가 거주는 보증금이 들지 않는다고 본다.",
    ),
    "MONTHLY_RENT": Baseline(
        key="housing_deposit_monthly_rent", label="월세 보증금",
        amount=Decimal("10000000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="수도권 원룸 월세를 가정한 값이며 통계가 아니다. "
              "지역·평형에 따라 크게 달라지므로 실제 시세로 입력하는 것을 권한다.",
    ),
    "JEONSE": Baseline(
        key="housing_deposit_jeonse", label="전세 보증금",
        amount=Decimal("100000000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="수도권 소형 전세를 가정한 값이며 통계가 아니다. "
              "전세대출을 쓰는 경우 자기부담금만 목표에 넣어야 한다.",
    ),
    "DORM_SHARE": Baseline(
        key="housing_deposit_dorm", label="기숙사·셰어하우스 보증금",
        amount=Decimal("2000000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="보증금이 낮거나 없는 경우가 많아 소액으로 가정했다.",
    ),
}

# ── 여가 ─────────────────────────────────────────────────────

LEISURE_MONTHLY = Baseline(
    key    = "leisure_monthly",
    label  = "월평균 여가비용",
    amount = Decimal("187000"),
    kind   = ValueKind.STATISTIC,
    source = "문화체육관광부 국민문화예술활동·여가활동조사 (2024-12 발표)",
    as_of  = "2024년 조사",
    scope  = "전 국민 평균이다. 대학생·사회초년생 개인의 예산으로 "
             "그대로 대입하지 않으며, 여가 유형에 따라 조정한다.",
    url    = "https://www.mcst.go.kr/site/s_notice/press/pressView.jsp?pSeq=21576",
)

# 주말 활동(Q5)별 여가비 조정 계수 — 전체 평균을 기준 1.0 으로 둔다
LEISURE_FACTOR: dict[str, Decimal] = {
    "HOME_REST": Decimal("0.6"),
    "SELF_DEV" : Decimal("0.9"),
    "CULTURE"  : Decimal("1.2"),
    "SOCIAL"   : Decimal("1.3"),
    "OUTDOOR"  : Decimal("1.1"),
}

# ── 생활비 (여가 제외) ────────────────────────────────────────
#
# 식비·통신·교통 등을 묶은 가정값이다. 1인가구 항목별 공표 통계를
# 확인하지 못해 가정으로 둔다. 사용자 입력을 우선 사용한다.

LIVING_BASE_MONTHLY = Baseline(
    key    = "living_base_monthly",
    label  = "월 기본 생활비 (주거·여가 제외)",
    amount = Decimal("700000"),
    kind   = ValueKind.ASSUMPTION,
    source = "모아제 가정",
    as_of  = "-",
    scope  = "식비·통신·교통·생필품을 묶은 가정값이며 공표 통계가 아니다. "
             "실제 지출을 입력하면 그 값으로 계산한다.",
)

# ── 초기 정착 비용 ────────────────────────────────────────────

MOVE_IN_COST = Baseline(
    key    = "move_in_cost",
    label  = "이사·가전·가구 등 초기 정착 비용",
    amount = Decimal("3000000"),
    kind   = ValueKind.ASSUMPTION,
    source = "모아제 가정",
    as_of  = "-",
    scope  = "이사비·기본 가전·가구를 합한 가정값이며 통계가 아니다. "
             "본가 거주를 선택하면 적용하지 않는다.",
)

# ── 예상 월수입 (세후) ────────────────────────────────────────
#
# 근무 환경(Q3)만으로 개인의 연봉을 확정할 수 없다.
# 아래는 계산을 시작하기 위한 가정값일 뿐이며, 사용자 입력을 우선한다.

EXPECTED_INCOME: dict[str, Baseline] = {
    "LARGE_CORP": Baseline(
        key="income_large_corp", label="대기업·공공기관 예상 월수입(세후)",
        amount=Decimal("3000000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="희망 기업 유형만으로 개인 연봉을 확정할 수 없다. "
              "계산을 시작하기 위한 가정값이며 실제 목표 연봉을 입력하는 것을 권한다.",
    ),
    "STARTUP": Baseline(
        key="income_startup", label="스타트업·중소기업 예상 월수입(세후)",
        amount=Decimal("2500000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="희망 기업 유형만으로 개인 연봉을 확정할 수 없다.",
    ),
    "FREELANCE": Baseline(
        key="income_freelance", label="프리랜서·창업 예상 월수입(세후)",
        amount=Decimal("2200000"), kind=ValueKind.ASSUMPTION,
        source="모아제 가정", as_of="-",
        scope="자영업자는 월별 소득 변동이 크다. 미 연준 2024 조사에서 "
              "월별 소득 변동 응답이 자영업자 59%, 피고용자 28% 였다. "
              "변동성을 감안해 보수적으로 잡는 것을 권한다.",
        url="https://www.federalreserve.gov/publications/2025-economic-well-being-of-us-households-in-2024-income-and-expenses.htm",
    ),
}

# ── 비상금 ───────────────────────────────────────────────────

EMERGENCY_MONTHS = Baseline(
    key    = "emergency_months",
    label  = "비상금 개월 수",
    amount = Decimal("3"),
    kind   = ValueKind.ASSUMPTION,
    source = "모아제 가정",
    as_of  = "-",
    scope  = "예상치 못한 지출에 대비할 기간을 월 생활비 기준 3개월로 가정했다. "
             "소득 변동이 큰 경우 더 길게 잡는 것을 권한다.",
)


def all_baselines() -> list[Baseline]:
    """API 로 노출할 전체 기준값 목록"""
    out = [RIR_RATIO, DEPOSIT_REFERENCE, LEISURE_MONTHLY,
           LIVING_BASE_MONTHLY, MOVE_IN_COST, EMERGENCY_MONTHS]
    out.extend(HOUSING_DEPOSIT.values())
    out.extend(EXPECTED_INCOME.values())
    return out
