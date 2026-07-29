from fastapi import Request, status
from fastapi.responses import JSONResponse
import time
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# In-memory rate limiter
rate_limit_store: dict = defaultdict(list)

RATE_LIMITS = [
    ("/api/auth/login", {"max_requests": 10, "window_seconds": 60}),
    ("/api/auth/signup", {"max_requests": 5, "window_seconds": 3600}),
    ("/api/users", {"max_requests": 60, "window_seconds": 60}),
    ("/api/dashboard", {"max_requests": 30, "window_seconds": 60}),
]
DEFAULT_LIMIT = {"max_requests": 100, "window_seconds": 60}


def get_rate_limit_config(path: str) -> tuple:
    """Get (prefix, config) for a given path."""
    for prefix, config in RATE_LIMITS:
        if path.startswith(prefix):
            return prefix, config
    return "default", DEFAULT_LIMIT


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limiting_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)

    client_ip = get_client_ip(request)
    prefix, config = get_rate_limit_config(path)
    max_requests = config["max_requests"]
    window_seconds = config["window_seconds"]

    # Use the matched prefix as key to prevent bypass via different IDs
    key = f"{client_ip}:{prefix}"
    now = time.time()
    timestamps = rate_limit_store[key]
    rate_limit_store[key] = [t for t in timestamps if now - t < window_seconds]

    if len(rate_limit_store[key]) >= max_requests:
        logger.warning(f"Rate limit exceeded for {client_ip} on {path} (prefix: {prefix})")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Too many requests. Please try again later.",
                "retry_after_seconds": int(window_seconds)
            }
        )

    rate_limit_store[key].append(now)
    response = await call_next(request)
    return response
