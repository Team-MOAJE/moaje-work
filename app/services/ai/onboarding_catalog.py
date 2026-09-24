"""
온보딩 10문항 카탈로그

기획안 3절의 질문·수집 목적을 코드로 옮긴 정의다.

주의 (기획안 명시):
  질문 문구, 10개라는 개수, 답변 코드는 모아제의 설계이며
  검증된 심리검사나 신용평가 척도가 아니다.
  답변 하나로 개인의 성격·신용 상태를 진단하지 않는다.

UNKNOWN('아직 모르겠어요')는 모든 선택형 문항에서 허용한다.
계산 단계에서는 UNKNOWN 을 '미입력'으로 보고 기본 가정을 적용하며,
그 사실을 사용자에게 표시한다.
"""
from typing import Optional

UNKNOWN = "UNKNOWN"
_UNKNOWN_LABEL = "아직 모르겠어요"


class Question:
    def __init__(
        self,
        no: int,
        phase: str,
        text: str,
        purpose: str,
        options: dict[str, str],
        free_text: bool = False,
    ):
        self.no        = no
        self.phase     = phase
        self.text      = text
        self.purpose   = purpose
        self.options   = options      # code -> 표시 문구
        self.free_text = free_text    # 서술형 여부

    def valid_codes(self) -> set[str]:
        codes = set(self.options.keys())
        codes.add(UNKNOWN)
        return codes


QUESTIONS: dict[int, Question] = {
    1: Question(
        no=1, phase="현재",
        text="지금 어떤 상태이신가요?",
        purpose="목표 기간의 초기 가정",
        options={
            "STUDENT"    : "학생",
            "JOB_SEEKER" : "취업 준비 중",
            "EMPLOYED"   : "직장인",
            "OTHER"      : "그 외",
        },
    ),
    2: Question(
        no=2, phase="고정비",
        text="취업 후 가장 먼저 그려보는 주거 형태는?",
        purpose="보증금·월 주거비 추정의 출발점",
        options={
            "WITH_FAMILY"  : "본가에서 계속 거주",
            "MONTHLY_RENT" : "월세",
            "JEONSE"       : "전세",
            "DORM_SHARE"   : "기숙사 · 셰어하우스",
        },
    ),
    3: Question(
        no=3, phase="고정비",
        text="내가 일하고 싶은 환경은?",
        purpose="수입 형태·변동성 시나리오 선택",
        options={
            "LARGE_CORP" : "대기업 · 공공기관",
            "STARTUP"    : "스타트업 · 중소기업",
            "FREELANCE"  : "프리랜서 · 창업",
        },
    ),
    4: Question(
        no=4, phase="변동비",
        text="첫 월급을 받으면 가장 먼저 하고 싶은 일은?",
        purpose="첫 수입의 사용 우선순위",
        options={
            "REWARD"    : "나를 위한 선물",
            "SAVING"    : "저축 · 투자 시작",
            "FAMILY"    : "가족에게 보답",
            "DEBT"      : "학자금 등 상환",
            "EXPERIENCE": "여행 등 경험",
        },
    ),
    5: Question(
        no=5, phase="변동비",
        text="직장인이 된 나의 이상적인 주말은?",
        purpose="여가 활동 파악 (비용은 빈도·단가로 별도 계산)",
        options={
            "HOME_REST": "집에서 푹 쉬기",
            "OUTDOOR"  : "야외 활동 · 운동",
            "CULTURE"  : "전시 · 공연 · 영화",
            "SOCIAL"   : "친구들과 만남",
            "SELF_DEV" : "자기계발 · 공부",
        },
    ),
    6: Question(
        no=6, phase="변동비",
        text="평소 사고 싶은 물건이 생겼을 때 나의 행동은?",
        # 기획안 명시: 이 답변으로 신용 상태를 추론하지 않는다
        purpose="구매 전 숙고·지출 관리 습관 파악 (신용 상태는 추론하지 않음)",
        options={
            "BUY_NOW"     : "바로 구매하는 편",
            "COMPARE"     : "가격을 비교해보고 결정",
            "WAIT"        : "며칠 두고 고민",
            "BUDGET_CHECK": "예산을 먼저 확인",
        },
    ),
    7: Question(
        no=7, phase="가치관",
        text="둘 중 하나를 고른다면?",
        purpose="소득과 생활시간 중 우선순위",
        options={
            "HIGH_SALARY": "연봉이 높고 바쁜 일",
            "WORK_LIFE"  : "평균 연봉에 여유로운 일",
        },
    ),
    8: Question(
        no=8, phase="가치관",
        text="내 삶에서 가장 포기할 수 없는 소비는?",
        purpose="Recap 에서 존중할 소비 우선순위",
        options={
            "FOOD"      : "먹는 것",
            "HOBBY"     : "취미 · 덕질",
            "TRAVEL"    : "여행",
            "APPEARANCE": "옷 · 미용",
            "RELATION"  : "사람들과의 시간",
            "LEARNING"  : "배움 · 자기계발",
        },
    ),
    9: Question(
        no=9, phase="미래",
        text="3년 뒤, 내가 생각하는 '성공한 내 모습'은?",
        purpose="미래 카드와 메인 화면의 목표 텍스트 생성",
        options={},
        free_text=True,
    ),
    10: Question(
        no=10, phase="미래",
        text="현재 돈과 관련해서 가장 막막한 점은?",
        purpose="먼저 도움받고 싶은 주제 선택",
        options={
            "NO_SAVING"   : "모으질 못하겠어요",
            "NO_PLAN"     : "얼마가 필요한지 모르겠어요",
            "UNEXPECTED"  : "갑작스러운 지출이 부담돼요",
            "NO_KNOWLEDGE": "금융 용어가 어려워요",
            "DEBT_WORRY"  : "빚이 걱정돼요",
        },
    ),
}

TOTAL_QUESTIONS = len(QUESTIONS)

# Future Profile 컬럼에 매핑할 문항
PROFILE_FIELD_MAP = {
    1 : "status_code",
    2 : "housing_type",
    3 : "work_env",
    7 : "value_priority",
    8 : "must_keep_category",
    10: "money_concern",
}


def get_question(no: int) -> Optional[Question]:
    return QUESTIONS.get(no)


def validate_answer(no: int, code: str, text: Optional[str]) -> Optional[str]:
    """답변 유효성 검사. 문제가 없으면 None, 있으면 사유 문자열을 반환한다."""
    q = QUESTIONS.get(no)
    if not q:
        return f"{no}번 문항은 존재하지 않습니다 (1~{TOTAL_QUESTIONS})"

    if q.free_text:
        # 서술형은 UNKNOWN 또는 본문이 있어야 한다
        if code == UNKNOWN:
            return None
        if not text or not text.strip():
            return f"{no}번 문항은 서술형입니다. answer_text 를 입력해주세요"
        return None

    if code not in q.valid_codes():
        allowed = ", ".join(sorted(q.valid_codes()))
        return f"{no}번 문항의 답변 코드가 올바르지 않습니다. 허용: {allowed}"

    return None


def option_label(no: int, code: str) -> str:
    """답변 코드를 표시 문구로 변환"""
    if code == UNKNOWN:
        return _UNKNOWN_LABEL
    q = QUESTIONS.get(no)
    if not q:
        return code
    return q.options.get(code, code)
