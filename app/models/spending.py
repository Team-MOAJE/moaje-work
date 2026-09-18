from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, Date, Numeric, Enum, Index, String, Integer, JSON, SmallInteger, UniqueConstraint, Boolean, Text, func
from sqlalchemy.orm import Mapped, mapped_column
import enum
from app.db.session import Base
from tsidpy import TSID


def generate_tsid() -> int:
    return TSID.create().number


class EventType(str, enum.Enum):
    EXAM           = "EXAM"
    MT             = "MT"
    FESTIVAL       = "FESTIVAL"
    VACATION       = "VACATION"
    EMPLOYMENT     = "EMPLOYMENT"
    # ✅ 학사 일정 자동 연동용 추가 타입
    SEMESTER_START = "SEMESTER_START"   # 개강
    SEMESTER_END   = "SEMESTER_END"     # 종강
    MIDTERM        = "MIDTERM"          # 중간고사
    FINAL          = "FINAL"            # 기말고사
    HOLIDAY        = "HOLIDAY"          # 공휴일/휴강


class AnalysisType(str, enum.Enum):
    DAILY_LIMIT    = "DAILY_LIMIT"
    PATTERN_UPDATE = "PATTERN_UPDATE"
    SCHEDULE_ALERT = "SCHEDULE_ALERT"


class PeriodType(str, enum.Enum):
    WEEKLY  = "WEEKLY"
    MONTHLY = "MONTHLY"


class AiSpendingProfile(Base):
    __tablename__ = "ai_spending_profile"

    id                  : Mapped[int]      = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id             : Mapped[int]      = mapped_column(BigInteger, nullable=False)
    # ✅ 학교 연동
    university_id       : Mapped[int]      = mapped_column(Integer, nullable=True)
    avg_daily_amount    : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=0)
    std_daily_amount    : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=15000)
    tx_count            : Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    peak_spend_hour     : Mapped[int]      = mapped_column(SmallInteger, nullable=False, default=0)
    top_category        : Mapped[str]      = mapped_column(String(50), nullable=False)
    risk_score_baseline : Mapped[Decimal]  = mapped_column(Numeric(5, 2), nullable=False, default=0)
    last_analyzed_at    : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    created_at          : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at          : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_id"),
    )


class AcademicSchedule(Base):
    __tablename__ = "academic_schedule"

    id                   : Mapped[int]       = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id              : Mapped[int]       = mapped_column(BigInteger, nullable=False)
    event_type           : Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    event_name           : Mapped[str]       = mapped_column(String(100), nullable=False)
    start_date           : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    end_date             : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    expected_extra_spend : Mapped[Decimal]   = mapped_column(Numeric(18, 4), nullable=False, default=0)
    # ✅ 자동 등록 여부 (학사 캘린더 연동 시 True)
    is_auto              : Mapped[bool]      = mapped_column(Boolean, nullable=False, default=False)
    created_at           : Mapped[datetime]  = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_user_date", "user_id", "start_date"),
    )


class AiAnalysisLog(Base):
    __tablename__ = "ai_analysis_log"

    id               : Mapped[int]          = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id          : Mapped[int]          = mapped_column(BigInteger, nullable=False)
    analysis_type    : Mapped[AnalysisType] = mapped_column(Enum(AnalysisType), nullable=False)
    input_snapshot   : Mapped[dict]         = mapped_column(JSON, nullable=False)
    result_message   : Mapped[str]          = mapped_column(String(500), nullable=False)
    daily_limit      : Mapped[Decimal]      = mapped_column(Numeric(18, 4), nullable=True)
    confidence_score : Mapped[Decimal]      = mapped_column(Numeric(5, 4), nullable=False, default=0)
    created_at       : Mapped[datetime]     = mapped_column(DateTime, nullable=False, server_default=func.now())


# ══════════════════════════════════════════════════
#  학사 일정 자동 연동 테이블
# ══════════════════════════════════════════════════

