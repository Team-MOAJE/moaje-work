"""
Redis 클라이언트
Work 서비스 캐시 관리

장애 대응 전략 (금융 앱 가용성 우선):
  Redis 장애 시 → 캐시 MISS로 처리, DB에서 직접 조회 (fail-open)
  DB 장애 시    → Redis 캐시 데이터 반환
  둘 다 장애 시 → 503 응답
"""
import json
import logging
from decimal import Decimal

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

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
            socket_connect_timeout=2,   # 연결 타임아웃 2초
            socket_timeout=2,           # 읽기/쓰기 타임아웃 2초
            retry_on_timeout=False,     # 타임아웃 시 즉시 실패 (금융 앱 응답속도 우선)
        )
        logger.info("✅ Redis 연결 완료")
    return _redis


async def stop_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("🛑 Redis 연결 종료")


async def is_redis_available() -> bool:
    """Redis 가용성 확인"""
    try:
        redis = await get_redis()
        await redis.ping()
        return True
    except Exception:
        return False


# ── Daily Limit 캐시 ──────────────────────────────

async def get_daily_limit_cache(user_id: int) -> dict | None:
    """
    Daily Limit 캐시 조회
    Redis 장애 시 None 반환 → 호출자가 DB fallback 처리
    """
    try:
        redis = await get_redis()
        data = await redis.get(f"daily_limit:{user_id}")
        if data:
            logger.info(f"✅ Redis HIT | daily_limit:{user_id}")
            return json.loads(data)
        logger.info(f"❌ Redis MISS | daily_limit:{user_id}")
        return None
    except (RedisError, RedisConnectionError, Exception) as e:
        logger.warning(f"⚠️ Redis 장애 → DB fallback | daily_limit:{user_id} | {e}")
        return None  # fail-open: None 반환 → DB에서 직접 조회


async def set_daily_limit_cache(user_id: int, payload: dict, ttl: int = 300):
    """
    Daily Limit 캐시 저장 (TTL: 300초)
    Redis 장애 시 무시하고 계속 진행 (캐시 실패는 치명적 오류 아님)
    """
    try:
        redis = await get_redis()
        await redis.set(
            f"daily_limit:{user_id}",
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=ttl,
        )
        logger.info(f"💾 Redis SET | daily_limit:{user_id} | TTL={ttl}s")
    except Exception as e:
        logger.warning(f"⚠️ Redis SET 실패 (무시) | daily_limit:{user_id} | {e}")


async def delete_daily_limit_cache(user_id: int):
    """Daily Limit 캐시 무효화"""
    try:
        redis = await get_redis()
        await redis.delete(f"daily_limit:{user_id}")
        logger.info(f"🗑️ Redis 삭제 | daily_limit:{user_id}")
    except Exception as e:
        logger.warning(f"⚠️ Redis 삭제 실패 (무시) | daily_limit:{user_id} | {e}")


# ── 이벤트 버퍼 캐시 ──────────────────────────────

async def get_event_buffer_cache(user_id: int) -> str | None:
    """
    학사 이벤트 버퍼 캐시 조회
    Redis 장애 시 None 반환 → DB fallback
    """
    try:
        redis = await get_redis()
        data = await redis.get(f"event_buffer:{user_id}")
        if data:
            logger.info(f"✅ Redis HIT | event_buffer:{user_id}")
        return data
    except Exception as e:
        logger.warning(f"⚠️ Redis 장애 → DB fallback | event_buffer:{user_id} | {e}")
        return None


async def set_event_buffer_cache(user_id: int, buffer: Decimal, ttl: int = 3600):
    """학사 이벤트 버퍼 캐시 저장 (TTL: 3600초)"""
    try:
        redis = await get_redis()
        await redis.set(f"event_buffer:{user_id}", str(buffer), ex=ttl)
        logger.info(f"💾 Redis SET | event_buffer:{user_id} | TTL={ttl}s")
    except Exception as e:
        logger.warning(f"⚠️ Redis SET 실패 (무시) | event_buffer:{user_id} | {e}")


# ── 소비 프로필 캐시 ──────────────────────────────

async def get_spending_profile_cache(user_id: int) -> dict | None:
    """
    AI 소비 프로필 캐시 조회
    Redis 장애 시 None 반환 → DB fallback
    """
    try:
        redis = await get_redis()
        data = await redis.get(f"spending_profile:{user_id}")
        if data:
            logger.info(f"✅ Redis HIT | spending_profile:{user_id}")
            return json.loads(data)
        return None
    except Exception as e:
        logger.warning(f"⚠️ Redis 장애 → DB fallback | spending_profile:{user_id} | {e}")
        return None


async def set_spending_profile_cache(user_id: int, profile: dict, ttl: int = 1800):
    """AI 소비 프로필 캐시 저장 (TTL: 1800초)"""
    try:
        redis = await get_redis()
        await redis.set(
            f"spending_profile:{user_id}",
            json.dumps(profile, ensure_ascii=False, default=str),
            ex=ttl,
        )
        logger.info(f"💾 Redis SET | spending_profile:{user_id} | TTL={ttl}s")
    except Exception as e:
        logger.warning(f"⚠️ Redis SET 실패 (무시) | spending_profile:{user_id} | {e}")
