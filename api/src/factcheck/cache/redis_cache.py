"""Redis 缓存。claim normalize hash → 完整 CheckResponse JSON。

按 mode + policy_status 分级 TTL：
- 历史事实 / expired policy：30 天（不会变）
- active policy：7 天
- entity_status：1 天
- real-time：不缓存（TTL=0）
"""
from __future__ import annotations

import orjson

try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = None

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger("cache")


class RedisCache:
    def __init__(self, redis: "Redis", namespace: str = "factcheck:cache") -> None:
        self._r = redis
        self._ns = namespace

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    async def get(self, key: str) -> dict | None:
        try:
            data = await self._r.get(self._k(key))
            if not data:
                return None
            return orjson.loads(data)
        except Exception as e:
            logger.warning("cache_get_failed", key=key, error=str(e))
            return None

    async def set(self, key: str, value: dict, *, ttl: int) -> None:
        if ttl <= 0:
            return
        try:
            await self._r.set(self._k(key), orjson.dumps(value), ex=ttl)
        except Exception as e:
            logger.warning("cache_set_failed", key=key, error=str(e))

    async def delete(self, key: str) -> None:
        try:
            await self._r.delete(self._k(key))
        except Exception:
            pass

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False


async def build_cache() -> RedisCache | None:
    if not REDIS_AVAILABLE:
        logger.warning("redis_lib_not_installed")
        return None
    s = get_settings()
    try:
        r = Redis.from_url(s.redis_url, decode_responses=False)
        await r.ping()
        return RedisCache(r)
    except Exception as e:
        logger.warning("redis_connect_failed", error=str(e))
        return None