class University(Base):
    """
    대학교 목록
    학교 선택 시 academic_calendar 에서 자동으로 일정을 가져와
    academic_schedule 에 등록합니다.
    """
    __tablename__ = "university"

    id         : Mapped[int] = mapped_column(Integer, primary_key=True, default=generate_tsid)
    name       : Mapped[str] = mapped_column(String(100), nullable=False)        # 한신대학교
    short_name : Mapped[str] = mapped_column(String(30), nullable=False)         # 한신대
    region     : Mapped[str] = mapped_column(String(50), nullable=True)          # 경기도 오산시
    is_active  : Mapped[bool]= mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("name", name="uq_university_name"),
    )


class AcademicCalendar(Base):
    """
    학교별 학사 일정 마스터 데이터
    관리자가 학기별로 등록해두면, 유저가 학교 선택 시 자동으로
    academic_schedule 에 복사되어 등록됩니다.
    """
    __tablename__ = "academic_calendar"

    id                   : Mapped[int]       = mapped_column(Integer, primary_key=True, default=generate_tsid)
    university_id        : Mapped[int]       = mapped_column(Integer, nullable=False)
    year                 : Mapped[int]       = mapped_column(Integer, nullable=False)      # 2025
    semester             : Mapped[int]       = mapped_column(SmallInteger, nullable=False) # 1 or 2
    event_type           : Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    event_name           : Mapped[str]       = mapped_column(String(100), nullable=False)
    start_date           : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    end_date             : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    # 이벤트 유형별 기본 예상 추가 지출
    default_extra_spend  : Mapped[Decimal]   = mapped_column(Numeric(18, 4), nullable=False, default=0)

    __table_args__ = (
        Index("idx_calendar_univ_semester", "university_id", "year", "semester"),
    )


class MonthlyCashflow(Base):
    """
    Asset 월별 수입/지출/자금이동 집계 (Money Recap 원천)

    토픽: moaje.asset.monthly-cashflow-aggregated
    Asset 이 매월 1일 스케줄러로 집계 후 발행한다.

    revision 처리:
      늦게 도착한 거래로 Asset 이 재집계하면 같은 (user_id, year_month) 에
      더 큰 revision 으로 다시 발행된다. Work 는 가장 큰 revision 만 최종
      결과로 사용해야 하므로, 유니크 제약으로 한 행만 유지하고
      revision 이 더 클 때만 갱신한다.
    """
    __tablename__ = "monthly_cashflow"

    id           : Mapped[int]      = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id      : Mapped[int]      = mapped_column(BigInteger, nullable=False)
    year_month   : Mapped[str]      = mapped_column(String(7), nullable=False)   # "2026-09"
    revision     : Mapped[int]      = mapped_column(Integer, nullable=False, default=0)

    total_income : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_expense: Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_transfer: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    created_at   : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at   : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "year_month", name="uq_monthly_user_ym"),
        Index("idx_monthly_user", "user_id"),
    )


class CategoryCashflow(Base):
    """
    Asset 월별 카테고리 집계 (소비패턴 별명 · Recap 원천)

    토픽: moaje.asset.category-cashflow-aggregated
    revision 처리 방식은 MonthlyCashflow 와 동일하다.
    """
    __tablename__ = "category_cashflow"

    id           : Mapped[int]      = mapped_column(BigInteger, primary_key=True, default=generate_tsid)
    user_id      : Mapped[int]      = mapped_column(BigInteger, nullable=False)
    year_month   : Mapped[str]      = mapped_column(String(7), nullable=False)
    category_code: Mapped[str]      = mapped_column(String(50), nullable=False)
    revision     : Mapped[int]      = mapped_column(Integer, nullable=False, default=0)

    amount       : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tx_count     : Mapped[int]      = mapped_column(Integer, nullable=False, default=0)

    created_at   : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at   : Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "year_month", "category_code", name="uq_category_user_ym_cat"),
        Index("idx_category_user", "user_id"),
    )
