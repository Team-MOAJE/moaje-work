from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# 문항 조회

class QuestionOption(BaseModel):
    code : str
    label: str


class QuestionResponse(BaseModel):
    question_no : int
    phase       : str
    text        : str
    purpose     : str
    free_text   : bool
    options     : list[QuestionOption]


# 답변 저장

class AnswerSubmit(BaseModel):
    question_no : int = Field(..., ge=1, le=10)
    answer_code : str = Field(default="UNKNOWN", max_length=50,
                              description="선택지 코드. 모르면 UNKNOWN")
    answer_text : Optional[str] = Field(default=None, description="서술형 문항의 본문")


class AnswerBulkSubmit(BaseModel):
    user_id : int
    answers : list[AnswerSubmit] = Field(..., min_length=1)


class AnswerResponse(BaseModel):
    question_no : int
    answer_code : str
    answer_label: str
    answer_text : Optional[str] = None
    answered_at : datetime
    model_config = {"from_attributes": True}


# 진행 상태

class OnboardingProgress(BaseModel):
    user_id          : int
    answered_count   : int
    total_questions  : int
    is_completed     : bool
    remaining        : list[int]
    unknown_answers  : list[int] = Field(
        default_factory=list,
        description="'아직 모르겠어요'로 답한 문항. 계산 시 기본 가정이 적용된다."
    )
    next_question_no : Optional[int] = None


# Future Profile

class FutureProfileResponse(BaseModel):
    user_id        : int

    status_code        : Optional[str] = None
    housing_type       : Optional[str] = None
    work_env           : Optional[str] = None
    value_priority     : Optional[str] = None
    must_keep_category : Optional[str] = None
    money_concern      : Optional[str] = None
    success_image      : Optional[str] = None

    card_title     : Optional[str] = None
    card_summary   : Optional[str] = None

    answered_count : int
    is_completed   : bool
    completed_at   : Optional[datetime] = None

    model_config = {"from_attributes": True}
