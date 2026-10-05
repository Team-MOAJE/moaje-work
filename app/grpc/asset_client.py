"""
Asset gRPC 클라이언트 (mTLS)

Work 는 미래 자금 계산에 '현재 자산'이 필요하다.
기획안 4-A ④ 에 따라 Asset 은 현재 자산 수치만 제공하고,
목표 금액 도출과 Gap 계산은 Work 가 맡는다.

계약: moaje-grpc-contracts/proto/grpc/asset_service.proto
      rpc GetDailyCashflow(GetDailyCashflowRequest)
          returns (GetDailyCashflowResponse)

보안:
  Asset gRPC 서버는 클라이언트 인증서를 요구한다.
  (moaje-asset: AssetGrpcServer.kt — ClientAuth.REQUIRE)
  따라서 평문 연결은 서버가 받지 않으며, Work 도 평문으로 우회하지 않는다.

  Infra 가 발급한 Work 전용 인증서를 /run/grpc 에 읽기 전용으로 마운트하고,
  Work 는 그 파일을 읽어 채널 자격증명을 구성한다.
  인증서 발급과 CA 개인키 보관은 Infra 담당이다.

장애 대응:
  Asset 이 응답하지 않아도 Work 의 시뮬레이션은 계속 동작해야 한다.
  조회에 실패하면 None 을 돌려주고, 호출한 쪽은 사용자가 입력한
  값이나 0 으로 계산을 이어간다. 어느 쪽이었는지는 응답에 표시한다.

  인증서가 없거나 잘못된 경우도 '조회 실패'로 처리한다.
  연결 방식을 낮춰서 성공시키는 선택지는 두지 않는다.
"""
import logging
from decimal import Decimal
from pathlib import Path

import grpc
from grpc import aio

from app.core.config import settings
from app.grpc import asset_service_pb2, asset_service_pb2_grpc
from app.grpc.common import resources_pb2

logger = logging.getLogger(__name__)


class AssetCertificateUnavailable(RuntimeError):
    """mTLS 인증서를 읽을 수 없어 Asset 을 호출할 수 없는 상태."""


# 인증서는 컨테이너 수명 동안 바뀌지 않으므로 성공한 자격증명만 캐시한다.
# 실패는 캐시하지 않는다 — Infra 가 Work 기동 이후에 인증서를 마운트해도
# 다음 호출에서 다시 읽어 정상 동작할 수 있어야 한다.
_credentials: grpc.ChannelCredentials | None = None


def _read_pem(path_str: str, label: str) -> bytes:
    """PEM 파일을 읽는다. 없거나 비어 있으면 호출 자체를 중단시킨다."""
    if not path_str:
        raise AssetCertificateUnavailable(f"{label} 경로가 설정되지 않았습니다.")

    path = Path(path_str)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise AssetCertificateUnavailable(f"{label} 파일이 없습니다. path={path}")
    except PermissionError:
        raise AssetCertificateUnavailable(f"{label} 파일을 읽을 수 없습니다. path={path}")
    except OSError as e:
        raise AssetCertificateUnavailable(f"{label} 파일 읽기 실패. path={path} | {e}")

    if not data.strip():
        raise AssetCertificateUnavailable(f"{label} 파일이 비어 있습니다. path={path}")

    return data


def load_channel_credentials() -> grpc.ChannelCredentials:
    """
    CA · 클라이언트 인증서 · 개인키로 mTLS 자격증명을 만든다.

    root_certificates  — Asset 서버 인증서를 검증할 CA (서버 검증 유지)
    certificate_chain  — Work 가 제시할 클라이언트 인증서
    private_key        — 그 인증서의 개인키

    셋 중 하나라도 없으면 AssetCertificateUnavailable.
    """
    global _credentials
    if _credentials is not None:
        return _credentials

    ca_pem   = _read_pem(settings.ASSET_GRPC_CA_PATH,   "Asset 서버 검증용 CA 인증서")
    cert_pem = _read_pem(settings.WORK_GRPC_CERT_PATH,  "Work 클라이언트 인증서")
    key_pem  = _read_pem(settings.WORK_GRPC_KEY_PATH,   "Work 클라이언트 개인키")

    _credentials = grpc.ssl_channel_credentials(
        root_certificates = ca_pem,
        private_key       = key_pem,
        certificate_chain = cert_pem,
    )
    logger.info("🔐 Asset mTLS 자격증명 구성 완료")
    return _credentials


