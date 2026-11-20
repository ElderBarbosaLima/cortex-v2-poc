"""Request id + structured request logs (shape of the monorepo's middleware).

Request bodies are not logged: the PoC carries synthetic customer data and the
access gate will log decisions explicitly where they matter.
"""

import functools
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.config import OrgBrainConfig

logger = structlog.get_logger()


@functools.cache
def _service_name() -> str:
    return OrgBrainConfig().service_name


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        clear_contextvars()
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            service=_service_name(),
        )
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info("request.completed", status=response.status_code, duration_ms=duration_ms)
        return response
