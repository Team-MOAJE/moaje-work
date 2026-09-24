"""
재무 온보딩 서비스 (My Future)

10문항 답변을 저장하고 Future Profile 을 생성한다.

완료 상태 정합성 (기획안 6절):
  답변의 원천은 Work 이며, 완료 상태가 답변보다 먼저 확정되지 않는다.
  is_completed 는 10문항이 모두 저장된 뒤에만 True 가 되고,
  Auth 로의 플래그 전파는 auth_synced 로 따로 추적해 실패 시 재시도한다.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding import OnboardingAnswer, FutureProfile
from app.services.ai.onboarding_catalog import (
    QUESTIONS, TOTAL_QUESTIONS, PROFILE_FIELD_MAP,
    UNKNOWN, validate_answer, option_label,
)

logger = logging.getLogger(__name__)


class OnboardingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # 답변 저장

    async def save_answer(
        self, user_id: int, question_no: int, answer_code: str, answer_text: str | None = None
    ) -> OnboardingAnswer:
        """문항 하나를 저장한다. 이미 답한 문항이면 갱신한다(재답변 허용)."""
        err = validate_answer(question_no, answer_code, answer_text)
        if err:
            raise ValueError(err)

        result = await self.db.execute(
            select(OnboardingAnswer).where(
                OnboardingAnswer.user_id     == user_id,
                OnboardingAnswer.question_no == question_no,
            )
        )
        answer = result.scalar_one_or_none()

        if answer:
            answer.answer_code = answer_code
            answer.answer_text = answer_text
        else:
            answer = OnboardingAnswer(
                user_id     = user_id,
                question_no = question_no,
                answer_code = answer_code,
                answer_text = answer_text,
            )
            self.db.add(answer)

        await self.db.flush()
        return answer

    async def save_answers(self, user_id: int, answers: list[dict]) -> list[OnboardingAnswer]:
        """여러 문항을 한 번에 저장한다."""
        saved = []
        for a in answers:
            saved.append(await self.save_answer(
                user_id     = user_id,
                question_no = int(a["question_no"]),
                answer_code = a.get("answer_code", UNKNOWN),
                answer_text = a.get("answer_text"),
            ))
        return saved

    # 조회

    async def get_answers(self, user_id: int) -> list[OnboardingAnswer]:
        result = await self.db.execute(
            select(OnboardingAnswer)
            .where(OnboardingAnswer.user_id == user_id)
            .order_by(OnboardingAnswer.question_no)
        )
        return list(result.scalars().all())

    async def get_profile(self, user_id: int) -> FutureProfile | None:
        result = await self.db.execute(
            select(FutureProfile).where(FutureProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    # Future Profile 생성·갱신

    async def rebuild_profile(self, user_id: int) -> FutureProfile:
        """
        저장된 답변으로 Future Profile 을 다시 만든다.
        답변이 정본이므로 답변이 바뀔 때마다 호출한다.
        """
        answers = await self.get_answers(user_id)
        by_no   = {a.question_no: a for a in answers}

        profile = await self.get_profile(user_id)
        if not profile:
            profile = FutureProfile(user_id=user_id)
            self.db.add(profile)
            await self.db.flush()

        # 답변 -> 프로필 컬럼 매핑
        for q_no, field in PROFILE_FIELD_MAP.items():
            a = by_no.get(q_no)
            setattr(profile, field, a.answer_code if a else None)

        # Q9 서술형
        a9 = by_no.get(9)
        profile.success_image = (a9.answer_text if a9 and a9.answer_code != UNKNOWN else None)

        # 진행 상태
        profile.answered_count = len(answers)
        was_completed          = profile.is_completed
        profile.is_completed   = len(answers) >= TOTAL_QUESTIONS

        # 완료 시점은 처음 완료된 순간만 기록
        if profile.is_completed and not was_completed:
            profile.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # 미래 카드 생성 (완료 전에도 부분 생성해 진행감을 준다)
        profile.card_title   = self._build_card_title(by_no)
        profile.card_summary = self._build_card_summary(by_no)

        await self.db.flush()
        logger.info(
            f"✅ Future Profile 갱신 | user={user_id} "
            f"| {profile.answered_count}/{TOTAL_QUESTIONS} | 완료={profile.is_completed}"
        )
        return profile

    # 미래 카드 문구

    @staticmethod
    def _build_card_title(by_no: dict[int, OnboardingAnswer]) -> str:
        """
        답변 조합으로 카드 제목을 만든다.
        미응답·UNKNOWN 은 문구에서 제외해 단정적인 표현을 피한다.
        """
        work = by_no.get(3)
        val  = by_no.get(7)

        work_label = {
            "LARGE_CORP": "안정을 택한",
            "STARTUP"   : "성장을 택한",
            "FREELANCE" : "자유를 택한",
        }.get(work.answer_code if work else "", "")

        val_label = {
            "HIGH_SALARY": "목표를 향해 달리는",
            "WORK_LIFE"  : "균형을 지키는",
        }.get(val.answer_code if val else "", "")

        parts = [p for p in (work_label, val_label) if p]
        if not parts:
            return "미래를 그리는 중"
        return f"{' '.join(parts)} 사회인"

    @staticmethod
    def _build_card_summary(by_no: dict[int, OnboardingAnswer]) -> str:
        """카드 본문. 사용자가 답한 내용만 사용하고, 답하지 않은 항목은 언급하지 않는다."""
        lines = []

        h = by_no.get(2)
        if h and h.answer_code != UNKNOWN:
            lines.append(f"주거: {option_label(2, h.answer_code)}")

        k = by_no.get(8)
        if k and k.answer_code != UNKNOWN:
            lines.append(f"지키고 싶은 소비: {option_label(8, k.answer_code)}")

        c = by_no.get(10)
        if c and c.answer_code != UNKNOWN:
            lines.append(f"먼저 돕고 싶은 것: {option_label(10, c.answer_code)}")

        s = by_no.get(9)
        if s and s.answer_text:
            text = s.answer_text.strip()
            if len(text) > 60:
                text = text[:60] + "…"
            lines.append(f"3년 뒤의 나: {text}")

        if not lines:
            return "질문에 답하면 미래 카드가 채워져요."
        return "\n".join(lines)

    # 진행 상태

    async def get_progress(self, user_id: int) -> dict:
        answers  = await self.get_answers(user_id)
        answered = {a.question_no for a in answers}
        unknown  = {a.question_no for a in answers if a.answer_code == UNKNOWN}

        remaining = [no for no in QUESTIONS if no not in answered]

        return {
            "answered_count"  : len(answers),
            "total_questions" : TOTAL_QUESTIONS,
            "is_completed"    : len(answers) >= TOTAL_QUESTIONS,
            "remaining"       : remaining,
            "unknown_answers" : sorted(unknown),   # 계산 시 기본 가정이 적용될 문항
            "next_question_no": remaining[0] if remaining else None,
        }
