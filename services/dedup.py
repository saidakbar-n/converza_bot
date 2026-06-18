"""
Telegram update_id deduplication.

v1 default: in-memory OrderedDict (single process).
Production multi-instance: set REDIS_URL for shared dedup across workers.
"""

from collections import OrderedDict
import logging
import os
import time

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 10_000
_TTL_SECONDS = 86_400  # 24h
_REDIS_KEY_PREFIX = "converza:telegram:update:"
REDIS_URL = os.getenv("REDIS_URL", "").strip()

_seen: OrderedDict[int, float] = OrderedDict()
_redis_client = None
_redis_unavailable = False


def _get_redis():
    global _redis_client, _redis_unavailable
    if not REDIS_URL or _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info("Webhook dedup using Redis at %s", REDIS_URL.split("@")[-1])
        return _redis_client
    except Exception as exc:
        _redis_unavailable = True
        logger.warning("REDIS_URL set but Redis unavailable — falling back to in-memory dedup: %s", exc)
        return None


def is_duplicate(update_id: int) -> bool:
    """Return True if this update_id was already processed."""
    client = _get_redis()
    if client is not None:
        key = f"{_REDIS_KEY_PREFIX}{update_id}"
        added = client.set(key, "1", nx=True, ex=_TTL_SECONDS)
        return added is None

    now = time.time()
    _purge_expired(now)

    if update_id in _seen:
        return True

    _seen[update_id] = now
    if len(_seen) > _MAX_ENTRIES:
        _seen.popitem(last=False)
    return False


def _purge_expired(now: float) -> None:
    cutoff = now - _TTL_SECONDS
    while _seen:
        oldest_id, oldest_ts = next(iter(_seen.items()))
        if oldest_ts >= cutoff:
            break
        _seen.popitem(last=False)