def certificate_status() -> dict:
    """헬스체크용 — 인증서 마운트 상태를 확인한다. (내용은 노출하지 않는다)"""
    try:
        load_channel_credentials()
        return {"status": "ok"}
    except AssetCertificateUnavailable as e:
        return {"status": "not_mounted", "detail": str(e)}


class AssetClient:
    """
    호출마다 채널을 열고 닫는다.
    Work 의 Asset 호출은 사용자가 화면을 열 때 산발적으로 일어나므로,
    채널을 상주시키는 것보다 단순하고 연결 상태 관리 부담이 없다.
    """

    def __init__(self, target: str | None = None, timeout: float | None = None):
        self.target  = target  or settings.ASSET_GRPC_TARGET
        self.timeout = timeout or settings.GRPC_TIMEOUT_SEC

    def _channel_options(self) -> list[tuple[str, str]]:
        """
        접속 주소와 인증서 SAN 이 다를 때만 검증 대상 호스트명을 바꾼다.
        비워두면 target 의 호스트명으로 서버 인증서를 검증한다. (기본 동작)
        """
        authority = settings.ASSET_GRPC_OVERRIDE_AUTHORITY.strip()
        if not authority:
            return []
        return [("grpc.ssl_target_name_override", authority)]

    async def get_current_balance(self, user_id: int) -> Decimal | None:
        """
        현재 잔액을 조회한다. 실패하면 None.

        GetDailyCashflow 는 Daily Limit 계산용 RPC 라 여러 입력을 받지만,
        Work 가 필요한 것은 응답의 current_balance 뿐이다.
        계산 입력은 0 으로 두고 잔액만 읽는다.
        """
        try:
            credentials = load_channel_credentials()
        except AssetCertificateUnavailable as e:
            # 평문(insecure) 으로 다시 시도하지 않는다.
            # Asset 은 클라이언트 인증서를 요구하므로 평문은 어차피 거절되고,
            # 무엇보다 인증서 누락을 조용히 우회하는 경로를 남기지 않는다.
            logger.error(f"❌ Asset mTLS 인증서 미비로 호출 중단 | user={user_id} | {e}")
            return None

        zero = resources_pb2.Money(amount=0, currency="KRW")

        # 계약의 user_id 는 string 이다. Work 내부는 정수로 다루므로 여기서 변환한다.
        #
        # days_until_next_payday = 0 은 '잔액만 필요하다'는 뜻이다.
        # Work 는 current_balance 만 쓰고 Asset 이 계산한 daily_limit 은 쓰지 않는데,
        # Work 에는 사용자의 다음 수입일 정보가 없다. 없는 값을 지어내 보내면
        # Asset 이 수입 0·지출 0 으로 만든 의미 없는 daily_limit 을 돌려주게 되므로
        # 0 을 그대로 보내고, 0 일 때의 처리는 Asset 쪽 정책에 맡기기로 합의했다.
        # (2026-10-05 Asset 담당자 협의)
        req = asset_service_pb2.GetDailyCashflowRequest(
            user_id                = str(user_id),
            expected_income        = zero,
            fixed_expenses         = zero,
            event_buffer           = zero,
            days_until_next_payday = 0,
            snapshot_date          = "",
            force_refresh          = False,
        )

        try:
            async with aio.secure_channel(
                self.target, credentials, options=self._channel_options()
            ) as channel:
                stub = asset_service_pb2_grpc.AssetServiceStub(channel)
                res  = await stub.GetDailyCashflow(req, timeout=self.timeout)

            balance = Decimal(str(res.current_balance.amount))
            logger.info(
                f"✅ Asset 잔액 조회 | user={user_id} | balance={int(balance):,}원"
            )
            return balance

        except aio.AioRpcError as e:
            # 인증서 불일치·만료는 UNAVAILABLE 로 오며 details 에 TLS 사유가 담긴다.
            # 연결 실패와 구분해 원인을 찾을 수 있도록 code 와 details 를 함께 남긴다.
            logger.warning(
                f"⚠️ Asset 잔액 조회 실패 | user={user_id} "
                f"| target={self.target} | code={e.code().name} | {e.details()}"
            )
            return None
        except Exception as e:
            logger.warning(f"⚠️ Asset 연결 실패 | user={user_id} | {type(e).__name__}: {e}")
            return None
