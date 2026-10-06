"""
데이터가 쌓인 상태에서의 점검

smoke_all.py 는 기능이 '되는지'만 본다. 깨끗한 DB 로 돌리면 전부 통과한다.
그런데 실제로 쓰기 시작하면 표는 계속 커지고, 오늘 통과한 코드가
내년에도 같은 답을 주리라는 보장은 없다. 이 스크립트는 일부러 몇 달치를
먼저 쌓아 넣고, 그 상태에서 답이 맞는지와 응답이 돌아오는지를 본다.

    docker compose exec work-service python scripts/volume_check.py

여기서 쓰는 사용자(VUID)는 이 스크립트 전용이다. 돌릴 때마다 그 사용자
데이터만 지우고 다시 넣으므로, 몇 번 돌려도 결과가 같고 다른 점검을
방해하지 않는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import delete, func, select, text

from app.db.session import AsyncSessionLocal, engine

# APP_ENV=development 면 engine 이 echo=True 로 떠서 쿼리가 전부 찍힌다.
# 점검 결과가 그 사이에 묻히므로 이 스크립트에서는 쿼리 로그만 내린다.
import logging
for _name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
    logging.getLogger(_name).setLevel(logging.WARNING)
from app.models.fds import FdsAlertLog, FdsInferenceLog, RiskLevel
from app.models.spending import (
    AcademicSchedule, AiAnalysisLog, AnalysisType, EventType,
)

BASE = "http://localhost:8084/api/v1/work"
VUID = 7791                     # 이 스크립트 전용 사용자
H    = {"X-Authenticated-User-Id": str(VUID)}

# 쌓을 양 — 한 학기를 꽉 채운 사용자를 가정한다
DAYS_OF_LIMITS   = 120          # 학기 길이만큼
CALLS_PER_DAY    = 4            # 하루에 Daily Limit 을 네 번 눌렀다고 보자
FDS_LOGS         = 1500         # 한 학기 거래 건수
ALERTS           = 150          # 알림 (목록 limit 20 보다 훨씬 많게)
SCHEDULES        = 600          # 몇 해를 쓴 사용자

SEM_START = date(2026, 9, 1)    # 2026-2 학기 (report_service.SEMESTER_PERIODS)
SEM_END   = date(2026, 12, 20)

SLOW_SECONDS = 3.0              # 이보다 느리면 사람이 기다린다고 본다

rows = []


def record(name, ok, detail=""):
    rows.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name:<46} {detail}")


# ── 쌓기 ──────────────────────────────────────────

async def seed() -> None:
    async with AsyncSessionLocal() as db:
        # 이 사용자 것만 비운다
        for model in (AiAnalysisLog, FdsInferenceLog, FdsAlertLog, AcademicSchedule):
            await db.execute(delete(model).where(model.user_id == VUID))
        await db.commit()

        # ① Daily Limit — 하루에 여러 번. '기록 일수'가 호출 횟수에
        #    휘둘리지 않는지 보기 위한 자료다.
        for i in range(DAYS_OF_LIMITS):
            day = SEM_START + timedelta(days=i)
            if day > SEM_END:
                break
            for k in range(CALLS_PER_DAY):
                db.add(AiAnalysisLog(
                    user_id          = VUID,
                    analysis_type    = AnalysisType.DAILY_LIMIT,
                    input_snapshot   = {"seed": True},
                    result_message   = "쌓기용",
                    daily_limit      = Decimal(20000 + (i * 137) % 15000),
                    confidence_score = Decimal("0.9500"),
                    created_at       = datetime.combine(day, datetime.min.time())
                                       + timedelta(hours=9 + k),
                ))

        # ② FDS 탐지 로그
        for i in range(FDS_LOGS):
            day = SEM_START + timedelta(days=i % 110)
            level = (RiskLevel.HIGH if i % 50 == 0
                     else RiskLevel.MEDIUM if i % 7 == 0
                     else RiskLevel.LOW)
            db.add(FdsInferenceLog(
                user_id        = VUID,
                transaction_id = f"VOL-{i:06d}",
                amount         = Decimal(3000 + (i * 91) % 90000),
                merchant       = "쌓기용 가맹점",
                risk_score     = Decimal("0.3000"),
                risk_level     = level,
                reason_code    = f"AMOUNT_SPIKE({i % 9}.{i % 10}배)",
                occurred_at    = datetime.combine(day, datetime.min.time()) + timedelta(hours=12),
                created_at     = datetime.combine(day, datetime.min.time()) + timedelta(hours=12),
            ))

        # ③ 알림
        for i in range(ALERTS):
            db.add(FdsAlertLog(
                user_id        = VUID,
                transaction_id = f"VOL-ALERT-{i:05d}",
                amount         = Decimal(500000),
                merchant       = "쌓기용 가맹점",
                risk_level     = RiskLevel.HIGH,
                reason_code    = "AMOUNT_SPIKE",
                message        = "쌓기용 알림",
                is_confirmed   = (i % 3 == 0),
                created_at     = datetime.now() - timedelta(minutes=i),
            ))

        # ④ 학사 일정 — 여러 해에 걸쳐
        for i in range(SCHEDULES):
            start = date(2022, 1, 1) + timedelta(days=i * 2)
            db.add(AcademicSchedule(
                user_id              = VUID,
                event_type           = EventType.MT if i % 2 else EventType.FESTIVAL,
                event_name           = f"쌓기용 일정 {i}",
                start_date           = datetime.combine(start, datetime.min.time()),
                end_date             = datetime.combine(start + timedelta(days=1), datetime.min.time()),
                expected_extra_spend = Decimal(10000),
                is_auto              = False,
            ))

        await db.commit()

        n_logs = await db.scalar(
            select(func.count()).select_from(AiAnalysisLog).where(AiAnalysisLog.user_id == VUID))
        print(f"  쌓기 완료 — Daily Limit {n_logs}줄 · FDS {FDS_LOGS}줄 · 알림 {ALERTS}줄 · 일정 {SCHEDULES}줄")


# ── 점검 ──────────────────────────────────────────

async def check_index() -> None:
    """
    모델에 넣은 인덱스가 실제 DB 에도 있는지 본다.

    create_all 은 이미 있는 표를 손대지 않는다. 쓰던 DB 라면
    schema_sync 가 채워줘야 하는데, 그게 돌았는지는 DB 를 봐야 안다.
    """
    want = {
        "ai_analysis_log"  : "idx_analysis_user_type_created",
        "fds_inference_log": "idx_fds_user_occurred",
    }
    async with AsyncSessionLocal() as db:
        for table, index in want.items():
            found = await db.scalar(text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
            ).bindparams(t=table, i=index))
            record(f"인덱스 존재 — {table}", bool(found), index if found else "없음 (쓰던 DB 라면 보정 실패)")

        # occurred_at 칸도 같은 이유로 확인한다
        col = await db.scalar(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'fds_inference_log' "
            "AND column_name = 'occurred_at'"
        ))
        record("칸 존재 — fds_inference_log.occurred_at", bool(col), "있음" if col else "없음")


async def timed(c, method, path, **kw):
    t0 = time.perf_counter()
    r = await c.request(method, BASE + path, headers=H, **kw)
    return r, time.perf_counter() - t0


async def main() -> int:
    print("── 0. 데이터 쌓기 ───────────────────────────────")
    await seed()

    print("\n── 1. 스키마가 실제 DB 에 반영됐는지 ────────────")
    await check_index()

    async with httpx.AsyncClient(timeout=60) as c:
        print("\n── 2. 학기 리포트 (하루 4번 누른 120일) ─────────")
        r, dt = await timed(c, "GET", f"/spending/{VUID}/report?year=2026&semester=2")
        ok = r.status_code == 200
        record("리포트 조회", ok, f"{r.status_code}  {dt:.2f}초")
        if ok:
            b = r.json()
            days = b["spending"]["total_log_days"]
            # 핵심: 호출 횟수(480)가 아니라 날짜 수(120)여야 한다.
            # 줄 단위로 세면 '기록 일수'가 호출 횟수만큼 불어나고
            # 준수율·변동계수도 많이 누른 날로 끌려간다.
            expected = min(DAYS_OF_LIMITS, (SEM_END - SEM_START).days + 1)
            record("  기록 일수가 날짜 수와 같음", days == expected,
                   f"{days}일 (기대 {expected}, 넣은 줄 {expected * CALLS_PER_DAY})")
            record("  호출 횟수로 부풀지 않음", days < expected * CALLS_PER_DAY, f"{days}")
            record("  FDS 집계가 들어옴", b["fds"]["total_detected"] > 0,
                   f"total_detected={b['fds']['total_detected']}")
            record("  리포트가 느려지지 않음", dt < SLOW_SECONDS, f"{dt:.2f}초 (기준 {SLOW_SECONDS}초)")

        print("\n── 3. 알림 — 목록은 잘라도 합계는 진짜 ──────────")
        r, dt = await timed(c, "GET", f"/fds/{VUID}/alerts?limit=20")
        ok = r.status_code == 200
        record("알림 조회", ok, f"{r.status_code}  {dt:.2f}초")
        if ok:
            b = r.json()
            unconfirmed = ALERTS - len(range(0, ALERTS, 3))
            record("  total 이 limit 에 안 잘림", b.get("total") == ALERTS,
                   f"total={b.get('total')} (기대 {ALERTS})")
            record("  unread 도 전체 기준", b.get("unread") == unconfirmed,
                   f"unread={b.get('unread')} (기대 {unconfirmed})")
            record("  목록은 limit 만큼만", len(b.get("alerts", [])) == 20,
                   f"{len(b.get('alerts', []))}건")

        print("\n── 4. limit 상한 ────────────────────────────────")
        for path, label in (
            (f"/fds/{VUID}/logs?limit=100000",   "탐지 로그 limit=100000 → 422"),
            (f"/fds/{VUID}/alerts?limit=100000", "알림 limit=100000 → 422"),
        ):
            r = await c.get(BASE + path, headers=H)
            record(label, r.status_code == 422, str(r.status_code))

        print("\n── 5. 일정 목록 (600건 쌓인 사용자) ─────────────")
        r, dt = await timed(c, "GET", f"/spending/{VUID}/schedules")
        ok = r.status_code == 200
        record("일정 목록 기본 조회", ok, f"{r.status_code}  {dt:.2f}초")
        if ok:
            n = len(r.json())
            record("  기본 상한 안에서 돌아옴", n <= 200, f"{n}건")
        r = await c.get(BASE + f"/spending/{VUID}/schedules?year=2024", headers=H)
        if r.status_code == 200:
            picked = r.json()
            years  = {str(s["start_date"])[:4] for s in picked}
            record("  연도 필터가 그 해만 집음", bool(picked) and years == {"2024"},
                   f"{len(picked)}건 · 연도={sorted(years) or '없음'}")
        r = await c.get(BASE + f"/spending/{VUID}/schedules?limit=9999", headers=H)
        record("  일정 limit=9999 → 422", r.status_code == 422, str(r.status_code))

        print("\n── 6. 다른 학기 자동 일정이 살아남는지 ──────────")
        await check_resync_keeps_other_semester(c)

    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"\n{'=' * 70}")
    print(f"{passed}/{len(rows)} 통과" + ("  ✅ 전부 통과" if passed == len(rows) else "  ❌ 실패 있음"))
    if passed != len(rows):
        print("\n실패 항목:")
        for name, ok, detail in rows:
            if not ok:
                print(f"  - {name}  →  {detail}")

    await engine.dispose()
    return 0 if passed == len(rows) else 1


async def check_resync_keeps_other_semester(c) -> None:
    """
    학기를 다시 동기화할 때, 다른 학기 자동 일정이 남아 있어야 한다.

    전에는 is_auto=True 를 전부 지우고 새로 넣었다. 그러면 2학기를
    동기화하는 순간 1학기 자동 일정이 사라지고, 지난 학기 리포트는
    '이벤트 없음'이 된다. 깨끗한 DB 로는 한 번도 안 드러나는 문제다.
    """
    marker = datetime(2025, 3, 10)
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(AcademicSchedule).where(
                AcademicSchedule.user_id == VUID,
                AcademicSchedule.is_auto == True,  # noqa: E712
            )
        )
        db.add(AcademicSchedule(
            user_id              = VUID,
            event_type           = EventType.MIDTERM,
            event_name           = "지난 학기 자동 일정",
            start_date           = marker,
            end_date             = marker + timedelta(days=4),
            expected_extra_spend = Decimal(50000),
            is_auto              = True,
        ))
        await db.commit()

    r = await c.post(BASE + "/calendar/sync", headers=H, json={
        "user_id": VUID, "university_id": 1, "year": 2026, "semester": 2,
    })
    if r.status_code == 404:
        record("재동기화 (학사 일정 미등록으로 건너뜀)", True, "404 — 마스터 데이터 없음")
        return
    if r.status_code != 200:
        record("재동기화", False, f"{r.status_code}")
        return
    record("재동기화", True, f"synced_count={r.json().get('synced_count')}")

    async with AsyncSessionLocal() as db:
        still = await db.scalar(
            select(func.count()).select_from(AcademicSchedule).where(
                AcademicSchedule.user_id    == VUID,
                AcademicSchedule.is_auto    == True,  # noqa: E712
                AcademicSchedule.start_date == marker,
            )
        )
    record("  지난 학기 자동 일정이 살아있음", still == 1,
           "남아있음" if still else "삭제됨 — 다른 학기까지 지우고 있다")


sys.exit(asyncio.run(main()))
