from __future__ import annotations

import hashlib
import logging

from redis import RedisError
from redis.asyncio import Redis
from redis.exceptions import NoScriptError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)


RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call("HMGET", key, "tokens", "updated_at")
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  updated_at = now
end

local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + (elapsed * rate))

local allowed = 0
local retry_after_ms = 0

if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  retry_after_ms = math.ceil((cost - tokens) / rate)
end

local reset_after_ms = math.ceil((capacity - tokens) / rate)
redis.call("HMSET", key, "tokens", tokens, "updated_at", now)
redis.call("PEXPIRE", key, ttl)

return { allowed, math.floor(tokens), retry_after_ms, reset_after_ms }
"""

_redis_client: Redis | None = None
_rate_limit_script_sha: str | None = None


class RateLimitBackendUnavailable(Exception):
    pass


class RateLimitDecision:
    def __init__(
        self,
        *,
        allowed: bool,
        limit: int,
        remaining: int,
        retry_after_seconds: int,
        reset_after_seconds: int,
    ) -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.retry_after_seconds = retry_after_seconds
        self.reset_after_seconds = reset_after_seconds

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_after_seconds),
            "Retry-After": str(self.retry_after_seconds),
        }


def _hash_client_key(client_key: str) -> str:
    return hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:12]


def _client_key(scope: Scope) -> str:
    headers = Headers(scope=scope)
    configured_header = settings.rate_limit_client_header.strip()
    if configured_header:
        value = headers.get(configured_header)
        if value:
            return value.split(",")[0].strip()

    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])

    return "unknown"


def _should_limit(scope: Scope) -> bool:
    return (
        bool(settings.rate_limit_enabled)
        and scope["type"] == "http"
        and scope.get("method") == "POST"
        and scope.get("path") == "/query"
    )


async def _send_json(
    send: Send,
    *,
    status_code: int,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> None:
    raw_headers = [(b"content-type", b"application/json")]
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))

    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": raw_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.rate_limit_redis_url, decode_responses=False)
    return _redis_client


async def _redis_time_ms(redis: Redis) -> int:
    seconds, microseconds = await redis.time()
    return (int(seconds) * 1000) + (int(microseconds) // 1000)


async def _eval_rate_limit(redis: Redis, key: str, args: list[float | int]) -> list[int]:
    global _rate_limit_script_sha
    if _rate_limit_script_sha is None:
        _rate_limit_script_sha = await redis.script_load(RATE_LIMIT_SCRIPT)

    try:
        return await redis.evalsha(_rate_limit_script_sha, 1, key, *args)
    except NoScriptError:
        _rate_limit_script_sha = await redis.script_load(RATE_LIMIT_SCRIPT)
        return await redis.evalsha(_rate_limit_script_sha, 1, key, *args)


async def check_rate_limit(client_key: str) -> RateLimitDecision:
    limit = max(1, int(settings.rate_limit_query_per_minute))
    capacity = max(1, int(settings.rate_limit_query_burst))
    rate_per_ms = limit / 60_000
    ttl_ms = max(60_000, int((capacity / rate_per_ms) * 2))
    key = f"rate-limit:query:{client_key}"

    try:
        redis = await get_redis_client()
        now_ms = await _redis_time_ms(redis)
        allowed, remaining, retry_after_ms, reset_after_ms = await _eval_rate_limit(
            redis,
            key,
            [rate_per_ms, capacity, now_ms, 1, ttl_ms],
        )
    except RedisError as exc:
        raise RateLimitBackendUnavailable from exc

    return RateLimitDecision(
        allowed=bool(allowed),
        limit=limit,
        remaining=int(remaining),
        retry_after_seconds=max(1, int((int(retry_after_ms) + 999) / 1000)),
        reset_after_seconds=max(1, int((int(reset_after_ms) + 999) / 1000)),
    )


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _should_limit(scope):
            await self.app(scope, receive, send)
            return

        client_key = _client_key(scope)
        key_hash = _hash_client_key(client_key)

        try:
            decision = await check_rate_limit(client_key)
        except RateLimitBackendUnavailable:
            logger.error("Rate limiter backend unavailable client=%s", key_hash)
            await _send_json(
                send,
                status_code=503,
                body=b'{"detail":"Rate limiter unavailable"}',
            )
            return

        if not decision.allowed:
            logger.info("Rate limit exceeded client=%s path=/query", key_hash)
            await _send_json(
                send,
                status_code=429,
                body=b'{"detail":"Rate limit exceeded"}',
                headers=decision.headers,
            )
            return

        async def send_with_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    (name.lower().encode("latin-1"), value.encode("latin-1"))
                    for name, value in decision.headers.items()
                    if name != "Retry-After"
                )
                message["headers"] = headers
            await send(message)

        logger.info("Rate limit allowed client=%s path=/query", key_hash)
        await self.app(scope, receive, send_with_rate_limit_headers)
