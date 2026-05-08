"""
Redis 클라이언트
Work 서비스 캐시 관리
"""
import json
import logging
from decimal import Decimal

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("✅ Redis 연결 완료")
    return _redis


async def stop_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("🛑 Redis 연결 종료")


# ── Daily Limit 캐시 ──────────────────────────────

async def get_daily_limit_cache(user_id: int) -> dict | None:
    """Daily Limit 캐시 조회"""
    redis = await get_redis()
    data = await redis.get(f"daily_limit:{user_id}")
    if data:
        logger.info(f"✅ Redis 캐시 HIT | daily_limit:{user_id}")
        return json.loads(data)
    logger.info(f"❌ Redis 캐시 MISS | daily_limit:{user_id}")
    return None


async def set_daily_limit_cache(user_id: int, payload: dict, ttl: int = 300):
    """Daily Limit 캐시 저장 (TTL: 300초)"""
    redis = await get_redis()
    await redis.set(
        f"daily_limit:{user_id}",
        json.dumps(payload, ensure_ascii=False, default=str),
        ex=ttl,
    )
    logger.info(f"💾 Redis 캐시 SET | daily_limit:{user_id} | TTL={ttl}s")


async def delete_daily_limit_cache(user_id: int):
    """Daily Limit 캐시 무효화 (새 거래 발생 시 호출)"""
    redis = await get_redis()
    await redis.delete(f"daily_limit:{user_id}")
    logger.info(f"🗑️ Redis 캐시 삭제 | daily_limit:{user_id}")


# ── 이벤트 버퍼 캐시 ──────────────────────────────

async def get_event_buffer_cache(user_id: int) -> str | None:
    """학사 이벤트 버퍼 캐시 조회"""
    redis = await get_redis()
    data = await redis.get(f"event_buffer:{user_id}")
    if data:
        logger.info(f"✅ Redis 캐시 HIT | event_buffer:{user_id}")
    return data


async def set_event_buffer_cache(user_id: int, buffer: Decimal, ttl: int = 3600):
    """학사 이벤트 버퍼 캐시 저장 (TTL: 3600초)"""
    redis = await get_redis()
    await redis.set(f"event_buffer:{user_id}", str(buffer), ex=ttl)
    logger.info(f"💾 Redis 캐시 SET | event_buffer:{user_id} | TTL={ttl}s")


# ── 소비 프로필 캐시 ──────────────────────────────

async def get_spending_profile_cache(user_id: int) -> dict | None:
    """AI 소비 프로필 캐시 조회"""
    redis = await get_redis()
    data = await redis.get(f"spending_profile:{user_id}")
    if data:
        logger.info(f"✅ Redis 캐시 HIT | spending_profile:{user_id}")
        return json.loads(data)
    return None


async def set_spending_profile_cache(user_id: int, profile: dict, ttl: int = 1800):
    """AI 소비 프로필 캐시 저장 (TTL: 1800초)"""
    redis = await get_redis()
    await redis.set(
        f"spending_profile:{user_id}",
        json.dumps(profile, ensure_ascii=False, default=str),
        ex=ttl,
    )
    logger.info(f"💾 Redis 캐시 SET | spending_profile:{user_id} | TTL={ttl}s")
