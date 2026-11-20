"""structlog setup, copied from the monorepo's org-brain-service (logging_config.py)."""

import logging
from typing import Any, cast

import structlog

REDACTED_FIELDS = {
    "password",
    "token",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "credential",
    "private_key",
}


def redact(data: Any) -> Any:
    """Recursively redact sensitive fields from a dict before logging."""
    if not isinstance(data, dict):
        return data
    items = cast(dict[str, Any], data)
    return {
        k: "***REDACTED***" if k.lower() in REDACTED_FIELDS else redact(v) for k, v in items.items()
    }


def configure_logging(log_level: str, log_format: str) -> None:
    """Configure structlog and stdlib logging. Call once at application startup."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Without this, a plain `logging.getLogger(...)` call's `extra={...}`
        # payload is dropped on the floor: ProcessorFormatter builds the event
        # dict for a FOREIGN record from `record.getMessage()` alone. Modules
        # logging via the stdlib logger rather than structlog (e.g.
        # `app.elasticsearch.terminology_index`) were emitting a bare event
        # name with every diagnostic field silently discarded -- an alarm that
        # fires and says nothing.
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(),
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level.upper())
