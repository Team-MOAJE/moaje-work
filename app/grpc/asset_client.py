"""
Asset gRPC 클라이언트 (mTLS)

Work 는 미래 자금 계산에 '현재 자산'이 필요하다.
기획안 4-A ④ 에 따라 Asset 은 현재 자산 수치만 제공하고,
목표 금액 도출과 Gap 계산은 Work 가 맡는다.

계약: moaje-grpc-contracts/proto/grpc/asset_service.proto
      rpc GetCurrentBalance(GetCurrentBalanceRequest)
          returns (GetCurrentBalanceResponse)

      전에는 GetDailyCashflow 를 불러 응답의 current_balance 만 꺼내 썼다.
      그 RPC 는 하루 예산 계산용이라 days_until_next_payday 가 필요한데,
      Work 에는 사용자의 다음 수입일 정보가 없어 0 을 보내고 있었고
      Asset 은 일수 0 을 거절한다. 잔액만 돌려주는 RPC 가 따로 생겨
      그쪽으로 옮겼다. (2026-10-08 Asset 담당자 반영)

      Work 는 GetDailyCashflow 를 더 이상 쓰지 않는다.

보안:
  Asset gRPC 서버는 클라이언트 인증서를 요구한다.
  (moaje-asset: AssetGrpcServer.kt — ClientAuth.REQUIRE)
  따라서 평문 연결은 서버가 받지 않으며, Work 도 평문으로 우회하지 않는다.

  Infra 가 발급한 Work 전용 인증서를 /run/grpc 에 읽기 전용으로 마운트하고,
  Work 는 그 파일을 읽어 채널 자격증명을 구성한다.
  인증서 발급과 CA 개인키 보관은 Infra 담당이다.

장애 대응:
  Asset 이 응답하지 않아도 Work 의 시뮬레이션은 계속 동작해야 한다.
  조회에 실패하면 0 으로 계산을 이어가고, 어느 쪽이었는지는 응답의
  asset_source 에 표시한다.

  '계좌 미연동'과 '조회 실패'는 나눈다. 앞은 사용자가 계좌를 연결하면
  풀리고 뒤는 할 수 있는 일이 없어서, 안내 문구가 달라야 한다.
  (BalanceStatus 참고)

  인증서가 없거나 잘못된 경우도 '조회 실패'로 처리한다.
  연결 방식을 낮춰서 성공시키는 선택지는 두지 않는다.
"""
import logging
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import NamedTuple

import grpc
from grpc import aio

from app.core.config import settings
from app.grpc import asset_service_pb2, asset_service_pb2_grpc

logger = logging.getLogger(__name__)


class AssetCertificateUnavailable(RuntimeError):
    """mTLS 인증서를 읽을 수 없어 Asset 을 호출할 수 없는 상태."""


class BalanceStatus(str, Enum):
    """
    잔액 조회 결과의 성격.

    사용자에게 하는 안내가 달라지므로 '실패'를 한 덩어리로 묶지 않는다.

      OK          잔액을 받았다. 0 원도 여기다 — 계좌가 여러 개여도
                  돈이 안 들어 있으면 0 원이고, 그건 정상 응답이다.
      NOT_LINKED  계좌가 없거나 활성 계좌가 없다. Asset 은 멀쩡하고
                  사용자가 계좌를 연결하면 풀린다.
      UNAVAILABLE 조회 자체가 안 됐다. 인증서·연결·Asset 장애.
                  사용자가 할 수 있는 일이 없다.
    """
    OK          = "OK"
    NOT_LINKED  = "NOT_LINKED"
    UNAVAILABLE = "UNAVAILABLE"


class BalanceLookup(NamedTuple):
    """amount 는 OK 일 때만 의미가 있다."""
    status: BalanceStatus
    amount: Decimal | None = None

    @property
    def ok(self) -> bool:
        return self.status is BalanceStatus.OK


