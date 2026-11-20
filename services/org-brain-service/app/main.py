from fastapi import FastAPI

from app.config import OrgBrainConfig
from app.logging_config import configure_logging
from app.routers.health import router as health_router
from app.utils.middleware import RequestIdMiddleware


def create_app(cfg: OrgBrainConfig | None = None) -> FastAPI:
    cfg = cfg or OrgBrainConfig()
    configure_logging(cfg.log_level, cfg.log_format)

    app = FastAPI(title="Cortex (org-brain-service)", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    return app


app = create_app()
