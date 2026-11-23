"""`make seed` also loads each domain pack's initial rules (issue 25).

For every registered pack with `<seed_dir>/<pack name>/rules.json`, each rule
is appended to its tenant's ledger as an approved record with its fixed
`record_id`, unless that record already exists (so seeding twice is a no-op).
Rules are validated by the admission checks first: a seed rule that the gate
would refuse is not loaded. Generic over packs: no workflow is named here.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import tenant_session
from app.domain_packs.base import get_pack, registered_packs
from app.models import Kind, Status
from app.seed.tenants import Seed
from app.services import admission, ledger
from app.utils.business_dates import business_start


@dataclass(frozen=True)
class Loaded:
    pack: str
    added: int
    present: int


def _approved_by(seed: Seed, doc: dict[str, Any]) -> dict[str, Any]:
    approved: dict[str, Any] = doc.get("approved_by") or {}
    person = approved.get("user")
    return {
        "approver_type": "user",
        "user_id": seed.people[person] if person else None,
        "decision_id": None,
        "via": "seed",
    }


def load_pack_rules(seed: Seed, seed_dir: Path) -> list[Loaded]:
    tenants = {t.key: t.tenant_id for t in seed.tenants}
    results: list[Loaded] = []
    for name in registered_packs():
        path = seed_dir / name / "rules.json"
        if not path.exists():
            continue
        added = present = 0
        for doc in json.loads(path.read_text(encoding="utf-8")):
            tenant_id = tenants[doc["tenant"]]
            proposal = admission.Proposal(
                domain=name,
                kind=Kind(doc["kind"]),
                schema_version=doc["schema_version"],
                body=doc["body"],
                scope=seed.node_ids[doc["scope"]],
                entities=tuple(doc["entities"]),
                enforcement=doc["enforcement"],
                authority=doc["authority"],
                sensitivity=doc["sensitivity"],
                valid_from=business_start(dt.date.fromisoformat(doc["valid_from"])),
            )
            _, problems = admission.validate_proposal(proposal)
            if problems:
                raise ValueError(f"seed rule {doc['key']} is not admissible: {problems}")
            with tenant_session(tenant_id) as session:
                try:
                    ledger.latest(session, doc["record_id"])
                    present += 1
                    continue
                except ledger.RecordNotFoundError:
                    pass
                ledger.append_new(
                    session,
                    tenant_id=tenant_id,
                    record_id=doc["record_id"],
                    status=Status(doc["status"]),
                    approved_by=_approved_by(seed, doc),
                    draft=ledger.RecordDraft(
                        kind=proposal.kind,
                        schema_version=proposal.schema_version,
                        body=proposal.body,
                        scope=proposal.scope,
                        asserted_by=doc["asserted_by"],
                        authority=proposal.authority,
                        source=doc["source"],
                        valid_from=proposal.valid_from,
                        valid_to=(
                            business_start(dt.date.fromisoformat(doc["valid_to"]))
                            if doc.get("valid_to")
                            else None
                        ),
                        enforcement=proposal.enforcement,
                        entities=proposal.entities,
                        assurance_level="reviewed",
                        derived_from=(),
                        sensitivity=proposal.sensitivity,
                    ),
                )
                added += 1
        results.append(Loaded(get_pack(name).name, added, present))
    return results
