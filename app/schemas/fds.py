from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field
from app.models.fds import RiskLevel, BlacklistReason


# ── 탐지 요청 ─────────────────────────────────────
class FdsDetectRequest(BaseModel):
    user_id        : int     = Field(..., description="사용자 ID")
    transaction_id : str     = Field(..., description="거래 고유 ID")
    amount         : Decimal = Field(..., ge=0, description="거래 금액")
    merchant       : str     = Field(default="", description="가맹점명")
    hour           : int     = Field(..., ge=0, le=23, description="거래 발생 시간 (0~23)")


# ── 탐지 결과 응답 ────────────────────────────────
class FdsDetectResponse(BaseModel):
    user_id        : int
    transaction_id : str
    risk_score     : Decimal
    risk_level     : RiskLevel
    reason_code    : str
    is_alerted     : bool
    message        : str


# ── 블랙리스트 등록 요청 ──────────────────────────
class BlacklistCreateRequest(BaseModel):
    user_id     : int              = Field(..., description="차단할 사용자 ID")
    reason      : BlacklistReason
    description : str              = Field(default="", description="차단 사유 설명")


# ── 블랙리스트 응답 ───────────────────────────────
class BlacklistResponse(BaseModel):
    id            : int
    user_id       : int
    reason        : BlacklistReason
    description   : str | None
    is_active     : bool
    registered_at : datetime
    released_at   : datetime | None
    model_config = {"from_attributes": True}
