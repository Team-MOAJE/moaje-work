from pydantic import BaseModel, Field


class QuestionDropoffResponse(BaseModel):
    question_no  : int
    answered     : int = Field(..., description="이 문항에 답한 사용자 수")
    dropoff_rate : str = Field(..., description="다음 문항으로 넘어가지 않은 비율 0~1")


class MetricsResponse(BaseModel):
    period_days          : int
    total_users          : int = Field(..., description="기간 내 기능을 쓴 사용자 수")

    onboarding_started   : int
    onboarding_completed : int
    completion_rate      : str = Field(..., description="온보딩 완료율 0~1")
    question_dropoff     : list[QuestionDropoffResponse]

    simulator_users      : int
    simulator_calls      : int
    simulator_reuse_rate : str = Field(..., description="2회 이상 사용한 사용자 비율 0~1")

    recap_users          : int
    recap_views          : int
    recap_repeat_rate    : str = Field(
        ..., description="서로 다른 달을 2개 이상 본 사용자 비율 0~1"
    )

    is_reliable : bool = Field(
        ..., description="false 면 표본이 적어 비율을 일반화하기 어렵다"
    )
    note        : str
    warnings    : list[str] = Field(default_factory=list)
