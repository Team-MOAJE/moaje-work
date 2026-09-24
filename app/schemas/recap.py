from typing import Optional

from pydantic import BaseModel, Field


class CategoryShareResponse(BaseModel):
    code     : str
    label    : str
    amount   : str
    tx_count : int
    share    : str = Field(..., description="지출 대비 비중 0~1")


class RecapCoverageResponse(BaseModel):
    """분석 범위. 결과를 어디까지 믿어도 되는지 알린다."""
    total_tx           : int
    classified_tx      : int
    unclassified_share : str
    is_reliable        : bool = Field(
        ...,
        description="false 면 거래가 적거나 미분류 비중이 커서 성향 분석을 하지 않았다."
    )
    note               : str


class RecapResponse(BaseModel):
    user_id        : int
    year_month     : str
    revision       : int = Field(..., description="Asset 집계 버전")

    total_income   : str
    total_expense  : str
    total_transfer : str
    net_saving     : str

    categories     : list[CategoryShareResponse]
    top_category   : Optional[CategoryShareResponse] = None

    nickname        : Optional[str] = Field(
        default=None,
        description="소비패턴 별명. 데이터가 충분할 때만 붙는다."
    )
    nickname_reason : Optional[str] = None

    coverage       : RecapCoverageResponse
    insights       : list[str] = Field(default_factory=list)


class RecapMonthsResponse(BaseModel):
    user_id : int
    months  : list[str] = Field(..., description="Recap 이 있는 월 목록 (최신순)")
