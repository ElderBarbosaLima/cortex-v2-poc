"""The service's view of OPA: decisions with tenant data kept loaded.

OPA keeps pushed data in memory, so after an OPA restart it no longer knows the
tenant and answers "unknown tenant". Then, and only then, the gate pushes that
tenant's seed data again and asks once more. OPA down is still a 503
(AccessGateUnavailable); this only repairs a stale data load.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from app.clients.opa import Decision, OpaClient
from app.seed.tenants import Seed

_logger = structlog.get_logger("org-brain.policy-gate")
UNKNOWN_TENANT = "unknown tenant"


def now_rfc3339() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class PolicyGate:
    def __init__(self, opa: OpaClient, seed: Seed) -> None:
        self._opa = opa
        self._by_tenant = {t.tenant_id: t for t in seed.tenants}

    def load_all(self) -> None:
        for tenant in self._by_tenant.values():
            self._opa.put_tenant(tenant.tenant_id, tenant.opa_data)

    def _with_reload(self, ask: Any, payload: dict[str, Any]) -> Decision:
        decision: Decision = ask(payload)
        tenant = self._by_tenant.get(str(payload.get("tenant_id")))
        if not decision.allow and decision.reason == UNKNOWN_TENANT and tenant is not None:
            _logger.warning("policy_gate.reloading_tenant", tenant_id=tenant.tenant_id)
            self._opa.put_tenant(tenant.tenant_id, tenant.opa_data)
            decision = ask(payload)
        return decision

    def read(self, payload: dict[str, Any]) -> Decision:
        return self._with_reload(self._opa.read_decision, payload)

    def approval(self, payload: dict[str, Any]) -> Decision:
        return self._with_reload(self._opa.approval_decision, payload)
