"""One error shape for the whole /v1/cortex contract.

    {"error": "<machine code>", "detail": <human text or list>, ...extra}

Codes in use: unauthorized (401), invalid_end_user / invalid_request (400/422),
insufficient_scope / access_denied (403), not_found (404),
access_gate_unavailable (503), not_implemented (501).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.clients.opa import AccessGateUnavailable


class ApiError(BaseModel):
    model_config = ConfigDict(extra="allow")

    error: str
    detail: Any = None


class CortexApiError(Exception):
    def __init__(self, status_code: int, error: str, detail: Any = None, **extra: Any) -> None:
        super().__init__(f"{status_code} {error}: {detail}")
        self.status_code = status_code
        self.body = {"error": error, "detail": detail, **extra}


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ApiError, "description": "Missing or unknown API key"},
    403: {"model": ApiError, "description": "Insufficient scope, or denied by policy"},
    422: {"model": ApiError, "description": "Invalid request"},
    503: {"model": ApiError, "description": "Access gate (policy engine) unavailable: fail closed"},
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CortexApiError)
    async def _api(request: Request, exc: CortexApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body)

    @app.exception_handler(AccessGateUnavailable)
    async def _gate(request: Request, exc: AccessGateUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"error": "access_gate_unavailable", "detail": str(exc)}
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {
                "loc": list(e.get("loc", ())),
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"error": "invalid_request", "detail": problems}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code, content={"error": code, "detail": exc.detail}
        )
