from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, DateTime, Numeric, Enum, Index, String, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
import enum
from app.db.session import Base


class RiskLevel(str, enum.Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class BlacklistReason(str, enum.Enum):
    ABNORMAL_AMOUNT = "ABNORMAL_AMOUNT"
    ABNORMAL_TIME   = "ABNORMAL_TIME"
    RAPID_REPEAT    = "RAPID_REPEAT"
    MANUAL          = "MANUAL"


# ── FDS 탐지 로그 ─────────────────────────────────
class FdsInferenceLog(Base):
    __tablename__ = "fds_inference_log"

    id             : Mapped[int]       = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id        : Mapped[int]       = mapped_column(BigInteger, nullable=False)
    transaction_id : Mapped[str]       = mapped_column(String(100), nullable=False)
    amount         : Mapped[Decimal]   = mapped_column(Numeric(18, 4), nullable=False)
    merchant       : Mapped[str]       = mapped_column(String(100), nullable=True)
    risk_score     : Mapped[Decimal]   = mapped_column(Numeric(5, 4), nullable=False)
    risk_level     : Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), nullable=False)
    reason_code    : Mapped[str]       = mapped_column(String(200), nullable=True)
    is_alerted     : Mapped[bool]      = mapped_column(default=False, nullable=False)
    created_at     : Mapped[datetime]  = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_fds_user_id", "user_id"),
        Index("idx_fds_risk_level", "risk_level"),
    )


# ── FDS 블랙리스트 ────────────────────────────────
class FdsBlacklist(Base):
    __tablename__ = "fds_blacklist"

    id            : Mapped[int]             = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id       : Mapped[int]             = mapped_column(BigInteger, nullable=False)
    reason        : Mapped[BlacklistReason] = mapped_column(Enum(BlacklistReason), nullable=False)
    description   : Mapped[str]            = mapped_column(String(300), nullable=True)
    is_active     : Mapped[bool]           = mapped_column(default=True, nullable=False)
    registered_at : Mapped[datetime]       = mapped_column(DateTime, nullable=False, server_default=func.now())
    released_at   : Mapped[datetime]       = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_blacklist_user_id", "user_id"),
        Index("idx_blacklist_active", "is_active"),
    )


# ── ✅ FDS 이상거래 알림 로그 ──────────────────────
class FdsAlertLog(Base):
    """
    FDS HIGH 탐지 시 자동 생성되는 사용자 알림 로그
    프론트엔드가 /api/work/fds/{user_id}/alerts 로 조회하여
    앱 내 알림 표시에 활용합니다.
    """
    __tablename__ = "fds_alert_log"

    id             : Mapped[int]       = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id        : Mapped[int]       = mapped_column(BigInteger, nullable=False)
    transaction_id : Mapped[str]       = mapped_column(String(100), nullable=False)
    amount         : Mapped[Decimal]   = mapped_column(Numeric(18, 4), nullable=False)
    merchant       : Mapped[str]       = mapped_column(String(100), nullable=True)
    risk_level     : Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), nullable=False)
    reason_code    : Mapped[str]       = mapped_column(String(200), nullable=True)
    message        : Mapped[str]       = mapped_column(String(500), nullable=False)
    is_confirmed   : Mapped[bool]      = mapped_column(Boolean, default=False, nullable=False)
    confirmed_at   : Mapped[datetime]  = mapped_column(DateTime, nullable=True)
    created_at     : Mapped[datetime]  = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_alert_user_id", "user_id"),
        Index("idx_alert_confirmed", "is_confirmed"),
    )
