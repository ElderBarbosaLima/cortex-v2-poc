"""OPA client for the access gate and approvals (D22).

Fail closed: if OPA does not answer, answers with an HTTP error, times out, or
returns no `result`, the caller gets `AccessGateUnavailable` (HTTP 503
`access_gate_unavailable`) and nothing is read or decided. That is the
documented degraded mode: no reads, no approvals. A denial is a normal answer
(`allow: false` plus OPA's reason), surfaced as 403 by the routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx
import structlog

_logger = structlog.get_logger("org-brain.opa")

READ_DECISION = "cortex/policy/authz/decision"
APPROVAL_DECISION = "cortex/policy/approval/decision"


class AccessGateUnavailable(RuntimeError):
    """OPA could not give a decision. Never treated as an allow."""


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    policy_revision: str | None
    raw: dict[str, Any]

    @property
    def readable_scopes(self) -> list[str]:
        return list(self.raw.get("readable_scopes") or [])

    @property
    def sensitivity_ceiling(self) -> str | None:
        ceiling = self.raw.get("sensitivity_ceiling")
        return str(ceiling) if ceiling is not None else None


class OpaClient:
    def __init__(
        self, base_url: str, *, timeout_s: float = 0.5, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.post(f"/v1/data/{path}", json={"input": payload})
            response.raise_for_status()
            body: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            _logger.error("opa.unavailable", path=path, error=f"{type(exc).__name__}: {exc}")
            raise AccessGateUnavailable(
                f"policy engine unavailable ({type(exc).__name__})"
            ) from exc
        result: Any = cast("dict[str, Any]", body).get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            _logger.error("opa.no_result", path=path)
            raise AccessGateUnavailable("policy engine returned no decision")
        decision = cast("dict[str, Any]", result)
        if not isinstance(decision.get("allow"), bool):
            _logger.error("opa.no_result", path=path)
            raise AccessGateUnavailable("policy engine returned no decision")
        return decision

    def _decide(self, path: str, payload: dict[str, Any]) -> Decision:
        result = self._post(path, payload)
        decision = Decision(
            allow=bool(result["allow"]),
            reason=str(result.get("reason", "")),
            policy_revision=result.get("policy_revision"),
            raw=result,
        )
        _logger.info(
            "opa.decision",
            path=path,
            allow=decision.allow,
            reason=decision.reason,
            policy_revision=decision.policy_revision,
        )
        return decision

    def read_decision(self, payload: dict[str, Any]) -> Decision:
        return self._decide(READ_DECISION, payload)

    def approval_decision(self, payload: dict[str, Any]) -> Decision:
        return self._decide(APPROVAL_DECISION, payload)

    def put_tenant(self, tenant_id: str, data: dict[str, Any]) -> None:
        try:
            response = self._http.put(f"/v1/data/cortex/tenants/{tenant_id}", json=data)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AccessGateUnavailable(
                f"could not load tenant data into OPA ({type(exc).__name__})"
            ) from exc
