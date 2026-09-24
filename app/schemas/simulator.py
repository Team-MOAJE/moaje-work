from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class BaselineInfo(BaseModel):
    """계산에 쓰인 기준값의 근거"""
    key    : str
    label  : str
    amount : str
    kind   : str = Field(..., description="STATISTIC / DERIVED / ASSUMPTION")
    source : str
    as_of  : str
    scope  : str
    url    : str = ""


class SimulateRequest(BaseModel):
    """
    시뮬레이션 입력.

    값을 넣지 않으면 온보딩 답변과 기준값으로 계산하며,
    가정값을 쓴 항목은 응답의 assumed_keys 에 표시된다.
    """
    monthly_income  : Optional[Decimal] = Field(default=None, ge=0, description="세후 월수입")
    monthly_housing : Optional[Decimal] = Field(default=None, ge=0, description="월 주거비")
    monthly_living  : Optional[Decimal] = Field(default=None, ge=0, description="월 기본 생활비")
    monthly_leisure : Optional[Decimal] = Field(default=None, ge=0, description="월 여가비")
    deposit         : Optional[Decimal] = Field(default=None, ge=0, description="보증금")
    move_in_cost    : Optional[Decimal] = Field(default=None, ge=0, description="초기 정착 비용")
    current_asset   : Decimal           = Field(default=Decimal("0"), ge=0,
                                                description="현재 자산. 미입력 시 0")


class TargetBreakdown(BaseModel):
    deposit        : str
    move_in_cost   : str
    emergency_fund : str
    total          : str


class CashflowBreakdown(BaseModel):
    income  : str
    housing : str
    living  : str
    leisure : str
    expense : str
    savable : str


class SimulateResponse(BaseModel):
    user_id        : int

    target         : TargetBreakdown
    cashflow       : CashflowBreakdown

    current_asset  : str
    gap            : str
    months_to_goal : Optional[int] = Field(
        default=None,
        description="달성까지 남은 개월 수. 월 저축 가능액이 0 이하면 null"
    )
    progress_rate  : str = Field(..., description="목표 대비 진행률 0~1")

    assumed_keys   : list[str] = Field(
        default_factory=list,
        description="사용자가 입력하지 않아 가정값으로 계산한 항목"
    )
    baselines      : list[BaselineInfo] = Field(
        default_factory=list,
        description="계산에 쓰인 기준값의 출처·기준일·범위"
    )
    advice         : list[str] = Field(default_factory=list)


class AdjustRequest(SimulateRequest):
    """조건부 시뮬레이션 — 월 지출을 조정했을 때의 변화"""
    monthly_delta : Decimal = Field(
        ...,
        description="월 지출 변화량. 음수면 절약, 양수면 지출 증가"
    )


class AdjustResponse(BaseModel):
    user_id                  : int
    monthly_delta            : str
    base_monthly_savable     : str
    adjusted_monthly_savable : str
    base_months_to_goal      : Optional[int] = None
    adjusted_months_to_goal  : Optional[int] = None
    months_saved             : Optional[int] = Field(
        default=None,
        description="단축된 개월 수. 양수면 앞당겨짐"
    )
    message                  : str
