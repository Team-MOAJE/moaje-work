import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine, Base
from app.kafka.producer import stop_producer
from app.kafka.consumer import start_consumer
from app.redis.client import stop_redis
from app.grpc.server import start_grpc_server

from app.models import spending, fds  # noqa

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 공통 에러 응답 형식 ───────────────────────────
def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code"     : code,
            "message"  : message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service"  : "moaje-work-service",
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    consumer_task = asyncio.create_task(start_consumer())
    grpc_task     = asyncio.create_task(start_grpc_server())
    logger.info(f"✅ Work Service 시작 | ENV: {settings.APP_ENV} | PORT: 8084")

    yield

    consumer_task.cancel()
    grpc_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    try:
        await grpc_task
    except asyncio.CancelledError:
        pass

    await stop_producer()
    await stop_redis()
    await engine.dispose()
    logger.info("🛑 Work Service 종료")


app = FastAPI(
    title="모아제 Work Service",
    description="AI 소비 패턴 분석 · FDS 이상거래 탐지",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1/work")


# ══════════════════════════════════════════════════
#  전역 예외 핸들러
# ══════════════════════════════════════════════════

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 입력값 검증 실패"""
    errors = [
        f"{' → '.join(str(l) for l in e['loc'])}: {e['msg']}"
        for e in exc.errors()
    ]
    logger.warning(f"⚠️ 입력값 검증 실패 | {request.url} | {errors}")
    return error_response(
        code       = "VALIDATION_ERROR",
        message    = f"입력값이 올바르지 않습니다. {', '.join(errors)}",
        status_code= status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(OperationalError)
async def db_operational_error_handler(request: Request, exc: OperationalError):
    """DB 연결 실패"""
    logger.error(f"❌ DB 연결 실패 | {request.url} | {exc}")
    return error_response(
        code       = "DB_CONNECTION_ERROR",
        message    = "데이터베이스 연결에 실패했습니다. 잠시 후 다시 시도해주세요.",
        status_code= status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(request: Request, exc: SQLAlchemyError):
    """DB 쿼리 오류"""
    logger.error(f"❌ DB 오류 | {request.url} | {exc}")
    return error_response(
        code       = "DB_ERROR",
        message    = "데이터베이스 처리 중 오류가 발생했습니다.",
        status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError):
    """Redis / 외부 서비스 연결 실패"""
    logger.error(f"❌ 연결 실패 | {request.url} | {exc}")
    return error_response(
        code       = "CONNECTION_ERROR",
        message    = "외부 서비스 연결에 실패했습니다. 잠시 후 다시 시도해주세요.",
        status_code= status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError):
    """gRPC / 외부 호출 타임아웃"""
    logger.error(f"❌ 타임아웃 | {request.url} | {exc}")
    return error_response(
        code       = "TIMEOUT_ERROR",
        message    = "요청 처리 시간이 초과됐습니다. 잠시 후 다시 시도해주세요.",
        status_code= status.HTTP_504_GATEWAY_TIMEOUT,
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """잘못된 값"""
    logger.warning(f"⚠️ 값 오류 | {request.url} | {exc}")
    return error_response(
        code       = "INVALID_VALUE",
        message    = str(exc),
        status_code= status.HTTP_400_BAD_REQUEST,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """예상치 못한 모든 예외 — 최후 방어선"""
    logger.error(f"❌ 예상치 못한 오류 | {request.url} | {type(exc).__name__}: {exc}")
    return error_response(
        code       = "INTERNAL_SERVER_ERROR",
        message    = "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ══════════════════════════════════════════════════
#  헬스체크 (DB · Redis · ML 모델 상태 포함)
# ══════════════════════════════════════════════════

@app.get("/health", tags=["헬스체크"])
async def health_check():
    """서비스 및 의존성 상태 확인"""
    import sqlalchemy
    health = {
        "status"    : "ok",
        "service"   : "moaje-work-service",
        "port"      : 8084,
        "timestamp" : datetime.now(timezone.utc).isoformat(),
        "components": {}
    }

    # DB 상태 확인
    try:
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        health["components"]["database"] = {"status": "ok"}
    except Exception as e:
        health["components"]["database"] = {"status": "error", "detail": str(e)}
        health["status"] = "degraded"
        logger.warning(f"⚠️ 헬스체크 DB 오류: {e}")

    # Redis 상태 확인
    try:
        from app.redis.client import get_redis
        redis = await get_redis()
        await redis.ping()
        health["components"]["redis"] = {"status": "ok"}
    except Exception as e:
        health["components"]["redis"] = {"status": "error", "detail": str(e)}
        health["status"] = "degraded"
        logger.warning(f"⚠️ 헬스체크 Redis 오류: {e}")

    # ML 모델 상태 확인
    try:
        from app.services.fds.detector import _ml_model
        health["components"]["ml_model"] = {
            "status": "ok" if _ml_model else "not_loaded"
        }
    except Exception as e:
        health["components"]["ml_model"] = {"status": "error", "detail": str(e)}

    status_code = 200 if health["status"] == "ok" else 207
    return JSONResponse(status_code=status_code, content=health)
