"""The projector: drains the outbox into the search projection.

    python -m app.workers.projector            # loop (the org-brain-projector service)
    python -m app.workers.projector --once     # one pass, then exit

Runs as its own process, so a projection can lag the ledger (acceptance
scenario 4) without any test-only switch in the code: tests simply drain when
they choose to.
"""

from __future__ import annotations

import argparse
import signal
import time
from types import FrameType

import structlog

from app.config import OrgBrainConfig
from app.db import app_engine
from app.keys import install as install_keys
from app.logging_config import configure_logging
from app.models import Outbox
from app.workers import handlers
from app.workers.outbox import drain_all, tenant_ids

_logger = structlog.get_logger("org-brain.projector")
# Imported for its side effect: it registers the outbox handlers this process runs.
REGISTERED = (handlers.project_record,)


_running = True


def _stop(signum: int, frame: FrameType | None) -> None:
    global _running
    _running = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain the outbox into the search projection")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    cfg = OrgBrainConfig()
    configure_logging(cfg.log_level, cfg.log_format)
    signal.signal(signal.SIGTERM, _stop)
    engine = app_engine()
    install_keys(tenant_ids(engine))  # the projection opens sealed bodies
    _logger.info("projector.started", table=Outbox.__tablename__)
    while _running:
        for tenant, result in drain_all(engine).items():
            if result.dispatched or result.failed or result.retried:
                _logger.info("projector.drained", tenant_id=tenant, **vars(result))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
