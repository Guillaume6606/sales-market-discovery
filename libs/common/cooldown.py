"""Shared per-source ingestion cooldown stored in Redis.

A connector sets the cooldown when a marketplace denies access (401/403/429).
The scheduler checks it before enqueueing jobs, so a block costs one request
per cooldown window instead of one per product per interval, and the state
survives worker restarts.
"""

from datetime import UTC, datetime
from typing import Any

COOLDOWN_KEY_PREFIX = "ingestion:cooldown:"


def cooldown_key(source: str) -> str:
    return f"{COOLDOWN_KEY_PREFIX}{source}"


async def set_source_cooldown(redis: Any, source: str, seconds: int) -> None:
    await redis.set(cooldown_key(source), datetime.now(UTC).isoformat(), ex=seconds)


async def source_cooldown_active(redis: Any, source: str) -> bool:
    return bool(await redis.exists(cooldown_key(source)))
