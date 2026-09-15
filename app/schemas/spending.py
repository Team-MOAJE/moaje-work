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


# ── 소비 리포트 카드 스키마 ──────────────────────────

class EventSpendingStat(BaseModel):
    """학사 이벤트별 지출 통계"""
    event_type      : str
    event_name      : str
    start_date      : date
    end_date        : date
    period_days     : int
    avg_daily_spend : Decimal  # 이벤트 기간 일 평균 지출
    total_spend     : Decimal  # 이벤트 기간 총 지출
    vs_normal_ratio : Decimal  # 평소 대비 배율 (1.0 = 평소와 동일)


class FdsSummary(BaseModel):
    """FDS 이상거래 탐지 요약"""
    total_detected  : int
    high_count      : int
    medium_count    : int
    safety_score    : int   # 0~40 (리포트 카드 안전도 만점 기준)
    safety_grade    : str   # A / B / C / D
    top_reason      : str   # 가장 많은 탐지 원인


class SpendingSummary(BaseModel):
    """소비 통계 요약"""
    total_tx_count      : int
    avg_daily_limit     : Decimal
    peak_spend_date     : str
    peak_spend_amount   : Decimal
    lowest_spend_date   : str
    lowest_spend_amount : Decimal
    peak_spend_hour     : int
    top_category        : str

    # ── 리포트 카드 점수 산출용 ──────────────────────
    total_log_days              : int            = 0            # Daily Limit 기록 일수
    daily_limit_compliance_rate : Decimal        = Decimal("0") # Daily Limit > 0 인 날 비율 (0~1)
    daily_limit_cv              : Decimal | None = None         # 변동계수 (소비 규칙성)


class ScoreBreakdown(BaseModel):
    """
    리포트 카드 100점 채점 상세

    안전도       40점 — HIGH -8점, MEDIUM -3점, 블랙리스트 -10점
    Daily Limit  30점 — 준수율 90%↑=30 / 70~89%=22 / 50~69%=15 / 50%↓=7
    이벤트 대비  20점 — 1.2배↓=20 / 1.5배↓=15 / 2.0배↓=10 / 2.0배↑=5
    소비 규칙성  10점 — CV 0.5↓=10 / 0.8↓=7 / 1.2↓=5 / 1.2↑=3
    """
    safety_score      : int  # 만점 40
    daily_limit_score : int  # 만점 30
    event_score       : int  # 만점 20
    regularity_score  : int  # 만점 10
    total             : int  # 0~100


class SemesterReportResponse(BaseModel):
    """학기 소비 리포트 카드"""
    user_id         : int
    year            : int
    semester        : int
    period_label    : str   # "2026년 1학기"
    period_start    : date
    period_end      : date

    spending        : SpendingSummary
    fds             : FdsSummary
    events          : list[EventSpendingStat]

    score_breakdown : ScoreBreakdown   # 항목별 점수 상세
    overall_grade   : str   # A / B / C / D
    overall_score   : int   # 0~100
    summary_message : str   # 한 줄 총평
    badges          : list[str]

    model_config = {"from_attributes": True}
