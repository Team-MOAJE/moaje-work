"""
공통 요청 의존성 — Gateway 가 전달한 사용자 식별값 처리

Gateway 는 JWT 서명 검증을 마친 뒤 sub 클레임을
X-Authenticated-User-Id 헤더에 담아 전달한다.
(moajeGateway/.../filter/GatewayJwtFilter.kt)

Gateway 는 클라이언트가 같은 이름으로 보낸 헤더를 덮어쓰므로
이 헤더의 값은 신뢰할 수 있다. 반대로 Work 가 직접 JWT 를
해석하지는 않는다 — 토큰 검증은 Gateway 의 책임이다.

인증의 근거는 이 헤더 하나뿐이다.
URL 경로나 요청 본문의 user_id 는 "누가 요청했는지"가 아니라
"무엇을 요청했는지"이므로, 인증 수단으로 쓰지 않는다.
두 값이 다르면 타인의 데이터를 요구한 것이므로 403 으로 막는다.
"""
import logging
import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

HEADER_NAME = "X-Authenticated-User-Id"

# ASCII 숫자만 받는다.
# str.isdigit() 은 '²' 나 전각 '７' 같은 유니코드 숫자도 참을 돌려주는데,
# 그중 '²' 는 latin-1 로 표현되므로 HTTP 헤더로 실제 도달할 수 있고
# int() 에서는 ValueError 가 된다. 그러면 401 이 아니라 전역 ValueError
# 핸들러의 400 으로 응답이 새므로, 형식 검사 단계에서 끊는다.
# 길이 19 는 BIGINT 최대값(9223372036854775807)의 자릿수다.
_USER_ID_RE = re.compile(r"^[0-9]{1,19}$")


# ══════════════════════════════════════════════════
#  1. 인증 사용자 확인
# ══════════════════════════════════════════════════

async def get_authenticated_user_id(
    x_authenticated_user_id: Annotated[
        str | None,
        Header(
            alias=HEADER_NAME,
            description="Gateway 가 JWT 검증 후 주입하는 사용자 ID (클라이언트가 직접 보낼 수 없음)",
        ),
    ] = None,
) -> int:
    """
    Gateway 가 주입한 사용자 ID를 꺼낸다.

    헤더가 없거나 숫자가 아니면 401.
    Gateway 를 거치지 않고 Work 포트로 직접 들어온 요청이 여기에 해당하며,
    이때 URL·본문의 user_id 로 대신 인증하지 않는다.
    """
    if x_authenticated_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{HEADER_NAME} 헤더가 없습니다. Gateway 를 통해 요청해주세요.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw = x_authenticated_user_id.strip()

    # Auth 는 sub 에 숫자형 사용자 ID를 문자열로 담는다. (app/core/security.py: "sub": str(user_id))
    # 음수·0·소수점·접두사가 붙은 값은 변조이거나 규격 위반이므로 통과시키지 않는다.
    if not _USER_ID_RE.fullmatch(raw):
        logger.warning(f"⚠️ {HEADER_NAME} 형식 오류 | value={x_authenticated_user_id!r}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{HEADER_NAME} 형식이 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(raw)
    if user_id <= 0:
        logger.warning(f"⚠️ {HEADER_NAME} 범위 오류 | value={user_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{HEADER_NAME} 값이 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


AuthUserId = Annotated[int, Depends(get_authenticated_user_id)]


# ══════════════════════════════════════════════════
#  2. 소유자 확인
# ══════════════════════════════════════════════════

def ensure_self(auth_user_id: int, claimed_user_id: int, *, where: str = "요청") -> int:
    """
    요청이 가리키는 사용자와 인증된 사용자가 같은지 확인하고,
    이후 DB·Asset 호출에 쓸 '검증된 사용자 ID' 를 돌려준다.

    요청 본문에서 user_id 를 받는 엔드포인트가 호출한다.
    경로 파라미터는 verify_path_user_id 가 라우터 단위로 처리한다.
    """
    if claimed_user_id != auth_user_id:
        logger.warning(
            f"🚫 타인 데이터 접근 차단 | auth={auth_user_id} "
            f"| claimed={claimed_user_id} | at={where}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="다른 사용자의 데이터에는 접근할 수 없습니다.",
        )
    return auth_user_id


async def verify_path_user_id(
    request: Request,
    auth_user_id: AuthUserId,
) -> int:
    """
    경로에 {user_id} 가 있으면 인증 사용자와 같은지 확인한다.

    라우터 단위 의존성으로 걸어 쓴다.

        router = APIRouter(dependencies=[Depends(verify_path_user_id)])

    엔드포인트마다 검사를 붙이는 방식과 달리, 앞으로 추가되는
    엔드포인트도 검사를 빼먹을 수 없다는 점이 이 방식의 이유다.
    경로에 {user_id} 가 없는 공용 조회(학교 목록, 질문 목록 등)는
    401 만 거치고 소유자 확인은 건너뛴다.

    이 가드를 통과한 이후의 user_id 경로 파라미터는
    인증된 사용자 ID와 같음이 보장되므로, 엔드포인트가 그 값을
    그대로 DB 조건이나 Asset 호출 인자로 써도 된다.
    """
    raw = request.path_params.get("user_id")
    if raw is None:
        return auth_user_id

    try:
        claimed = int(raw)
    except (TypeError, ValueError):
        # 경로 타입 검증(int)에서 이미 걸러지지만, 가드가 조용히
        # 통과시키는 경로를 남기지 않기 위해 명시적으로 막는다.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id 형식이 올바르지 않습니다.",
        )

    return ensure_self(auth_user_id, claimed, where=str(request.url.path))


OwnedUserId = Annotated[int, Depends(verify_path_user_id)]


# ══════════════════════════════════════════════════
#  3. 운영용 엔드포인트 가드
# ══════════════════════════════════════════════════

async def require_operator(
    x_internal_token: Annotated[
        str | None,
        Header(
            alias="X-Internal-Token",
            description="운영·내부 호출 전용 토큰 (WORK_INTERNAL_TOKEN)",
        ),
    ] = None,
) -> None:
    """
    사용자 본인 데이터가 아닌 것을 다루는 엔드포인트를 막는다.

    해당 대상
      - 블랙리스트 등록·해제 : 제재 조치이므로 본인이 할 수 있으면 안 된다.
                               (본인 해제를 허용하면 차단이 무의미해진다)
      - 재방문 검증 지표     : 전체 사용자 집계이므로 개인 데이터가 아니다.

    Gateway 는 /api/v1/work/** 전체를 전달하므로, 로그인한 사용자라면
    누구나 이 경로에 닿는다. 역할(role) 클레임 규격이 아직 없어
    우선 공유 토큰으로 막아둔다.

    토큰이 설정되지 않은 환경에서는 열어두지 않고 503 으로 닫는다.
    설정 누락이 곧 공개로 이어지지 않게 하려는 것이다.
    """
    expected = settings.WORK_INTERNAL_TOKEN

    if not expected:
        logger.error("❌ WORK_INTERNAL_TOKEN 미설정 — 운영용 엔드포인트를 닫는다.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="운영용 엔드포인트가 설정되지 않았습니다. WORK_INTERNAL_TOKEN 을 지정해주세요.",
        )

    if x_internal_token != expected:
        logger.warning("🚫 운영용 엔드포인트 접근 차단 — 내부 토큰 불일치")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 엔드포인트는 내부 운영용입니다.",
        )
