from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print(f"✅ Work Service started | ENV: {settings.APP_ENV}")
    yield
    await engine.dispose()
    print("🛑 Work Service stopped")


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
