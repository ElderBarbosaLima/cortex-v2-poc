"""The tenant seed: data/synthetic/tenant.json → what OPA and the service need.

In production the scope tree, grants and consumers come from client-service
(arbitr-rbac's access-context payload). In the PoC they come from the seed
file, in the same shape: UUID node ids with a materialised `path_up` (as
client-service's org_nodes.path), grants per principal, roles with their
permissions (the UAT v1 catalog for people, plus a role for consumers).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Permissions per role: the memory.* subset of the UAT v1 role catalog
# (client-service 20261008_095_seed_v1_role_catalog.py), plus a consumer role.
ROLES: dict[str, list[str]] = {
    "reviewer": ["memory.read", "memory.write"],
    "final_validator": ["memory.read", "memory.write", "memory.approve"],
    "cortex_consumer": ["memory.read", "memory.write"],
}
_GRANT_NAMESPACE = uuid.UUID("0c0b3a5e-3f1d-5c8e-9a51-20261121c0de")


@dataclass(frozen=True)
class ConsumerInfo:
    consumer_id: str
    tenant_id: str
    tenant_key: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class TenantSeed:
    tenant_id: str
    key: str
    opa_data: dict[str, Any]


@dataclass(frozen=True)
class Seed:
    tenants: tuple[TenantSeed, ...]
    consumers: dict[str, ConsumerInfo]
    node_ids: dict[str, str]  # scope key ("jp/finance") -> node UUID
    people: dict[str, str]  # person key ("OWN-001") -> UUID


def _tenant_key(node_key: str) -> str:
    return node_key.split("/", 1)[0]


def _grant_id(principal: str, node: str) -> str:
    return str(uuid.uuid5(_GRANT_NAMESPACE, f"{principal}@{node}"))


def build_seed(doc: dict[str, Any]) -> Seed:
    by_key = {n["key"]: n for n in doc["nodes"]}
    node_ids = {k: str(n["obj_id"]) for k, n in by_key.items()}
    roots = [n for n in doc["nodes"] if n["type"] == "organization"]
    tenants: list[TenantSeed] = []
    consumers: dict[str, ConsumerInfo] = {}
    for tenant in (n for n in doc["nodes"] if n["type"] == "tenant"):
        tkey = tenant["key"]
        members = [
            n for n in doc["nodes"] if n["type"] != "organization" and _tenant_key(n["key"]) == tkey
        ]
        nodes = {
            node_ids[n["key"]]: {
                "parent": node_ids[n["parent"]] if n["parent"] else None,
                "name": n["name"],
                "type": n["type"],
                "is_barrier": bool(n["is_barrier"]),
                "path_up": [node_ids[k] for k in n["path_up"]],
            }
            for n in [*roots, *members]
        }
        grants = [
            {
                "id": _grant_id(str(p["obj_id"]), node_ids[p["node"]]),
                "principal_type": "user",
                "principal_id": str(p["obj_id"]),
                "role": p["role"],
                "node": node_ids[p["node"]],
            }
            for p in doc["people"]
            if _tenant_key(p["node"]) == tkey
        ]
        tenant_consumers = [c for c in doc["consumers"] if c["tenant"] == tkey]
        grants += [
            {
                "id": _grant_id(c["key"], node_ids[tkey]),
                "principal_type": "consumer",
                "principal_id": c["key"],
                "role": "cortex_consumer",
                "node": node_ids[tkey],
            }
            for c in tenant_consumers
        ]
        tenant_id = node_ids[tkey]
        for c in tenant_consumers:
            consumers[c["key"]] = ConsumerInfo(
                c["key"], tenant_id, tkey, tuple(c.get("scopes", []))
            )
        tenants.append(
            TenantSeed(
                tenant_id=tenant_id,
                key=tkey,
                opa_data={
                    "nodes": nodes,
                    "roles": ROLES,
                    "grants": sorted(grants, key=lambda g: g["id"]),
                    "consumers": {c["key"]: {"purposes": c["purposes"]} for c in tenant_consumers},
                },
            )
        )
    people = {str(p["key"]): str(p["obj_id"]) for p in doc["people"]}
    return Seed(tuple(tenants), consumers, node_ids, people)


def load_seed(seed_dir: Path) -> Seed:
    return build_seed(json.loads((seed_dir / "tenant.json").read_text(encoding="utf-8")))
