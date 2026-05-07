from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel, Field
from app.models.spending import EventType, AnalysisType


class BaseResponse(BaseModel):
    success: bool = True
    message: str  = "ok"


class SpendingProfileResponse(BaseModel):
    user_id             : int
    avg_daily_amount    : Decimal
    peak_spend_hour     : int
    top_category        : str
    risk_score_baseline : Decimal
    last_analyzed_at    : datetime
    model_config = {"from_attributes": True}


class DailyLimitRequest(BaseModel):
    user_id           : int     = Field(..., description="사용자 ID")
    current_balance   : Decimal = Field(..., ge=0, description="현재 잔고")
    expected_income   : Decimal = Field(default=0, ge=0, description="예상 알바비")
    fixed_expenses    : Decimal = Field(default=0, ge=0, description="고정 지출")
    days_until_payday : int     = Field(..., ge=1, le=31, description="월급날까지 남은 일수")
    event_buffer      : Decimal = Field(default=0, ge=0, description="학사 이벤트 예비비")


class DailyLimitResponse(BaseResponse):
    user_id       : int
    daily_limit   : Decimal
    advice        : str
    formula_detail: dict


class AcademicScheduleCreate(BaseModel):
    user_id              : int       = Field(..., description="사용자 ID")
    event_type           : EventType
    event_name           : str       = Field(..., max_length=100)
    start_date           : date
    end_date             : date
    expected_extra_spend : Decimal   = Field(default=0, ge=0)


class AcademicScheduleResponse(BaseModel):
    id                   : int
    user_id              : int
    event_type           : EventType
    event_name           : str
    start_date           : datetime
    end_date             : datetime
    expected_extra_spend : Decimal
    model_config = {"from_attributes": True}