def status_from_grpc_code(code: grpc.StatusCode) -> BalanceStatus:
    """
    Asset 이 돌려준 gRPC 상태코드를 Work 의 판정으로 옮긴다.

    Asset 은 계좌 미연동·활성계좌 없음을 FAILED_PRECONDITION 으로 보낸다.
    (moaje-asset AssetGrpcService.toGrpcStatus — AccountException 처리)
    "요청 값은 정상이지만 계좌 상태가 조건을 만족하지 못함" 이라는 뜻이고,
    Asset 쪽 주석에도 Work 가 이를 정상 잔액 0 원과 구분하라고 적혀 있다.

    INVALID_ARGUMENT 는 Work 가 빈 user_id 를 보낸 경우다. 사용자 문제가
    아니라 Work 의 버그이므로 UNAVAILABLE 로 묶되 로그를 따로 남긴다.
    """
    if code == grpc.StatusCode.FAILED_PRECONDITION:
        return BalanceStatus.NOT_LINKED
    return BalanceStatus.UNAVAILABLE


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

    async def get_current_balance(self, user_id: int) -> BalanceLookup:
        """
        현재 잔액을 조회한다.

        결과를 세 갈래로 돌려준다 (BalanceStatus 참고). 잔액 0 원은 실패가
        아니라 OK 다. 계좌가 여러 개여도 돈이 안 들어 있으면 0 원이고,
        그건 정상 응답이다.

        '계좌 미연동'과 '조회 실패'를 나누는 이유는 사용자에게 할 말이
        다르기 때문이다. 앞은 계좌를 연결하면 풀리고, 뒤는 사용자가
        할 수 있는 일이 없다. 한 덩어리로 묶으면 계좌만 연결하면 될 사람에게
        "자산을 가져오지 못했다"고만 말하게 된다.
        """
        try:
            credentials = load_channel_credentials()
        except AssetCertificateUnavailable as e:
            # 평문(insecure) 으로 다시 시도하지 않는다.
            # Asset 은 클라이언트 인증서를 요구하므로 평문은 어차피 거절되고,
            # 무엇보다 인증서 누락을 조용히 우회하는 경로를 남기지 않는다.
            logger.error(f"❌ Asset mTLS 인증서 미비로 호출 중단 | user={user_id} | {e}")
            return BalanceLookup(BalanceStatus.UNAVAILABLE)

        # 계약의 user_id 는 string 이다. Work 내부는 정수로 다루므로 여기서 변환한다.
        req = asset_service_pb2.GetCurrentBalanceRequest(user_id=str(user_id))

        try:
            async with aio.secure_channel(
                self.target, credentials, options=self._channel_options()
            ) as channel:
                stub = asset_service_pb2_grpc.AssetServiceStub(channel)
                res  = await stub.GetCurrentBalance(req, timeout=self.timeout)

            balance = Decimal(str(res.current_balance.amount))
            logger.info(
                f"✅ Asset 잔액 조회 | user={user_id} | balance={int(balance):,}원"
            )
            return BalanceLookup(BalanceStatus.OK, balance)

        except aio.AioRpcError as e:
            code   = e.code()
            status = status_from_grpc_code(code)

            if status is BalanceStatus.NOT_LINKED:
                # 장애가 아니다. 계좌를 연결하면 풀리는 상태라 info 로 남긴다.
                logger.info(
                    f"ℹ️ Asset 계좌 상태로 잔액 없음 | user={user_id} "
                    f"| code={code.name} | {e.details()}"
                )
            elif code == grpc.StatusCode.INVALID_ARGUMENT:
                # Work 가 잘못된 user_id 를 보낸 경우 — 사용자 문제가 아니라 우리 버그다.
                logger.error(
                    f"❌ Asset 이 요청을 거절 (Work 측 문제) | user={user_id} "
                    f"| code={code.name} | {e.details()}"
                )
            else:
                # 인증서 불일치·만료는 UNAVAILABLE 로 오며 details 에 TLS 사유가 담긴다.
                # 연결 실패와 구분해 원인을 찾을 수 있도록 code 와 details 를 함께 남긴다.
                logger.warning(
                    f"⚠️ Asset 잔액 조회 실패 | user={user_id} "
                    f"| target={self.target} | code={code.name} | {e.details()}"
                )
            return BalanceLookup(status)

        except Exception as e:
            logger.warning(f"⚠️ Asset 연결 실패 | user={user_id} | {type(e).__name__}: {e}")
            return BalanceLookup(BalanceStatus.UNAVAILABLE)
