"""
주입 결과 검증 — inject_asset_events.py 를 돌린 뒤 실행한다.

    docker compose exec work-service python scripts/verify_asset_events.py
"""
import sys
from pathlib import Path

# python 은 cwd 가 아니라 스크립트가 있는 폴더를 sys.path 에 넣는다.
# 여기서는 /app/scripts 가 되어 app 패키지를 찾지 못하므로 상위를 직접 올린다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from decimal import Decimal

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.fds import FdsInferenceLog
from app.models.spending import CategoryCashflow, MonthlyCashflow

USER_ID    = 777
YEAR_MONTH = "2026-09"

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(f"  {'✅' if ok else '❌'} {name:<44} {got!r}" + ("" if ok else f"  (기대 {want!r})"))


async def main() -> int:
    async with AsyncSessionLocal() as s:
        print("── 1. 거래 이벤트 (Protobuf 파싱 · user_id string→int · 멱등성) ──")
        n = (await s.execute(
            select(func.count(FdsInferenceLog.id))
            .where(FdsInferenceLog.user_id == USER_ID)
        )).scalar_one()
        check("적재된 거래 건수 (12건 전송 + 1건 재전송)", n, 12)

        amounts = (await s.execute(
            select(FdsInferenceLog.amount)
            .where(FdsInferenceLog.user_id == USER_ID)
            .order_by(FdsInferenceLog.amount)
        )).scalars().all()
        check("최소 금액", Decimal(amounts[0]).quantize(Decimal("1")), Decimal("12000"))
        check("최대 금액", Decimal(amounts[-1]).quantize(Decimal("1")), Decimal("23000"))

        print("\n── 2. 월별 집계 (revision 규칙) ──")
        m = (await s.execute(
            select(MonthlyCashflow).where(
                MonthlyCashflow.user_id == USER_ID,
                MonthlyCashflow.year_month == YEAR_MONTH,
            )
        )).scalar_one_or_none()
        if m is None:
            check("월별 집계 행", None, "존재")
        else:
            check("revision (rev1 → rev1재전송 무시 → rev2 반영)", m.revision, 2)
            check("수입", m.total_income.quantize(Decimal("1")),   Decimal("1250000"))
            check("지출", m.total_expense.quantize(Decimal("1")),  Decimal("845000"))
            check("자금이동", m.total_transfer.quantize(Decimal("1")), Decimal("50000"))
            check("rev=1 재전송의 999 가 반영되지 않음", m.total_income != Decimal("999"), True)

        print("\n── 3. 카테고리 집계 (역순 도착 무시) ──")
        cats = (await s.execute(
            select(CategoryCashflow)
            .where(CategoryCashflow.user_id == USER_ID,
                   CategoryCashflow.year_month == YEAR_MONTH)
            .order_by(CategoryCashflow.amount.desc())
        )).scalars().all()
        check("카테고리 개수", len(cats), 4)
        if cats:
            check("최상위 카테고리", cats[0].category_code, "FOOD")
            check("  금액", cats[0].amount.quantize(Decimal("1")), Decimal("420000"))
            food = next((c for c in cats if c.category_code == "FOOD"), None)
            check("rev=1 역순 도착이 FOOD 를 1원으로 덮지 않음",
                  food.amount.quantize(Decimal("1")) if food else None, Decimal("420000"))
            check("수신되지 않는 tx_count 는 0", {c.tx_count for c in cats}, {0})

        print("\n── 4. Money Recap (별명 판정) ──")
        from app.services.ai.recap_service import RecapService
        svc = RecapService(s)
        tx_in_month = await svc._count_transactions(USER_ID, YEAR_MONTH)
        check("Work 자체 로그에서 센 거래 건수", tx_in_month, 12)

        recap = await svc.get_recap(USER_ID, YEAR_MONTH, must_keep=None)
        if recap is None:
            check("Recap 결과", None, "존재")
        else:
            print(f"     coverage: {recap.coverage.note}")
            check("분석 신뢰 (12건 ≥ 10건, 미분류 17% < 30%)", recap.coverage.is_reliable, True)
            check("  미분류 비중 (금액 기준 145000/845000)",
                  recap.coverage.unclassified_share, Decimal("0.17"))
            check("별명이 붙음", recap.nickname is not None, True)
            if recap.nickname:
                print(f"     별명: {recap.nickname} — {recap.nickname_reason}")
            banned = [w for w in ("낭비", "줄이", "아껴", "과소비")
                      if w in f"{recap.nickname}{recap.nickname_reason}"]
            check("금지 표현 없음", banned, [])

    # 엔진을 닫지 않고 끝내면 aiomysql 커넥션이 __del__ 에서 닫히려다
    # 이미 종료된 이벤트 루프를 건드려 RuntimeError 트레이스백이 찍힌다.
    # 결과에는 영향이 없지만 진짜 오류와 헷갈리므로 명시적으로 정리한다.
    from app.db.session import engine
    await engine.dispose()

    print(f"\n{'=' * 64}\n{sum(results)}/{len(results)} 통과"
          + ("  ✅ 전부 통과" if all(results) else "  ❌ 실패 있음"))
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
