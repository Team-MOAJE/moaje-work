from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, Numeric, Enum, Index, String, Integer, JSON, SmallInteger, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
import enum
from app.db.session import Base


class EventType(str, enum.Enum):
    EXAM       = "EXAM"
    MT         = "MT"
    FESTIVAL   = "FESTIVAL"
    VACATION   = "VACATION"
    EMPLOYMENT = "EMPLOYMENT"


class AnalysisType(str, enum.Enum):
    DAILY_LIMIT    = "DAILY_LIMIT"
    PATTERN_UPDATE = "PATTERN_UPDATE"
    SCHEDULE_ALERT = "SCHEDULE_ALERT"


class PeriodType(str, enum.Enum):
    WEEKLY  = "WEEKLY"
    MONTHLY = "MONTHLY"


class AiSpendingProfile(Base):
    __tablename__ = "ai_spending_profile"

    id                  : Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id             : Mapped[int]      = mapped_column(BigInteger, nullable=False)
    avg_daily_amount    : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=0)
    # ✅ 개인화 FDS용 표준편차 (Z-score 기반 이상 탐지에 사용)
    std_daily_amount    : Mapped[Decimal]  = mapped_column(Numeric(18, 4), nullable=False, default=15000)
    # ✅ 하이브리드 FDS용 누적 거래 건수 (개인화 비중 자동 조정)
    # 0~10건:  Rule 100%       | 11~30건: Rule 70% + 개인화 30%
    # 31~70건: Rule 30% + 개인화 70% | 70건+: Rule 10% + 개인화 90%
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

    id                   : Mapped[int]       = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id              : Mapped[int]       = mapped_column(BigInteger, nullable=False)
    event_type           : Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    event_name           : Mapped[str]       = mapped_column(String(100), nullable=False)
    start_date           : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    end_date             : Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    expected_extra_spend : Mapped[Decimal]   = mapped_column(Numeric(18, 4), nullable=False, default=0)
    created_at           : Mapped[datetime]  = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_user_date", "user_id", "start_date"),
    )


class AiAnalysisLog(Base):
    __tablename__ = "ai_analysis_log"

    id               : Mapped[int]          = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id          : Mapped[int]          = mapped_column(BigInteger, nullable=False)
    analysis_type    : Mapped[AnalysisType] = mapped_column(Enum(AnalysisType), nullable=False)
    input_snapshot   : Mapped[dict]         = mapped_column(JSON, nullable=False)
    result_message   : Mapped[str]          = mapped_column(String(500), nullable=False)
    daily_limit      : Mapped[Decimal]      = mapped_column(Numeric(18, 4), nullable=True)
    confidence_score : Mapped[Decimal]      = mapped_column(Numeric(5, 4), nullable=False, default=0)
    created_at       : Mapped[datetime]     = mapped_column(DateTime, nullable=False, server_default=func.now())
