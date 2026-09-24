from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ReadinessItemResponse(BaseModel):
    key    : str
    label  : str
    weight : int = Field(..., description="이 항목의 배점")
    score  : Optional[int] = Field(
        default=None,
        description="획득 점수. null 이면 데이터가 없어 평가에서 제외된 항목"
    )
    detail : str


class ReadinessRequest(BaseModel):
    """
    준비도 계산 입력.

    목표 자금 충족률은 Future Simulator 와 같은 값을 써야 하므로,
    시뮬레이션에 넣었던 값을 그대로 전달한다.
    """
    monthly_income  : Optional[Decimal] = Field(default=None, ge=0)
    monthly_housing : Optional[Decimal] = Field(default=None, ge=0)
    monthly_living  : Optional[Decimal] = Field(default=None, ge=0)
    monthly_leisure : Optional[Decimal] = Field(default=None, ge=0)
    deposit         : Optional[Decimal] = Field(default=None, ge=0)
    move_in_cost    : Optional[Decimal] = Field(default=None, ge=0)
    current_asset   : Optional[Decimal] = Field(
        default=None, ge=0,
        description="현재 자산. 넣지 않으면 Asset 서비스에서 조회한다."
    )


class ReadinessResponse(BaseModel):
    user_id      : int
    percent      : int = Field(..., description="사회인 준비도 0~100")
    level        : int = Field(..., description="1~5 단계")
    level_label  : str

    items        : list[ReadinessItemResponse]
    evaluated    : list[str] = Field(..., description="평가에 반영된 항목")
    excluded     : list[str] = Field(..., description="데이터가 없어 제외된 항목")

    asset_source : str = Field(
        default="INPUT",
        description="현재 자산의 출처. INPUT / ASSET_SERVICE / UNAVAILABLE"
    )
    message      : str
    disclaimer   : str = Field(
        ...,
        description="이 수치의 성격. 개인 평가가 아닌 앱 내부 지표임을 알린다."
    )
