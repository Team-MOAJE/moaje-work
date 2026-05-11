"""
gRPC 서버 구현
Work Service가 제공하는 RPC:
  - GetDailyBudget  : Asset → Work 일일 가용 생활비 요청
  - CheckBlacklist  : Gateway → Work 블랙리스트 확인
"""
import asyncio
import logging
from decimal import Decimal

import grpc
from grpc import aio

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ai.spending_service import SpendingAnalysisService
from app.services.fds.detector import FdsDetector
from app.schemas.spending import DailyLimitRequest

logger = logging.getLogger(__name__)

# gRPC 포트
GRPC_PORT = 50051


class WorkServicer:
    """
    Work gRPC 서비스 구현체

    proto 정의 (moaje-infra/proto/grpc/work_service.proto):
      - GetDailyBudget  (Asset → Work)
      - CheckBlacklist  (Gateway → Work)

    TODO: proto 파일로 코드 자동 생성 후 아래 주석 해제
    grpc 코드 생성 명령어:
      python -m grpc_tools.protoc
        -I proto
        --python_out=app/grpc
        --grpc_python_out=app/grpc
        proto/grpc/work_service.proto
    """

    async def GetDailyBudget(self, request, context):
        """
        일일 가용 생활비 산출
        Asset 서비스에서 호출

        Daily_Limit = (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼)
                      ÷ 월급날까지 남은 일수
        """
        logger.info(f"📥 gRPC GetDailyBudget 요청 | user_id={request.user_id}")

        async with AsyncSessionLocal() as session:
            service = SpendingAnalysisService(session)

            # 학사 이벤트 버퍼 자동 계산
            event_buffer = await service.get_event_buffer(int(request.user_id))

            req = DailyLimitRequest(
                user_id          = int(request.user_id),
                current_balance  = Decimal(request.current_balance),
                expected_income  = Decimal(request.expected_income),
                fixed_expenses   = Decimal(request.fixed_expenses),
                days_until_payday= request.days_until_payday,
                event_buffer     = event_buffer,
            )

            result = await service.calculate_daily_limit(req)
            await session.commit()

        logger.info(f"📤 gRPC GetDailyBudget 응답 | user_id={request.user_id} | daily_limit={result.daily_limit}")

        # TODO: proto 자동 생성 후 실제 response 객체 반환
        # return work_service_pb2.GetDailyBudgetResponse(
        #     transaction_id=request.transaction_id,
        #     user_id=request.user_id,
        #     daily_limit=str(result.daily_limit),
        #     advice=result.advice,
        #     timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
        # )
        return {
            "transaction_id": request.transaction_id,
            "user_id"       : request.user_id,
            "daily_limit"   : str(result.daily_limit),
            "advice"        : result.advice,
        }

    async def CheckBlacklist(self, request, context):
        """
        FDS 블랙리스트 확인
        Gateway에서 호출 → 차단 여부 반환
        """
        logger.info(f"📥 gRPC CheckBlacklist 요청 | user_id={request.user_id}")

        async with AsyncSessionLocal() as session:
            detector = FdsDetector(session)
            is_blocked = await detector.get_blacklist_status(int(request.user_id))

        reason = "FDS 이상거래 탐지로 인한 차단" if is_blocked else ""
        logger.info(f"📤 gRPC CheckBlacklist 응답 | user_id={request.user_id} | is_blocked={is_blocked}")

        # TODO: proto 자동 생성 후 실제 response 객체 반환
        return {
            "transaction_id": request.transaction_id,
            "is_blocked"    : is_blocked,
            "reason"        : reason,
        }


async def start_grpc_server():
    """
    gRPC 서버 시작
    앱 시작 시 백그라운드 태스크로 실행

    TODO: proto 자동 생성 후 아래 주석 해제
    server = aio.server()
    work_service_pb2_grpc.add_WorkServiceServicer_to_server(WorkServicer(), server)
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    await server.wait_for_termination()
    """
    logger.info(f"🚀 gRPC 서버 준비 완료 | port={GRPC_PORT}")
    logger.info("⚠️  proto 코드 자동 생성 후 실제 서버 시작 가능합니다.")

    # proto 생성 전 임시 대기 루프
    while True:
        await asyncio.sleep(3600)
