"""End to end on the running stack, both workflows, public API only (issue 29).

`make e2e` runs the phases in order, with the admin steps in between:

    before         happy case · scenarios 2, 3, 4 · correction → approval by the named
                   owner → next applicable case · unrelated case · verify ·
                   attribution · evidence export
    (make reindex) criterion (a)
    after-reindex  the same packs again: identical · retire the scenario 4 rule →
                   verify holds the old pack, the new pack drops it
    (make erase-record, for each learned rule)
    after-erasure  the old pack stays whole but the rule's body is gone · the record
                   is erased · new packs drop it · verify holds
    report         one report per workflow, every check named

Each phase reads and writes /state (./.e2e on the host). Runs in the
org-brain-service image (network namespace of the stack): the API is
http://127.0.0.1:5008. Needs a fresh stack (`make stop && make dev`): the
scenario 4 rule is retired by the run itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("CORTEX_E2E_API_URL", "http://127.0.0.1:5008")
DATA = Path(os.environ.get("CORTEX_E2E_DATA_DIR", "/data/synthetic"))
STATE = Path(os.environ.get("CORTEX_E2E_STATE_DIR", "/state"))
AGENT_KEY = os.environ["CORTEX_E2E_AGENT_KEY"]
DESK_KEY = os.environ["CORTEX_DESK_API_KEY"]
WORKFLOWS = ("custom_invoices", "cash_application")
REVIEW = "rule_review"

Json = dict[str, Any]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[Json]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


TENANT = _load(DATA / "tenant.json")
NODES = {n["key"]: n["obj_id"] for n in TENANT["nodes"]}
PEOPLE = {p["key"]: p["obj_id"] for p in TENANT["people"]}


class Workflow:
    def __init__(self, name: str) -> None:
        d = DATA / name
        self.name = name
        self.timeline = _load(d / "timeline.json")
        self.expected = {e["case_id"]: e for e in _load(d / "expected.json")}
        self.rules = {r["record_id"]: r["key"] for r in _load(d / "rules.json")}
        self.rule_id = {v: k for k, v in self.rules.items()}
        raw = {c.get("case_id") or c["payment_id"]: c for c in _jsonl(d / "cases.jsonl")}
        if (d / "invoices.json").exists():  # a payment is matched with an open invoice
            invoices = {i["invoice_id"]: i for i in _load(d / "invoices.json")}
            self.cases = {
                k: {"payment": p, "invoice": invoices[p["open_invoices"][0]]} for k, p in raw.items()
            }
        else:
            self.cases = raw
        self.corrections = {c["correction_id"]: c for c in _jsonl(d / "corrections.jsonl")}
        self.scope = NODES[self.timeline["request_scope"]]
        self.tenant = NODES[self.timeline["request_scope"].split("/")[0]]
        self.purpose = self.timeline["purpose"]
        steps = self.timeline["steps"]
        self.approve_step = next(s for s in steps if s["step"].startswith("approve-"))
        self.retire_step = next(s for s in steps if s["step"].startswith("retire-"))
        self.correction_ids = [s["correction"] for s in steps if s["step"].startswith("correct-")]

    def first(self, *, group: str, batch: str, has: str | None = None) -> str:
        for case_id, e in self.expected.items():
            if e["group"] == group and e["batch"] == batch and (has is None or has in e["expected_rules"]):
                return case_id
        raise LookupError(f"{self.name}: no {group} case in batch {batch}")

    def ideal_output(self, case_id: str) -> Json:
        case = self.cases[case_id]
        if "payment" in case:  # cash_application: the expected route, the evidence, a fee if auto
            auto = self.expected[case_id]["route"] == "auto"
            diff = int(case["invoice"]["total"]) - int(case["payment"]["amount"])
            return {
                "mode": "auto" if auto else "review",
                "evidence": {"payment_id": case_id, "invoice_id": case["invoice"]["invoice_id"]},
                "difference_treatment": ("fee" if diff else "none") if auto else "review",
            }
        issuer = _load(DATA / self.name / "customers.json")["issuer"]  # custom_invoices
        return {
            "registration_number": issuer["registration_number"],
            "tax_breakdown": case["tax_by_rate"],
            "remarks": {
                "payment_terms": "月末締め翌月末払い",
                "department_code": "D-77",
                "po_number": f"注文番号: {case['po_number']}",
            },
            "attachments": ["kenshusho"],
        }


class Run:
    def __init__(self, wf: Workflow, state: Json) -> None:
        self.wf = wf
        self.state = state
        self.checks: list[Json] = state.setdefault("checks", [])
        self.agent = httpx.Client(base_url=API, headers={"authorization": f"Bearer {AGENT_KEY}"}, timeout=30)
        self.desk = httpx.Client(base_url=API, headers={"authorization": f"Bearer {DESK_KEY}"}, timeout=30)

    # ── plumbing ────────────────────────────────────────────────────────────

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f" — {detail}"))
        return bool(ok)

    def _ok(self, resp: httpx.Response, status: int = 200) -> Json:
        if resp.status_code != status:
            raise RuntimeError(f"{resp.request.method} {resp.request.url.path}: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def pack(self, case_id: str) -> Json:
        return self._ok(
            self.agent.post(
                "/v1/cortex/context-packs",
                json={
                    "domain": self.wf.name,
                    "case": self.wf.cases[case_id],
                    "scope": self.wf.scope,
                    "purpose": self.wf.purpose,
                    "as_of": self.wf.expected[case_id]["as_of"],
                },
            ),
            201,
        )

    def verify(self, pack_id: str, case_id: str) -> Json:
        return self._ok(
            self.agent.post(
                "/v1/cortex/verify",
                json={"pack_id": pack_id, "case": self.wf.cases[case_id], "output": self.wf.ideal_output(case_id)},
            )
        )

    def key(self, record_id: str | None) -> str | None:
        if record_id is None:
            return None
        if record_id == self.state.get("learned_id"):
            return self.wf.approve_step["rule"]
        return self.wf.rules.get(record_id, record_id)

    def kept(self, pack: Json) -> list[str]:
        return sorted(str(self.key(r["record_id"])) for r in pack["records"] if r["kind"] == "rule" and r["role"] != "overridden")

    def overridden(self, pack: Json) -> dict[str, str | None]:
        return {
            str(self.key(r["record_id"])): self.key(r["overridden_by"])
            for r in pack["records"]
            if r["kind"] == "rule" and r["role"] == "overridden"
        }

    def roles(self, pack: Json) -> list[tuple[str, str]]:
        return sorted((r["record_id"], r["role"]) for r in pack["records"] if r["kind"] == "rule")

    def as_owner(self) -> dict[str, str]:
        return {"x-end-user-id": PEOPLE[self.wf.approve_step["approver"]]}

    # ── phases ──────────────────────────────────────────────────────────────

    def before(self) -> None:
        wf = self.wf
        s4 = wf.first(group="scenario_4", batch="A", has=wf.retire_step["rule"])
        retired = self.desk.get(
            f"/v1/cortex/records/{wf.rule_id[wf.retire_step['rule']]}", params={"purpose": REVIEW}, headers=self.as_owner()
        )
        if retired.status_code != 200 or retired.json()["status"] != "approved":
            raise RuntimeError("the scenario 4 rule is not approved: run on a fresh stack (make stop && make dev)")

        happy = wf.first(group="happy", batch="A")
        pack = self.pack(happy)
        self.check("happy case: the pack holds exactly the expected rules", self.kept(pack) == sorted(wf.expected[happy]["expected_rules"]), f"{self.kept(pack)}")
        self.check("happy case: verify passes", self.verify(pack["pack_id"], happy)["result"] == "pass")
        self.state["reindex"] = {happy: self.roles(pack)}

        s2 = wf.first(group="scenario_2", batch="A")
        pack = self.pack(s2)
        want = wf.expected[s2]["overridden"]
        self.check(
            "scenario 2: the local default loses to the global prohibition",
            bool(want) and self.overridden(pack) == want,
            f"{self.overridden(pack)} vs {want}",
        )
        self.state["reindex"][s2] = self.roles(pack)

        s3 = wf.first(group="scenario_3", batch="A")
        pack = self.pack(s3)
        clause = next(k for k in wf.expected[s3]["expected_rules"] if "-C-" in k)
        self.check(f"scenario 3: the contract clause {clause} applies without shared words", clause in self.kept(pack), f"{self.kept(pack)}")
        self.state["reindex"][s3] = self.roles(pack)

        pack = self.pack(s4)
        self.check(f"scenario 4 (before): {wf.retire_step['rule']} is in the pack", wf.retire_step["rule"] in self.kept(pack))
        self.state["s4"] = {"case": s4, "pack": pack["pack_id"]}

        # correction → proposal → the named owner approves
        cid = wf.correction_ids[0]
        correction = wf.corrections[cid]
        served = self.pack(correction["case_id"])
        accepted = self._ok(
            self.agent.post(
                "/v1/cortex/corrections",
                headers={"x-end-user-id": PEOPLE[correction["reviewer"]]},
                json={
                    "pack_id": served["pack_id"],
                    "domain": wf.name,
                    "case_id": correction["case_id"],
                    "case": wf.cases[correction["case_id"]],
                    "correction": correction,
                    "idempotency_key": f"e2e-{cid}-{uuid.uuid4().hex[:8]}",
                },
            ),
            202,
        )
        self.check("correction: a rule is proposed and a review case opened", bool(accepted.get("proposed_record_id") and accepted.get("review_case_id")))
        step = wf.approve_step
        decided = self._ok(
            self.desk.post(
                f"/v1/cortex/review-cases/{accepted['review_case_id']}/approve",
                headers=self.as_owner(),
                json={"record_id": accepted["proposed_record_id"], "authority": step["authority"], "enforcement": step["enforcement"]},
            )
        )
        approved = [r for r in decided["records"] if r["outcome"] == "approved"]
        self.check(f"approval: {step['approver']} approves it ({step['enforcement']}, authority {step['authority']})", [r["record_id"] for r in approved] == [accepted["proposed_record_id"]])
        self.state["learned_id"] = accepted["proposed_record_id"]
        self.state["review_case"] = accepted["review_case_id"]
        learned = step["rule"]

        nxt = wf.first(group="learned_rule", batch="B", has=learned)
        pack = self.pack(nxt)
        self.check(f"next applicable case ({nxt}): the learned rule is in its pack", learned in self.kept(pack), f"{self.kept(pack)}")
        self.check(f"next applicable case ({nxt}): verify passes", self.verify(pack["pack_id"], nxt)["result"] == "pass")
        self.state["next"] = {"case": nxt, "pack": pack["pack_id"], "policy_revision": pack["policy_revision"]}
        self.state["reindex"][nxt] = self.roles(pack)

        unrelated = wf.first(group="unrelated", batch="B")
        pack = self.pack(unrelated)
        self.check(f"unrelated case ({unrelated}): the learned rule is not in its pack", learned not in self.kept(pack), f"{self.kept(pack)}")

        # attributable: correction, corrector, approver, policy revision
        detail = self._ok(self.desk.get(f"/v1/cortex/review-cases/{accepted['review_case_id']}", params={"purpose": REVIEW}, headers=self.as_owner()))
        prop = next(p for p in detail["proposals"] if p["record_id"] == accepted["proposed_record_id"])
        attributed = (
            (prop["approved_by"] or {}).get("user_id") == PEOPLE[step["approver"]]
            and bool((prop["approved_by"] or {}).get("decision_id"))
            and PEOPLE[correction["reviewer"]] in prop["source"].get("contributors", [])
            and bool(prop["derived_from"])
            and bool(self.state["next"]["policy_revision"])
        )
        self.check("attributable: the applied rule names its correction, corrector, approver and policy revision", attributed, json.dumps(prop["approved_by"]))

        exported = self._ok(self.agent.post(f"/v1/cortex/context-packs/{self.state['next']['pack']}/export"), 202)
        for _ in range(60):
            if exported["status"] == "exported":
                break
            time.sleep(1)
            exported = self._ok(self.agent.get(f"/v1/cortex/context-packs/{self.state['next']['pack']}/export"))
        self.check(
            "export: the pack's evidence is archived under Object Lock",
            exported["status"] == "exported" and exported["retention_mode"] == "COMPLIANCE" and bool(exported["manifest_sha256"]),
            json.dumps({k: exported.get(k) for k in ("status", "retention_mode")}),
        )

    def after_reindex(self) -> None:
        wf = self.wf
        for case_id, roles in self.state["reindex"].items():
            again = self.roles(self.pack(case_id))
            self.check(f"criterion (a): after make reindex, {case_id} gives the same pack", again == [tuple(r) for r in roles], f"{again}")

        rule = wf.retire_step["rule"]
        self._ok(self.desk.post(f"/v1/cortex/records/{wf.rule_id[rule]}/retire", headers={"x-end-user-id": PEOPLE[wf.retire_step["by"]]}, json={"reason": wf.retire_step["reason_ja"]}))
        held = self.verify(self.state["s4"]["pack"], self.state["s4"]["case"])
        self.check(
            f"scenario 4: after retiring {rule}, verify holds the earlier pack",
            held["result"] == "hold" and {(h["reason"], h["record_id"]) for h in held["reasons"]} >= {("rule_no_longer_approved", wf.rule_id[rule])},
            json.dumps(held["reasons"]),
        )
        later = wf.first(group="scenario_4", batch="C")
        self.check(f"scenario 4: {rule} is not in a later pack ({later})", rule not in self.kept(self.pack(later)))

    def after_erasure(self) -> None:
        learned = self.state["learned_id"]
        nxt = self.state["next"]
        old = self._ok(self.agent.get(f"/v1/cortex/context-packs/{nxt['pack']}"))
        item = next(r for r in old["records"] if r["record_id"] == learned)
        others = [r for r in old["records"] if r["record_id"] != learned]
        self.check("erasure: the earlier pack stays whole, the erased rule's body is gone", item["body"] is None and item["body_erased"] and bool(item["body_hash"]))
        self.check("erasure: the pack's other bodies are still readable", all(r["body"] is not None for r in others))
        record = self._ok(self.desk.get(f"/v1/cortex/records/{learned}", params={"purpose": REVIEW}, headers=self.as_owner()))
        self.check("erasure: the record is erased in the ledger", record["status"] == "erased", record["status"])
        self.check("erasure: a new pack no longer carries it", self.wf.approve_step["rule"] not in self.kept(self.pack(nxt["case"])))
        # A constraint shows as rule_no_longer_approved; a default (the fee rule)
        # by the output no longer being explained by any rule. Either way: no pass.
        held = self.verify(nxt["pack"], nxt["case"])
        self.check("erasure: verify no longer passes the earlier pack", held["result"] == "hold", json.dumps(held["reasons"]))


def _state_path(name: str) -> Path:
    return STATE / f"{name}.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["before", "after-reindex", "after-erasure", "report"])
    phase = parser.parse_args().phase
    STATE.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in WORKFLOWS:
        path = _state_path(name)
        if phase == "before":
            state: Json = {"workflow": name}
        else:
            state = _load(path)
        if phase == "report":
            checks = state.get("checks", [])
            bad = [c for c in checks if not c["ok"]]
            lines = [f"# e2e report — {name}", "", f"{len(checks) - len(bad)}/{len(checks)} checks passed", ""]
            lines += [f"- {'✓' if c['ok'] else '✗'} {c['name']}" + ("" if c["ok"] else f" — {c['detail']}") for c in checks]
            (STATE / f"report-{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n".join(lines) + "\n")
            failed += len(bad)
            continue
        print(f"== {name}: {phase}")
        run = Run(Workflow(name), state)
        start = len(run.checks)
        try:
            {"before": run.before, "after-reindex": run.after_reindex, "after-erasure": run.after_erasure}[phase]()
        except Exception as exc:  # noqa: BLE001 -- recorded as a failed check, then the run stops
            run.check(f"{phase}: the phase ran to the end", False, f"{type(exc).__name__}: {exc}")
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        failed += sum(1 for c in run.checks[start:] if not c["ok"])
        if phase == "after-reindex":
            with (STATE / "erase.txt").open("a", encoding="utf-8") as f:
                f.write(f"{run.wf.tenant} {state['learned_id']}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
