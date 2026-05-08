import logging
logging.basicConfig(level=logging.INFO)

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine, Base
from app.kafka.producer import stop_producer
from app.kafka.consumer import start_consumer
from app.redis.client import stop_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 시작 ──────────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Kafka Consumer 백그라운드 태스크 시작
    consumer_task = asyncio.create_task(start_consumer())
    logger.info(f"✅ Work Service 시작 | ENV: {settings.APP_ENV}")

    yield

    # ── 종료 ──────────────────────────────────────
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await stop_producer()
    await stop_redis()
    await engine.dispose()
    logger.info("🛑 Work Service 종료")


app = FastAPI(
    title="모아제 Work Service",
    description="AI 소비 패턴 분석 · 낭만 달빛 · FDS 이상거래 탐지",
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

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["헬스체크"])
async def health_check():
    return {"status": "ok", "service": "moaje-work-service"}
