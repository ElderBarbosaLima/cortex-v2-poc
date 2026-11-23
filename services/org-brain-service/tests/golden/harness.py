"""Golden-set harness (issues 25–26): replay a workflow's timeline through the
real API and compare every pack, absence, verify result and outcome with
`expected.json`. Generic over packs: the timeline's step kinds are the same
(seed, process-*, correct-*, approve-*, retire-*); the pack gives only an
`ideal_output(case)` that satisfies any rule it can ask for, and optionally
`build_cases` (when a case is assembled from several files) and `extra_check`
(per-pack expectations such as payer resolution or routing).

Every mismatch is collected, so one run reports them all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from arbitr_test_utils.network import allow_network
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import tenant_session
from app.models import Status
from app.seed.tenants import Seed
from app.services import ledger

AGENT_KEY, DESK_KEY = "golden-agent-key", "golden-desk-key"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@dataclass
class GoldenRun:
    workflow: str
    data_dir: Path
    api: TestClient
    seed: Seed
    ideal_output: Callable[[dict[str, Any]], dict[str, Any]]
    build_cases: Callable[[dict[str, dict[str, Any]]], dict[str, dict[str, Any]]] | None = None
    extra_check: (
        Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any]], list[str]] | None
    ) = None
    mismatches: list[str] = field(default_factory=list[str])
    checked: dict[str, int] = field(default_factory=dict[str, int])
    packs: dict[str, str] = field(default_factory=dict[str, str])  # case_id -> pack_id
    learned: dict[str, str] = field(default_factory=dict[str, str])  # rule key -> record id
    review_cases: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        d = self.data_dir / self.workflow
        self.timeline = json.loads((d / "timeline.json").read_text(encoding="utf-8"))
        raw = {c.get("case_id") or c["payment_id"]: c for c in _jsonl(d / "cases.jsonl")}
        self.cases = self.build_cases(raw) if self.build_cases else raw
        self.corrections = {c["correction_id"]: c for c in _jsonl(d / "corrections.jsonl")}
        self.expected = {
            e["case_id"]: e for e in json.loads((d / "expected.json").read_text(encoding="utf-8"))
        }
        rules = json.loads((d / "rules.json").read_text(encoding="utf-8"))
        self.rules = {r["record_id"]: r["key"] for r in rules}
        tenant_key = self.timeline["request_scope"].split("/")[0]
        self.foreign = [r["record_id"] for r in rules if r["tenant"] != tenant_key]
        self.foreign_tenants = {r["tenant"] for r in rules if r["tenant"] != tenant_key}
        self.tenant = self.seed.node_ids[tenant_key]
        self.scope = self.seed.node_ids[self.timeline["request_scope"]]

    # ── plumbing ────────────────────────────────────────────────────────────

    def _call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        key: str = AGENT_KEY,
        user: str | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {key}"}
        if user:
            headers["X-End-User-Id"] = self.seed.people[user]
        with allow_network():
            return self.api.request(method, path, json=body, headers=headers, params=params)

    def _miss(self, what: str) -> None:
        self.mismatches.append(what)

    def _count(self, what: str) -> None:
        self.checked[what] = self.checked.get(what, 0) + 1

    def _key(self, record_id: str | None) -> str | None:
        if record_id is None:
            return None
        return self.rules.get(record_id) or next(
            (k for k, v in self.learned.items() if v == record_id), f"?{record_id}"
        )

    # ── steps ───────────────────────────────────────────────────────────────

    def _process(self, step: dict[str, Any]) -> None:
        for case_id in step["cases"]:
            exp = self.expected[case_id]
            resp = self._call(
                "POST",
                "/v1/cortex/context-packs",
                {
                    "domain": self.workflow,
                    "case": self.cases[case_id],
                    "scope": self.scope,
                    "purpose": self.timeline["purpose"],
                    "as_of": exp["as_of"],
                },
            )
            if resp.status_code != 201:
                self._miss(f"{case_id}: pack {resp.status_code} {resp.text[:200]}")
                continue
            pack = resp.json()
            self.packs[case_id] = pack["pack_id"]
            rules = [r for r in pack["records"] if r["kind"] == "rule"]
            kept = sorted(
                self._key(r["record_id"]) or "" for r in rules if r["role"] != "overridden"
            )
            overridden = {
                self._key(r["record_id"]): self._key(r["overridden_by"])
                for r in rules
                if r["role"] == "overridden"
            }
            self._count("packs")
            if kept != sorted(exp["expected_rules"]):
                self._miss(f"{case_id}: rules {kept} != expected {sorted(exp['expected_rules'])}")
            if overridden != exp["overridden"]:
                self._miss(f"{case_id}: overridden {overridden} != {exp['overridden']}")
            if self.extra_check:
                self._count("extra checks")
                for problem in self.extra_check(case_id, self.cases[case_id], exp, pack):
                    self._miss(f"{case_id}: {problem}")
        self._verify_after(step["step"])

    def _verify_after(self, step_name: str) -> None:
        for case_id, pack_id in self.packs.items():
            for exp in self.expected[case_id]["verify"]:
                if exp["after"] != step_name:
                    continue
                resp = self._call(
                    "POST",
                    "/v1/cortex/verify",
                    {
                        "pack_id": pack_id,
                        "case": self.cases[case_id],
                        "output": self.ideal_output(self.cases[case_id]),
                    },
                )
                got = resp.json()
                self._count("verifications")
                if got.get("result") != exp["result"]:
                    self._miss(f"{case_id} after {step_name}: {got} != {exp}")
                    continue
                if exp["result"] == "hold":
                    reasons = {(r["reason"], self._key(r["record_id"])) for r in got["reasons"]}
                    if (exp["reason"], exp["rule"]) not in reasons:
                        self._miss(f"{case_id} after {step_name}: {reasons} lacks {exp}")

    def _correct(self, step: dict[str, Any]) -> None:
        correction = self.corrections[step["correction"]]
        case = self.cases[correction["case_id"]]
        resp = self._call(
            "POST",
            "/v1/cortex/corrections",
            {
                "pack_id": self.packs[correction["case_id"]],
                "domain": self.workflow,
                "case_id": correction["case_id"],
                "case": case,
                "correction": correction,
                "idempotency_key": f"golden-{correction['correction_id']}",
            },
            user=correction["reviewer"],
        )
        body = resp.json()
        self._count("corrections")
        if resp.status_code != 202 or not body.get("proposed_record_id"):
            self._miss(f"{step['correction']}: {resp.status_code} {body}")
            return
        self.review_cases.append(body["review_case_id"])
        expected = correction["expected_rule"]
        with allow_network(), tenant_session(self.tenant) as s:
            row = ledger.latest(s, body["proposed_record_id"])
            proposal_body = ledger.open_body(s, row)
        got = {
            "scope": row.scope,
            "entities": sorted(row.entities),
            "enforcement": row.enforcement,
            "authority": row.authority,
            "status": row.status,
            "asserted_by": row.asserted_by["type"],
            "targets": sorted(proposal_body["targets"]),
            "condition": proposal_body["condition"],
        }
        want = {
            "scope": self.seed.node_ids[expected["scope"]],
            "entities": sorted(expected["entities"]),
            "enforcement": expected["enforcement"],
            "authority": expected["authority"],
            "status": expected["status"],
            "asserted_by": expected["asserted_by"]["type"],
            "targets": sorted(expected["body"]["targets"]),
            "condition": expected["body"]["condition"],
        }
        if got != want:
            self._miss(f"{step['correction']}: proposal {got} != expected {want}")

    def _approve(self, step: dict[str, Any]) -> None:
        cases = sorted(set(self.review_cases))
        if len(cases) != 1:
            self._miss(f"{step['step']}: corrections opened {len(cases)} review cases, not 1")
            return
        resp = self._call(
            "POST",
            f"/v1/cortex/review-cases/{cases[0]}/approve",
            {"authority": step["authority"], "enforcement": step["enforcement"]},
            key=DESK_KEY,
            user=step["approver"],
        )
        if resp.status_code != 200:
            self._miss(f"{step['step']}: approve {resp.status_code} {resp.text[:200]}")
            return
        approved = [r for r in resp.json()["records"] if r["outcome"] == "approved"]
        self.learned[step["rule"]] = approved[0]["record_id"]
        detail = self._call(
            "GET",
            f"/v1/cortex/review-cases/{cases[0]}",
            key=DESK_KEY,
            user=step["approver"],
            params={"purpose": "rule_review"},
        ).json()
        outcomes = sorted(p["outcome"] for p in detail.get("proposals", []))
        self._count("outcomes")
        want = sorted(["approved"] + ["superseded"] * (len(outcomes) - 1))
        if outcomes != want or detail.get("status") != "decided":
            self._miss(f"{step['step']}: outcomes {outcomes} / {detail.get('status')}")
        self._verify_after(step["step"])

    def _retire(self, step: dict[str, Any]) -> None:
        record_id = next(rid for rid, key in self.rules.items() if key == step["rule"])
        with allow_network(), tenant_session(self.tenant) as s:
            # The owner retires it in the ledger; the projection is left lagging
            # (the outbox is not drained): verify must read the ledger.
            ledger.transition(s, record_id=record_id, to=Status.DEPRECATED)
        self._verify_after(step["step"])

    def run(self) -> GoldenRun:
        for step in self.timeline["steps"]:
            kind = step["step"].split("-", 1)[0]
            if kind == "seed":
                continue  # loaded by `make seed` (app/seed/rules.py) before the run
            {
                "process": self._process,
                "correct": self._correct,
                "approve": self._approve,
                "retire": self._retire,
            }[kind](step)
        self._absent_elsewhere()
        return self

    def _absent_elsewhere(self) -> None:
        """Rules seeded for another tenant exist there and are invisible here (RLS)."""
        visible = self._count_rules(self.tenant, self.foreign)
        there = sum(
            self._count_rules(self.seed.node_ids[t], self.foreign) for t in self.foreign_tenants
        )
        self._count("isolation")
        if visible or there != len(self.foreign):
            self._miss(f"isolation: {visible} foreign rule(s) visible here, {there} found there")

    @staticmethod
    def _count_rules(tenant_id: str, record_ids: list[str]) -> int:
        with allow_network(), tenant_session(tenant_id) as s:
            return int(
                s.execute(
                    text(
                        "SELECT count(DISTINCT record_id) FROM memory_record "
                        "WHERE record_id = ANY(CAST(:r AS uuid[]))"
                    ),
                    {"r": record_ids},
                ).scalar_one()
            )

    def summary(self) -> str:
        return f"{self.workflow}: {self.checked} — {len(self.mismatches)} mismatch(es)"
