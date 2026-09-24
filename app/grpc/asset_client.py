"""
Asset gRPC 클라이언트

Work 는 미래 자금 계산에 '현재 자산'이 필요하다.
기획안 4-A ④ 에 따라 Asset 은 현재 자산 수치만 제공하고,
목표 금액 도출과 Gap 계산은 Work 가 맡는다.

계약: moaje-grpc-contracts/proto/grpc/asset_service.proto
      rpc GetDailyCashflow(GetDailyCashflowRequest)
          returns (GetDailyCashflowResponse)

장애 대응:
  Asset 이 응답하지 않아도 Work 의 시뮬레이션은 계속 동작해야 한다.
  조회에 실패하면 None 을 돌려주고, 호출한 쪽은 사용자가 입력한
  값이나 0 으로 계산을 이어간다. 어느 쪽이었는지는 응답에 표시한다.
"""
import logging
from decimal import Decimal

import grpc
from grpc import aio

from app.core.config import settings
from app.grpc import asset_service_pb2, asset_service_pb2_grpc
from app.grpc.common import resources_pb2

logger = logging.getLogger(__name__)


class AssetClient:
    """
    호출마다 채널을 열고 닫는다.
    Work 의 Asset 호출은 사용자가 화면을 열 때 산발적으로 일어나므로,
    채널을 상주시키는 것보다 단순하고 연결 상태 관리 부담이 없다.
    """

    def __init__(self, target: str | None = None, timeout: float | None = None):
        self.target  = target  or settings.ASSET_GRPC_TARGET
        self.timeout = timeout or settings.GRPC_TIMEOUT_SEC

    async def get_current_balance(self, user_id: int) -> Decimal | None:
        """
        현재 잔액을 조회한다. 실패하면 None.

        GetDailyCashflow 는 Daily Limit 계산용 RPC 라 여러 입력을 받지만,
        Work 가 필요한 것은 응답의 current_balance 뿐이다.
        계산 입력은 0 으로 두고 잔액만 읽는다.
        """
        zero = resources_pb2.Money(amount=0, currency="KRW")

        req = asset_service_pb2.GetDailyCashflowRequest(
            user_id                = user_id,
            expected_income        = zero,
            fixed_expenses         = zero,
            event_buffer           = zero,
            days_until_next_payday = 0,
            snapshot_date          = "",
            force_refresh          = False,
        )

        try:
            async with aio.insecure_channel(self.target) as channel:
                stub = asset_service_pb2_grpc.AssetServiceStub(channel)
                res  = await stub.GetDailyCashflow(req, timeout=self.timeout)

            balance = Decimal(str(res.current_balance.amount))
            logger.info(
                f"✅ Asset 잔액 조회 | user={user_id} | balance={int(balance):,}원"
            )
            return balance

        except aio.AioRpcError as e:
            logger.warning(
                f"⚠️ Asset 잔액 조회 실패 | user={user_id} "
                f"| code={e.code().name} | {e.details()}"
            )
            return None
        except Exception as e:
            logger.warning(f"⚠️ Asset 연결 실패 | user={user_id} | {e}")
            return None
