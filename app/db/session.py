from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.APP_ENV == "development"),
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    # server_default=func.now() 로 둔 타임스탬프를 INSERT 직후에 읽어온다.
    #
    # SQLAlchemy 의 기본값 eager_defaults="auto" 는 RETURNING 을 지원하는
    # DB 에서만 서버 생성 값을 가져온다. MySQL 에는 RETURNING 이 없어
    # flush 후에도 last_analyzed_at 같은 컬럼이 미로딩으로 남고,
    # 그 값을 response_model 직렬화 시점에 접근하면 비동기 컨텍스트 밖에서
    # lazy load 가 일어나 MissingGreenlet 으로 500 이 됐다.
    #
    # True 로 두면 RETURNING 이 없는 DB 에서도 flush 안에서 SELECT 를 한 번
    # 더 보내 값을 채운다. INSERT 당 SELECT 가 한 번 늘지만, 생성한 행을
    # 그대로 응답으로 돌려주는 엔드포인트가 조용히 깨지는 것보다 낫다.
    __mapper_args__ = {"eager_defaults": True}


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
