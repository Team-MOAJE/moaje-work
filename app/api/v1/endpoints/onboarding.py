"""
재무 온보딩 API (My Future)

기획안 3절의 10대 온보딩 질문을 제공하고 답변을 저장한다.
답변이 저장될 때마다 Future Profile 을 다시 만든다.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.onboarding import (
    QuestionResponse, QuestionOption,
    AnswerSubmit, AnswerBulkSubmit, AnswerResponse,
    OnboardingProgress, FutureProfileResponse,
)
from app.services.ai.onboarding_catalog import (
    QUESTIONS, TOTAL_QUESTIONS, UNKNOWN, option_label,
)
from app.services.ai.onboarding_service import OnboardingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["재무 온보딩 (My Future)"])


@router.get(
    "/questions",
    response_model=list[QuestionResponse],
    summary="온보딩 문항 목록",
    description="10대 온보딩 질문과 선택지를 반환한다. 모든 선택형 문항은 UNKNOWN('아직 모르겠어요')을 허용한다.",
)
async def list_questions():
    out = []
    for no in sorted(QUESTIONS):
        q = QUESTIONS[no]
        options = [QuestionOption(code=c, label=l) for c, l in q.options.items()]
        if not q.free_text:
            options.append(QuestionOption(code=UNKNOWN, label="아직 모르겠어요"))
        out.append(QuestionResponse(
            question_no = q.no,
            phase       = q.phase,
            text        = q.text,
            purpose     = q.purpose,
            free_text   = q.free_text,
            options     = options,
        ))
    return out


@router.post(
    "/{user_id}/answers",
    response_model=OnboardingProgress,
    summary="답변 저장 (여러 문항)",
    description="문항 여러 개를 한 번에 저장한다. 이미 답한 문항은 갱신된다(재답변 허용).",
)
async def submit_answers(
    body: AnswerBulkSubmit,
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    service = OnboardingService(db)
    try:
        await service.save_answers(
            user_id=user_id,
            answers=[a.model_dump() for a in body.answers],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await service.rebuild_profile(user_id)
    progress = await service.get_progress(user_id)
    return OnboardingProgress(user_id=user_id, **progress)


@router.post(
    "/{user_id}/answers/{question_no}",
    response_model=OnboardingProgress,
    summary="답변 저장 (문항 하나)",
    description="카드형 UI 에서 한 문항씩 답할 때 사용한다.",
)
async def submit_single_answer(
    body: AnswerSubmit,
    user_id: int     = Path(..., description="사용자 ID"),
    question_no: int = Path(..., ge=1, le=TOTAL_QUESTIONS),
    db: AsyncSession = Depends(get_db),
):
    service = OnboardingService(db)
    try:
        await service.save_answer(
            user_id     = user_id,
            question_no = question_no,
            answer_code = body.answer_code,
            answer_text = body.answer_text,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await service.rebuild_profile(user_id)
    progress = await service.get_progress(user_id)
    return OnboardingProgress(user_id=user_id, **progress)


@router.get(
    "/{user_id}/answers",
    response_model=list[AnswerResponse],
    summary="내 답변 목록",
)
async def get_answers(
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    service = OnboardingService(db)
    answers = await service.get_answers(user_id)
    return [
        AnswerResponse(
            question_no  = a.question_no,
            answer_code  = a.answer_code,
            answer_label = option_label(a.question_no, a.answer_code),
            answer_text  = a.answer_text,
            answered_at  = a.answered_at,
        )
        for a in answers
    ]


@router.get(
    "/{user_id}/progress",
    response_model=OnboardingProgress,
    summary="온보딩 진행 상태",
)
async def get_progress(
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    service  = OnboardingService(db)
    progress = await service.get_progress(user_id)
    return OnboardingProgress(user_id=user_id, **progress)


@router.get(
    "/{user_id}/profile",
    response_model=FutureProfileResponse,
    summary="Future Profile (미래 카드)",
    description="온보딩 답변으로 만든 미래 카드. 답변이 없으면 404.",
)
async def get_future_profile(
    user_id: int = Path(..., description="사용자 ID"),
    db: AsyncSession = Depends(get_db),
):
    service = OnboardingService(db)
    profile = await service.get_profile(user_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="아직 온보딩을 시작하지 않았습니다.",
        )
    return profile
