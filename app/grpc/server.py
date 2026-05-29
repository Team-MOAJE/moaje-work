"""
gRPC 서버 구현
Work Service가 제공하는 RPC:
  - GetDailyBudget  : Asset → Work 일일 가용 생활비 요청
  - CheckBlacklist  : Gateway → Work 블랙리스트 확인
"""
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from grpc import aio

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ai.spending_service import SpendingAnalysisService
from app.services.fds.detector import FdsDetector
from app.schemas.spending import DailyLimitRequest

from app.grpc import work_service_pb2, work_service_pb2_grpc

logger = logging.getLogger(__name__)

GRPC_PORT = 50051


class WorkServicer(work_service_pb2_grpc.WorkServiceServicer):
    """
    Work gRPC 서비스 구현체
    proto: moaje-grpc-contracts/proto/grpc/work_service.proto
    """

    async def GetDailyBudget(self, request, context):
        """
        일일 가용 생활비 산출
        Asset 서비스에서 호출

        Daily_Limit = (현재 잔고 + 예상 알바비 - 고정 지출 - 이벤트 버퍼)
                      ÷ 월급날까지 남은 일수
        """
        logger.info(f"📥 gRPC GetDailyBudget 요청 | user_id={request.user_id}")

        try:
            async with AsyncSessionLocal() as session:
                service = SpendingAnalysisService(session)

                # 학사 이벤트 버퍼 자동 계산
                event_buffer = await service.get_event_buffer(int(request.user_id))

                req = DailyLimitRequest(
                    user_id           = int(request.user_id),
                    current_balance   = Decimal(request.current_balance),
                    expected_income   = Decimal(request.expected_income),
                    fixed_expenses    = Decimal(request.fixed_expenses),
                    days_until_payday = request.days_until_payday,
                    event_buffer      = event_buffer,
                )

                result = await service.calculate_daily_limit(req)
                await session.commit()

            logger.info(
                f"📤 gRPC GetDailyBudget 응답 | user_id={request.user_id} "
                f"| daily_limit={result.daily_limit}"
            )

            return work_service_pb2.GetDailyBudgetResponse(
                transaction_id = request.transaction_id,
                user_id        = request.user_id,
                daily_limit    = str(result.daily_limit),
                advice         = result.advice,
                timestamp      = int(datetime.now(timezone.utc).timestamp() * 1000),
            )

        except Exception as e:
            logger.error(f"❌ gRPC GetDailyBudget 오류 | user_id={request.user_id} | {e}")
            await context.abort(aio.StatusCode.INTERNAL, str(e))

    async def CheckBlacklist(self, request, context):
        """
        FDS 블랙리스트 확인
        Gateway에서 호출 → 차단 여부 반환
        """
        logger.info(f"📥 gRPC CheckBlacklist 요청 | user_id={request.user_id}")

        try:
            async with AsyncSessionLocal() as session:
                detector   = FdsDetector(session)
                is_blocked = await detector.get_blacklist_status(int(request.user_id))

            reason = "FDS 이상거래 탐지로 인한 차단" if is_blocked else ""
            logger.info(
                f"📤 gRPC CheckBlacklist 응답 | user_id={request.user_id} "
                f"| is_blocked={is_blocked}"
            )

            return work_service_pb2.CheckBlacklistResponse(
                transaction_id = request.transaction_id,
                is_blocked     = is_blocked,
                reason         = reason,
                timestamp      = int(datetime.now(timezone.utc).timestamp() * 1000),
            )

        except Exception as e:
            logger.error(f"❌ gRPC CheckBlacklist 오류 | user_id={request.user_id} | {e}")
            await context.abort(aio.StatusCode.INTERNAL, str(e))


async def start_grpc_server():
    """gRPC 서버 시작 — 앱 시작 시 백그라운드 태스크로 실행"""
    server = aio.server()
    work_service_pb2_grpc.add_WorkServiceServicer_to_server(WorkServicer(), server)
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    logger.info(f"✅ gRPC 서버 시작 완료 | port={GRPC_PORT}")
    await server.wait_for_termination()
