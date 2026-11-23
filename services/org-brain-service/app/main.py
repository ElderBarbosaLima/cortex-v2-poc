from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import install_error_handlers
from app.clients.llm import configure as configure_llm
from app.clients.opa import AccessGateUnavailable, OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine
from app.domain_packs import discover as discover_packs
from app.identity import ConsumerKeyStore
from app.keys import install as install_keys
from app.logging_config import configure_logging
from app.models import Tenant
from app.projections import default_projection
from app.projections.base import SearchProjection
from app.routers.cortex import router as cortex_router
from app.routers.health import router as health_router
from app.routers.review_cases import router as review_router
from app.seed.tenants import ConsumerInfo, Seed, load_seed
from app.services.policy_gate import PolicyGate
from app.services.writeback import RuleSuggestionPolicy
from app.utils.middleware import RequestIdMiddleware

_logger = structlog.get_logger("org-brain.main")


@dataclass
class AppServices:
    """Everything a request needs from the outside world. Tests inject fakes."""

    seed: Seed
    consumer_keys: ConsumerKeyStore
    policy_gate: PolicyGate
    search: SearchProjection = field(default_factory=default_projection)
    rule_suggestion: RuleSuggestionPolicy = field(default_factory=RuleSuggestionPolicy)
    evidence_bucket: str = "cortex-poc-evidence"


def register_tenants(seed: Seed, engine: Engine | None = None) -> None:
    """The tenant registry the outbox drainer iterates (idempotent)."""
    rows = [{"tenant_id": t.tenant_id, "key": t.key} for t in seed.tenants]
    with (engine or app_engine()).begin() as conn:
        conn.execute(insert(Tenant).values(rows).on_conflict_do_nothing())


def build_services(cfg: OrgBrainConfig) -> AppServices:
    discover_packs()  # every domain pack under app/domain_packs, found, never named (D18)
    configure_llm(cfg)  # AI through LiteLLM, only the uses switched on (issue 30)
    seed = load_seed(Path(cfg.seed_dir))
    gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=cfg.opa_timeout_s), seed)
    keys = ConsumerKeyStore.from_secrets_manager(cfg.consumer_keys_secret)
    register_tenants(seed)
    install_keys(t.tenant_id for t in seed.tenants)  # tenant KEKs; aes-gcm/1 for new bodies
    try:
        gate.load_all()
    except AccessGateUnavailable as exc:
        # Not fatal: reads fail closed until OPA answers, then the gate reloads.
        _logger.error("startup.opa_unavailable", error=str(exc))
    return AppServices(
        seed=seed,
        consumer_keys=keys,
        policy_gate=gate,
        rule_suggestion=RuleSuggestionPolicy.from_spec(cfg.rule_suggestion),
        evidence_bucket=cfg.evidence_bucket,
    )


def _install(app: FastAPI, services: AppServices) -> None:
    app.state.consumer_keys = services.consumer_keys
    app.state.consumers = services.seed.consumers
    app.state.policy_gate = services.policy_gate
    app.state.seed = services.seed
    app.state.search = services.search
    app.state.rule_suggestion = services.rule_suggestion
    app.state.evidence_bucket = services.evidence_bucket


def create_app(
    cfg: OrgBrainConfig | None = None, *, services: AppServices | None = None
) -> FastAPI:
    cfg = cfg or OrgBrainConfig()
    configure_logging(cfg.log_level, cfg.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if services is None:
            _install(app, build_services(cfg))
        yield

    app = FastAPI(
        title="Cortex API",
        version="0.1.0",
        summary="Governed organisational memory: the /v1/cortex consumer contract (PoC).",
        lifespan=lifespan,
    )
    if services is not None:
        _install(app, services)
    app.add_middleware(RequestIdMiddleware)

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(cortex_router)
    app.include_router(review_router)
    return app


__all__ = ["AppServices", "ConsumerInfo", "create_app"]

app = create_app()
