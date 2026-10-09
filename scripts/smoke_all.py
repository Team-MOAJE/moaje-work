"""
Work 전체 기능 점검

등록된 REST 엔드포인트를 전부 호출하고, 인증 가드와 gRPC 서버까지 확인한다.
컨테이너 안에서 자기 자신을 호출하므로 Gateway 없이 돌릴 수 있다.

    docker compose exec work-service python scripts/smoke_all.py

Gateway 를 거치지 않으므로 X-Authenticated-User-Id 를 직접 넣는다.
실제 운영에서는 Gateway 가 JWT 를 검증한 뒤 이 헤더를 주입한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import httpx

BASE = "http://localhost:8084/api/v1/work"
UID  = 777                      # 이벤트 주입 스크립트와 같은 사용자
OTHER = 888                     # 타인 접근 차단 확인용
OPS_TOKEN = "localdev"          # .env 의 WORK_INTERNAL_TOKEN

H     = {"X-Authenticated-User-Id": str(UID)}
H_OPS = {"X-Internal-Token": OPS_TOKEN}

rows = []

HEALTH = "http://localhost:8084/health"


async def wait_for_server(timeout_sec: int = 60) -> bool:
    """
    서버가 요청을 받을 때까지 기다린다.

    docker compose restart 는 컨테이너를 돌려놓고 바로 돌아오지만, 앱은 그 뒤로
    몇 초 더 걸린다(스키마 점검·Kafka 연결). 그 사이에 호출하면 연결 거부가 나고,
    코드가 깨진 것처럼 보인다. 실제로 한 번 그렇게 헷갈렸다.
    """
    import time as _time
    started = _time.monotonic()
    async with httpx.AsyncClient(timeout=3.0) as c:
        while _time.monotonic() - started < timeout_sec:
            try:
                if (await c.get(HEALTH)).status_code < 500:
                    waited = _time.monotonic() - started
                    if waited > 1:
                        print(f"  (서버 기동 대기 {waited:.0f}초)")
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    print(f"❌ 서버가 {timeout_sec}초 안에 뜨지 않았습니다. "
          f"docker compose logs work-service --tail 60 으로 확인하세요.")
    return False



def record(name, ok, detail=""):
    rows.append((name, ok, detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name:<46} {detail}")


async def call(c, method, path, *, headers=None, json_body=None,
               want=200, name=None, show=None):
    label = name or f"{method} {path}"
    try:
        r = await c.request(method, BASE + path, headers=headers, json=json_body)
    except Exception as e:
        record(label, False, f"요청 실패 {type(e).__name__}")
        return None

    wants = want if isinstance(want, (list, tuple)) else [want]
    ok = r.status_code in wants
    detail = str(r.status_code)
    body = None
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            body = r.json()
        except Exception:
            body = None
    if ok and show and isinstance(body, dict):
        bits = [f"{k}={body.get(k)}" for k in show if k in body]
        if bits:
            detail += "  " + " ".join(bits)
    if not ok:
        detail += f" (기대 {wants})"
        if isinstance(body, dict):
            detail += f" {body.get('message') or body.get('detail') or ''}"[:80]
    record(label, ok, detail)
    return body


async def main() -> int:
    if not await wait_for_server():
        return 1

    async with httpx.AsyncClient(timeout=20.0) as c:
        print("── 0. 헬스체크 ──────────────────────────────────")
        r = await c.get("http://localhost:8084/health")
        h = r.json()
        for comp, v in h["components"].items():
            st = v["status"]
            # 인증서는 Infra 발급 대기 중이라 not_mounted 가 정상이다
            ok = st == "ok" or (comp == "asset_grpc_mtls" and st == "not_mounted")
            record(f"components.{comp}", ok, st)

        print("\n── 1. 인증 가드 ─────────────────────────────────")
        await call(c, "GET", f"/spending/{UID}/profile", want=401,
                   name="헤더 없음 → 401")
        await call(c, "GET", f"/spending/{UID}/profile",
                   headers={"X-Authenticated-User-Id": "abc"}, want=401,
                   name="헤더 형식 오류 → 401")
        await call(c, "GET", f"/spending/{OTHER}/profile", headers=H, want=403,
                   name="타인 데이터 → 403")
        await call(c, "GET", "/metrics/retention", headers=H, want=403,
                   name="운영 API 를 사용자 헤더로 → 403")

        print("\n── 2. 공용 조회 (로그인만 필요) ──────────────────")
        await call(c, "GET", "/calendar/universities", headers=H, name="학교 목록")
        qs = await call(c, "GET", "/onboarding/questions", headers=H, name="온보딩 문항")
        record("  문항 10개", isinstance(qs, list) and len(qs) == 10,
               f"{len(qs) if isinstance(qs, list) else '?'}개")
        await call(c, "GET", "/simulator/baselines", headers=H, name="비용 기준값")

        print("\n── 3. 소비 패턴 분석 ────────────────────────────")
        await call(c, "GET", f"/spending/{UID}/profile", headers=H,
                   name="소비 프로필", show=["avg_daily_amount", "peak_spend_hour"])
        before = await call(c, "GET", f"/spending/{UID}/event-buffer", headers=H,
                            name="이벤트 버퍼", show=["event_buffer"])
        buf_before = Decimal(str(before.get("event_buffer", 0))) if isinstance(before, dict) else Decimal(0)
        # 이벤트 버퍼는 '앞으로 7일 이내' 일정만 센다.
        # (spending_service.get_event_buffer — today ~ today+7)
        # 날짜를 고정하면 테스트를 돌리는 날에 따라 창 밖으로 나가므로
        # 항상 사흘 뒤로 잡는다.
        start = date.today() + timedelta(days=3)
        end   = start + timedelta(days=2)
        await call(c, "POST", "/spending/schedule", headers=H, want=201,
                   name=f"학사 일정 등록 ({start} MT)",
                   json_body={"user_id": UID, "event_type": "MT",
                              "event_name": "가을 MT",
                              "start_date": str(start), "end_date": str(end),
                              "expected_extra_spend": 150000})
        await call(c, "GET", f"/spending/{UID}/schedules", headers=H, name="학사 일정 목록")
        # 일정을 등록했으니 버퍼가 0 이 아니어야 한다.
        # 캐시를 지우지 않으면 TTL(1시간) 동안 등록 전 값(0)이 그대로 나온다.
        buf = await call(c, "GET", f"/spending/{UID}/event-buffer", headers=H,
                         name="등록 후 이벤트 버퍼", show=["event_buffer"])
        buf_after = Decimal(str(buf.get("event_buffer", 0))) if isinstance(buf, dict) else Decimal(0)
        # 절대값이 아니라 증가분을 본다. 같은 DB 에 여러 번 돌리면 일정이 쌓여
        # 버퍼도 누적되므로(그게 정상이다) 절대값으로는 판정할 수 없다.
        record("  등록한 예비비 15만원만큼 늘어남 (캐시 무효화)",
               buf_after - buf_before == Decimal("150000"),
               f"{buf_before} → {buf_after}")
        await call(c, "GET", f"/spending/{UID}/report?year=2026&semester=2",
                   headers=H, name="학기 리포트 카드", show=["total_score", "grade"])

        print("\n── 4. 학사 일정 자동 연동 ───────────────────────")
        await call(c, "POST", "/calendar/sync", headers=H,
                   name="학교 일정 동기화", want=[200, 404],
                   json_body={"user_id": UID, "university_id": 1,
                              "year": 2026, "semester": 2},
                   show=["university_name", "synced_count"])
        await call(c, "GET", f"/calendar/{UID}/schedules", headers=H, name="전체 일정 조회")

        print("\n── 5. FDS ───────────────────────────────────────")
        tx = {"user_id": UID, "transaction_id": "SMOKE-001",
              "amount": 480000, "merchant": "테스트", "hour": 3}
        first = await call(c, "POST", "/fds/detect", headers=H, name="이상거래 탐지",
                           json_body=tx, show=["risk_level", "risk_score"])
        # 같은 거래를 다시 보내도 500 이 아니라 같은 판정이 와야 한다.
        # (user_id, transaction_id) 유니크 제약 때문에 예전에는 IntegrityError 로 샜다.
        again = await call(c, "POST", "/fds/detect", headers=H,
                           name="같은 거래 재요청 (멱등)", json_body=tx,
                           show=["risk_level", "risk_score"])
        if isinstance(first, dict) and isinstance(again, dict):
            record("  같은 판정이 돌아옴",
                   (first.get("risk_score"), first.get("risk_level"))
                   == (again.get("risk_score"), again.get("risk_level")),
                   f"{again.get('risk_level')} {again.get('risk_score')}")
        await call(c, "GET", f"/fds/{UID}/logs", headers=H, name="탐지 로그")
        await call(c, "GET", f"/fds/{UID}/alerts", headers=H, name="이상거래 알림",
                   show=["total", "unread"])
        await call(c, "GET", f"/fds/{UID}/blacklist", headers=H, name="블랙리스트 조회",
                   show=["is_blocked"])
        await call(c, "GET", f"/fds/{UID}/safety-score", headers=H,
                   name="소비 안전도", show=["safety_score", "grade"])

        print("\n── 6. 운영용 (내부 토큰) ────────────────────────")
        await call(c, "POST", "/fds/blacklist", headers=H_OPS, want=201,
                   name="블랙리스트 등록",
                   json_body={"user_id": OTHER, "reason": "MANUAL",
                              "description": "점검용"},
                   show=["user_id", "is_active"])
        await call(c, "DELETE", f"/fds/{OTHER}/blacklist", headers=H_OPS,
                   name="블랙리스트 해제")

        print("\n── 7. 재무 온보딩 (My Future) ───────────────────")
        # onboarding_catalog 의 실제 선택지 코드. Q9 는 서술형이다.
        answers = [
            {"question_no": 1,  "answer_code": "STUDENT"},
            {"question_no": 2,  "answer_code": "MONTHLY_RENT"},
            {"question_no": 3,  "answer_code": "LARGE_CORP"},
            {"question_no": 4,  "answer_code": "SAVING"},
            {"question_no": 5,  "answer_code": "HOME_REST"},
            {"question_no": 6,  "answer_code": "COMPARE"},
            {"question_no": 7,  "answer_code": "WORK_LIFE"},
            {"question_no": 8,  "answer_code": "FOOD"},
            {"question_no": 9,  "answer_code": "UNKNOWN",
             "answer_text": "서른쯤엔 작업실 하나 두고 싶다"},
            {"question_no": 10, "answer_code": "NO_PLAN"},
        ]
        await call(c, "POST", f"/onboarding/{UID}/answers", headers=H,
                   name="답변 일괄 제출", json_body={"user_id": UID, "answers": answers},
                   show=["answered", "is_completed"])
        await call(c, "POST", f"/onboarding/{UID}/answers/3", headers=H,
                   name="단일 문항 재답변",
                   json_body={"question_no": 3, "answer_code": "STARTUP"},
                   show=["answered"])
        await call(c, "GET", f"/onboarding/{UID}/answers", headers=H, name="답변 조회")
        await call(c, "GET", f"/onboarding/{UID}/progress", headers=H,
                   name="진행률", show=["answered", "is_completed"])
        await call(c, "GET", f"/onboarding/{UID}/profile", headers=H,
                   name="Future Profile", show=["target_region", "housing_type"])

        print("\n── 8. Future Simulator ──────────────────────────")
        sim_body = {"monthly_income": 2800000, "monthly_housing": 600000,
                    "monthly_living": 700000, "monthly_leisure": 200000,
                    "current_asset": 5000000}
        await call(c, "POST", f"/simulator/{UID}/simulate", headers=H,
                   name="미래 자금 역산", json_body=sim_body,
                   show=["target_amount", "months_to_goal", "asset_source"])
        # monthly_delta 는 필수다. 음수면 절약, 양수면 지출 증가.
        await call(c, "POST", f"/simulator/{UID}/adjust", headers=H,
                   name="조정 시뮬레이션 (월 20만원 절약)",
                   json_body={**sim_body, "monthly_delta": -200000},
                   show=["base_months_to_goal", "adjusted_months_to_goal"])

        print("\n── 9. 사회인 준비도 ─────────────────────────────")
        await call(c, "POST", f"/readiness/{UID}", headers=H,
                   name="준비도 산출", json_body=sim_body,
                   show=["total_score", "grade"])

        print("\n── 10. Money Recap ──────────────────────────────")
        months = await call(c, "GET", f"/recap/{UID}/months", headers=H, name="월 목록")
        await call(c, "GET", f"/recap/{UID}/2026-09", headers=H,
                   name="월별 Recap", show=["nickname", "total_expense"])

        print("\n── 11. gRPC 서버 ────────────────────────────────")
        try:
            import grpc
            from grpc import aio
            from app.grpc import work_service_pb2, work_service_pb2_grpc
            async with aio.insecure_channel("localhost:50051") as ch:
                stub = work_service_pb2_grpc.WorkServiceStub(ch)
                res = await stub.CheckBlacklist(
                    work_service_pb2.CheckBlacklistRequest(
                        transaction_id="SMOKE-GRPC-001",
                        user_id=str(UID),
                        timestamp=0,
                    ),
                    timeout=5.0)
            record("gRPC CheckBlacklist", True, f"is_blocked={res.is_blocked}")
        except Exception as e:
            record("gRPC CheckBlacklist", False, f"{type(e).__name__}: {str(e)[:60]}")

        try:
            from grpc import aio
            from app.grpc import work_service_pb2, work_service_pb2_grpc
            async with aio.insecure_channel("localhost:50051") as ch:
                stub = work_service_pb2_grpc.WorkServiceStub(ch)
                res = await stub.GetDailyBudget(
                    work_service_pb2.GetDailyBudgetRequest(
                        transaction_id="SMOKE-GRPC-002",
                        user_id=str(UID),
                        current_balance="1500000",
                        expected_income="800000",
                        fixed_expenses="500000",
                        days_until_payday=20,
                        timestamp=0,
                    ),
                    timeout=5.0)
            # 학사 이벤트 버퍼가 자동으로 빠지므로 입력만으로는 금액을 단정하지 않는다.
            amount = getattr(res, "daily_limit", None) or getattr(res, "daily_budget", "?")
            record("gRPC GetDailyBudget", True, f"daily_limit={amount}")
        except Exception as e:
            record("gRPC GetDailyBudget", False, f"{type(e).__name__}: {str(e)[:60]}")

        print("\n── 12. 재방문 지표 (활동 이후) ──────────────────")
        # 지표는 온보딩·시뮬레이터·Recap 을 호출한 뒤에 봐야 의미가 있다.
        # 그 전에 부르면 이벤트가 없어 전부 0 으로 나온다.
        m = await call(c, "GET", "/metrics/retention", headers=H_OPS,
                       name="재방문 지표", show=["total_users", "onboarding_completed"])
        if isinstance(m, dict):
            record("  이번 점검 활동이 집계됨", m.get("total_users", 0) >= 1,
                   f"total_users={m.get('total_users')}")
            record("  온보딩 완료가 잡힘", m.get("onboarding_completed", 0) >= 1,
                   f"completed={m.get('onboarding_completed')}")
            record("  표본 부족 경고가 뜸 (10명 미만)", m.get("is_reliable") is False,
                   f"is_reliable={m.get('is_reliable')}")

    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"\n{'=' * 70}")
    print(f"{passed}/{len(rows)} 통과" + ("  ✅ 전부 통과" if passed == len(rows) else "  ❌ 실패 있음"))
    if passed != len(rows):
        print("\n실패 항목:")
        for name, ok, detail in rows:
            if not ok:
                print(f"  - {name}  →  {detail}")

    from app.db.session import engine
    await engine.dispose()
    return 0 if passed == len(rows) else 1


sys.exit(asyncio.run(main()))
