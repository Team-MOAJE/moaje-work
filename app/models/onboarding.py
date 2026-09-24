"""
재무 온보딩 (My Future) 모델

기획안 3절의 10대 온보딩 질문을 저장하고 Future Profile 을 생성한다.

설계 원칙 (기획안 명시 사항):
  - 질문 문구·개수·답변 코드는 모아제의 설계이며 검증된 심리검사가 아니다.
  - Q6 의 구매 습관 답변으로 신용 상태를 추론하지 않는다.
  - '아직 모르겠어요'(UNKNOWN) 를 모든 선택형 문항에서 허용한다.
  - 답변은 언제든 수정 가능하다. (user_id, question_no) 로 한 행만 유지한다.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, String, Text, Integer,
    Boolean, Index, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.spending import generate_tsid


class OnboardingAnswer(Base):
    """
    온보딩 문항별 답변 (원천 데이터)

    Future Profile 은 이 답변들로부터 파생되므로, 답변이 정본이다.
    재답변 시 새 행을 만들지 않고 기존 행을 갱신한다.
    """
    __tablename__ = "onboarding_answer"

    id          : Mapped[int]      = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id     : Mapped[int]      = mapped_column(BigInteger, nullable=False)
    question_no : Mapped[int]      = mapped_column(Integer, nullable=False)          # 1~10

    # 선택형 답변 코드 (예: STUDENT, MONTHLY_RENT, UNKNOWN)
    answer_code : Mapped[str]      = mapped_column(String(50), nullable=False)

    # 서술형 답변 (Q9 '3년 뒤 성공한 내 모습' 등). 선택형은 NULL
    answer_text : Mapped[str]      = mapped_column(Text, nullable=True)

    answered_at : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at  : Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # 한 문항당 한 답변만 유지 — 재답변은 갱신
        UniqueConstraint("user_id", "question_no", name="uq_onboarding_user_q"),
        Index("idx_onboarding_user", "user_id"),
    )


class FutureProfile(Base):
    """
    Future Profile (미래 카드)

    온보딩 답변을 조합해 생성하는 요약 레코드.
    답변 자체는 OnboardingAnswer 가 정본이며, 이 테이블은
    조회 성능과 완료 상태 관리를 위한 파생 데이터다.

    완료 상태 정합성 (기획안 6절):
      Work 의 답변 저장과 Auth 의 완료 플래그 갱신 중 하나만 성공할 수 있다.
      답변의 원천은 Work 이므로, 완료 상태가 답변보다 먼저 확정되지 않도록
      is_completed 는 10문항이 모두 저장된 뒤에만 True 가 된다.
      auth_synced 는 Auth 로의 플래그 전파 성공 여부를 따로 기록해
      실패 시 재시도할 수 있게 한다.
    """
    __tablename__ = "future_profile"

    id           : Mapped[int]  = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id      : Mapped[int]  = mapped_column(BigInteger, nullable=False)

    # 답변 요약 (계산 입력으로 쓰는 항목만 컬럼화)
    status_code       : Mapped[str] = mapped_column(String(50), nullable=True)  # Q1 현재 상태
    housing_type      : Mapped[str] = mapped_column(String(50), nullable=True)  # Q2 주거 형태
    work_env          : Mapped[str] = mapped_column(String(50), nullable=True)  # Q3 근무 환경
    value_priority    : Mapped[str] = mapped_column(String(50), nullable=True)  # Q7 가치관
    must_keep_category: Mapped[str] = mapped_column(String(50), nullable=True)  # Q8 지키고 싶은 소비
    money_concern     : Mapped[str] = mapped_column(String(50), nullable=True)  # Q10 막막한 점

    # Q9 서술형 — 메인 화면 목표 텍스트로 사용
    success_image     : Mapped[str] = mapped_column(Text, nullable=True)

    # 미래 카드
    card_title   : Mapped[str] = mapped_column(String(100), nullable=True)
    card_summary : Mapped[str] = mapped_column(Text, nullable=True)

    # 진행 상태
    answered_count : Mapped[int]  = mapped_column(Integer, nullable=False, default=0)
    is_completed   : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_synced    : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    completed_at : Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at   : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at   : Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_future_profile_user"),
    )


class FeatureEvent(Base):
    """
    재방문 검증용 사용 이벤트 로그

    기획안 6절 '재방문 검증':
      "문항별 이탈률·온보딩 완료율·시뮬레이터 재사용률·
       다음 달 Recap 열람률을 확인합니다."

    위 네 지표를 내려면 '누가 언제 어떤 기능을 썼는가'가 필요하다.
    기존 ai_analysis_log 는 Daily Limit 계산 이력 전용이라
    온보딩·시뮬레이터·Recap 사용까지 담기엔 성격이 다르다.

    개인정보는 담지 않는다. 사용자 식별자와 기능 종류, 시각만 남기고
    답변 내용이나 금액은 기록하지 않는다. 지표 산출에 필요하지 않고,
    답변은 onboarding_answer 가 이미 갖고 있기 때문이다.
    """
    __tablename__ = "feature_event"

    id         : Mapped[int] = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id    : Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ONBOARDING_VIEW / ONBOARDING_ANSWER / ONBOARDING_COMPLETE
    # SIMULATE / SIMULATE_ADJUST / READINESS_VIEW / RECAP_VIEW
    event_type : Mapped[str] = mapped_column(String(40), nullable=False)

    # 문항 번호(온보딩) 또는 조회 대상 월(Recap) 등 지표 산출에 필요한 최소 정보
    ref_key    : Mapped[str] = mapped_column(String(40), nullable=True)

    created_at : Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_feature_user_type", "user_id", "event_type"),
        Index("idx_feature_created", "created_at"),
    )
