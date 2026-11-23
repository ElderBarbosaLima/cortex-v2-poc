"""Shared pieces of the synthetic data generators (docs/tasks.md, issues 6–7).

Everything is fictitious and deterministic: a fixed seed, UUIDv5 ids, no clock.
Company names always carry a fictitious marker (架空 / サンプル / テスト) and
JPY amounts are integers. Nothing here is used at runtime by the service.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEED = 20261120
GENERATOR_VERSION = "1"
NAMESPACE = uuid.UUID("6c0b3a5e-3f1d-5c8e-9a51-20261120c0de")
FICTITIOUS_MARKERS = ("架空", "サンプル", "テスト")

JsonObj = dict[str, Any]


def rng_for(name: str) -> random.Random:
    """One independent, reproducible stream per generator."""
    return random.Random(f"{SEED}:{name}")


def stable_uuid(kind: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{key}"))


def corporate_number(rng: random.Random) -> str:
    """A well-formed but fictitious 13-digit Japanese corporate number.

    The leading digit is the check digit: 9 - (Σ digit × weight mod 9), with
    weights 1 and 2 alternating from the right over the 12 base digits.
    """
    base = [rng.randrange(10) for _ in range(12)]
    weighted = sum(d * (2 if i % 2 == 0 else 1) for i, d in enumerate(reversed(base)))
    check = 9 - (weighted % 9)
    return f"{check}{''.join(str(d) for d in base)}"


def is_valid_corporate_number(number: str) -> bool:
    if len(number) != 13 or not number.isdigit():
        return False
    base = [int(c) for c in number[1:]]
    weighted = sum(d * (2 if i % 2 == 0 else 1) for i, d in enumerate(reversed(base)))
    return int(number[0]) == 9 - (weighted % 9)


def tax_by_rate(lines: Sequence[Mapping[str, Any]]) -> dict[int, int]:
    """Qualified Invoice System rounding: one rounding (down) per tax rate per invoice."""
    subtotals: dict[int, int] = {}
    for line in lines:
        rate = int(line["tax_rate"])
        subtotals[rate] = subtotals.get(rate, 0) + int(line["amount_excl_tax"])
    return {rate: subtotal * rate // 100 for rate, subtotal in sorted(subtotals.items())}


# ── Shared tenant (scope tree, people, consumers) ────────────────────────────


@dataclass(frozen=True)
class NodeSpec:
    key: str
    name: str
    parent: str | None
    type: str
    is_barrier: bool = False


# Scope keys are "<tenant>/<path>"; the seed (issue 13) maps them to these UUIDs.
NODES: tuple[NodeSpec, ...] = (
    NodeSpec("org", "架空グループ", None, "organization"),
    NodeSpec("jp", "架空グループ日本", "org", "tenant"),
    NodeSpec("jp/finance", "経理本部", "jp", "team"),
    NodeSpec("jp/finance/q2c-invoicing", "Q2C 請求書チーム", "jp/finance", "team"),
    NodeSpec("jp/finance/q2c-cash-application", "Q2C 入金消込チーム", "jp/finance", "team"),
    NodeSpec("other", "テスト別テナント", "org", "tenant"),
    NodeSpec("other/finance", "テスト別テナント経理", "other", "team"),
    # The English example set (issue 39): the same invoicing story, in English, in a
    # tenant of its own, so switching examples never mixes the two.
    NodeSpec("en", "Fictitious Group UK (example)", "org", "tenant"),
    NodeSpec("en/finance", "Finance", "en", "team"),
    NodeSpec("en/finance/q2c-invoicing", "Q2C Invoicing team", "en/finance", "team"),
)

# People are role labels only: no names, nothing personal.
PEOPLE: tuple[JsonObj, ...] = (
    {"key": "R-001", "label": "請求書レビュアー 1", "role": "reviewer", "node": "jp/finance"},
    {"key": "R-002", "label": "請求書レビュアー 2", "role": "reviewer", "node": "jp/finance"},
    {"key": "R-101", "label": "入金消込レビュアー 1", "role": "reviewer", "node": "jp/finance"},
    {"key": "R-102", "label": "入金消込レビュアー 2", "role": "reviewer", "node": "jp/finance"},
    {"key": "OWN-001", "label": "ルール責任者", "role": "final_validator", "node": "jp/finance"},
    {
        "key": "OWN-002",
        "label": "ルール責任者（代理）",
        "role": "final_validator",
        "node": "jp/finance",
    },
    {"key": "X-001", "label": "別テナント担当", "role": "final_validator", "node": "other"},
    # English example set: at the tenant root, so its group-wide rules can be proposed and decided.
    {"key": "EN-R-001", "label": "Invoice reviewer 1", "role": "reviewer", "node": "en"},
    {"key": "EN-R-002", "label": "Invoice reviewer 2", "role": "reviewer", "node": "en"},
    {"key": "EN-OWN-001", "label": "Rule owner", "role": "final_validator", "node": "en"},
    {"key": "EN-OWN-002", "label": "Rule owner (backup)", "role": "final_validator", "node": "en"},
)

CONSUMERS: tuple[JsonObj, ...] = (
    {
        "key": "askq2c-agent",
        "tenant": "jp",
        "scopes": ["cortex:read", "cortex:write"],
        "purposes": {
            "invoice_drafting": {"sensitivity_ceiling": "confidential"},
            "cash_matching": {"sensitivity_ceiling": "confidential"},
            # Until D3 is decided, the release check on Phase 1 packs runs as this agent (issue 35).
            "payment_reconciliation": {"sensitivity_ceiling": "confidential"},
        },
    },
    {
        "key": "decision-desk",
        "tenant": "jp",
        "scopes": ["cortex:read", "cortex:write"],
        "purposes": {"rule_review": {"sensitivity_ceiling": "confidential"}},
    },
    {
        # Phase 1 IBM integration profile (issue 36): context packs only, for the
        # Payment Reconciliation subset (issue 35).
        "key": "ibm-pr-agent",
        "tenant": "jp",
        "scopes": ["cortex:pack"],
        "purposes": {"payment_reconciliation": {"sensitivity_ceiling": "confidential"}},
    },
    {
        # English example set (issue 39): the agent the "Load example cases" button acts as.
        "key": "examples-agent-en",
        "tenant": "en",
        "scopes": ["cortex:read", "cortex:write"],
        "purposes": {"invoice_drafting": {"sensitivity_ceiling": "confidential"}},
    },
    {
        "key": "decision-desk-en",
        "tenant": "en",
        "scopes": ["cortex:read", "cortex:write"],
        "purposes": {"rule_review": {"sensitivity_ceiling": "confidential"}},
    },
    {
        # The second tenant's agent: proves one tenant's key never reads another's data.
        "key": "other-agent",
        "tenant": "other",
        "scopes": ["cortex:read", "cortex:write"],
        "purposes": {"invoice_drafting": {"sensitivity_ceiling": "confidential"}},
    },
)


# The named owner and backup who decide on proposals at a scope (doc 06: an
# IBM owner plus a backup). The nearest owned node up the tree applies.
OWNERS: tuple[JsonObj, ...] = (
    {"node": "jp/finance/q2c-invoicing", "owner": "OWN-001", "backup": "OWN-002"},
    {"node": "jp/finance/q2c-cash-application", "owner": "OWN-001", "backup": "OWN-002"},
    {"node": "other", "owner": "X-001", "backup": None},
    {"node": "en", "owner": "EN-OWN-001", "backup": "EN-OWN-002"},
)


def path_up(node_key: str) -> list[str]:
    by_key = {n.key: n for n in NODES}
    path: list[str] = []
    current: str | None = node_key
    while current is not None:
        path.append(current)
        current = by_key[current].parent
    return path


def tenant_document() -> JsonObj:
    return {
        "nodes": [
            {
                "key": n.key,
                "obj_id": stable_uuid("node", n.key),
                "name": n.name,
                "parent": n.parent,
                "type": n.type,
                "is_barrier": n.is_barrier,
                "path_up": path_up(n.key),
            }
            for n in NODES
        ],
        "people": [{**p, "obj_id": stable_uuid("person", str(p["key"]))} for p in PEOPLE],
        "consumers": list(CONSUMERS),
        "owners": list(OWNERS),
    }


# ── Rules and the golden-set oracle ──────────────────────────────────────────

Context = dict[str, Any]


@dataclass(frozen=True)
class RuleSpec:
    """A seed rule: its CEL text for the system under test, and an independent
    Python predicate over the same context that the golden set is computed from."""

    key: str
    scope: str
    enforcement: str  # mandatory | prohibition | default
    authority: int
    text_ja: str
    targets: tuple[str, ...]
    condition: str
    predicate: Callable[[Context], bool]
    entities: tuple[str, ...] = ()
    tenant: str = "jp"


def rule_document(rule: RuleSpec, workflow: str, *, status: str = "approved") -> JsonObj:
    """The rule as a memory_record-shaped seed document."""
    return {
        "key": rule.key,
        "record_id": stable_uuid("record", rule.key),
        "tenant": rule.tenant,
        "kind": "rule",
        "schema_version": f"{workflow}.rule/1",
        "scope": rule.scope,
        "entities": list(rule.entities),
        "enforcement": rule.enforcement,
        "authority": rule.authority,
        "status": status,
        "valid_from": "2026-11-01",
        "valid_to": None,
        "source": {"system": "synthetic", "document": "初期ルール一覧", "location": rule.key},
        "asserted_by": {"type": "import", "id": "synthetic-seed"},
        "approved_by": {"approver_type": "user", "user": "OWN-001"},
        "sensitivity": {"class": "internal", "personal_data": False, "retention_days": 3650},
        "body": {
            "text_ja": rule.text_ja,
            "targets": list(rule.targets),
            "condition": rule.condition,
        },
    }


_CONSTRAINT_RANK = {"prohibition": 0, "mandatory": 1}


def expected_rules(
    active: Sequence[RuleSpec],
    ctx: Context,
    *,
    request_scope: str,
    entities: set[str],
    tenant: str = "jp",
) -> tuple[list[str], dict[str, str]]:
    """What a correct access gate returns (Rev. B rule 4), as the golden set states it.

    Applicable = same tenant, scope at or above the request scope (never
    sideways), no entities or one shared with the case, predicate true. Then a
    default rule loses to any constraint on a shared target (attributed to a
    prohibition before a mandatory rule); among defaults on a shared target,
    higher authority wins, then the nearer (deeper) scope.
    """
    visible = set(path_up(request_scope))
    applicable = [
        r
        for r in active
        if r.tenant == tenant
        and r.scope in visible
        and (not r.entities or set(r.entities) & entities)
        and r.predicate(ctx)
    ]

    def outranks(winner: RuleSpec, loser: RuleSpec) -> bool:
        a, b = (
            (winner.authority, len(path_up(winner.scope))),
            (loser.authority, len(path_up(loser.scope))),
        )
        return a > b

    overridden: dict[str, str] = {}
    for rule in (r for r in applicable if r.enforcement == "default"):
        rivals = [r for r in applicable if r is not rule and set(r.targets) & set(rule.targets)]
        constraints = sorted(
            (r for r in rivals if r.enforcement in _CONSTRAINT_RANK),
            key=lambda r: (_CONSTRAINT_RANK[r.enforcement], r.key),
        )
        defaults = sorted(
            (r for r in rivals if r.enforcement == "default" and outranks(r, rule)),
            key=lambda r: (-r.authority, -len(path_up(r.scope)), r.key),
        )
        winner = (constraints or defaults or [None])[0]
        if winner is not None:
            overridden[rule.key] = winner.key
    return sorted(r.key for r in applicable if r.key not in overridden), overridden


# ── Output ───────────────────────────────────────────────────────────────────


def dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")


def write_manifest(directory: Path, workflow: str, counts: Mapping[str, int]) -> None:
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "manifest.json")
    write_json(
        directory / "manifest.json",
        {
            "workflow": workflow,
            "seed": SEED,
            "generator_version": GENERATOR_VERSION,
            "counts": dict(counts),
            "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        },
    )
