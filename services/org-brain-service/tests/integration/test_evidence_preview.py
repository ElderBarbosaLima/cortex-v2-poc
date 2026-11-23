"""Evidence and the approval preview (issue 52): the owner sees the correction
and the case behind a proposal, and what approving it changes for that case."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_owner_alerts import DESK, World, _call, _case, _pack, _rule, world  # noqa: F401

pytestmark = pytest.mark.integration


def _correct(w: World, code: str, order: str) -> dict[str, object]:
    pack = _pack(w, code, order)
    resp = _call(
        w,
        "POST",
        "/v1/cortex/corrections",
        user="R-001",
        body={
            "pack_id": pack["pack_id"],
            "domain": "widgets",
            "case_id": order,
            "case": _case(code, order),
            "correction": {
                "case_id": order,
                "target": "order.label",
                "after": "取扱注意",
                "reviewer": "R-001",
            },
            "idempotency_key": f"ev-{uuid.uuid4().hex}",
        },
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_the_owner_sees_the_correction_and_the_case(world: World) -> None:
    code = f"W-{uuid.uuid4().hex[:8]}"
    accepted = _correct(world, code, "O-EV-1")

    detail = _call(
        world,
        "GET",
        f"/v1/cortex/review-cases/{accepted['review_case_id']}",
        key=DESK,
        user="OWN-001",
        params={"purpose": "rule_review"},
    ).json()
    evidence = detail["proposals"][0]["evidence"]

    assert len(evidence) == 1
    assert evidence[0]["case_ref"] == "O-EV-1"
    assert evidence[0]["decided_by"] == world.seed.people["R-001"]
    assert evidence[0]["correction"]["after"] == "取扱注意"
    assert evidence[0]["case"] == _case(code, "O-EV-1")


def test_the_preview_shows_the_rule_joining_the_pack(world: World) -> None:
    code = f"W-{uuid.uuid4().hex[:8]}"
    existing = _rule(world, code, text_ja="既存の既定ラベル。")
    accepted = _correct(world, code, "O-EV-2")
    case_id, proposal = accepted["review_case_id"], accepted["proposed_record_id"]

    def preview(**params: object) -> dict[str, object]:
        resp = _call(
            world,
            "GET",
            f"/v1/cortex/review-cases/{case_id}/preview",
            key=DESK,
            user="OWN-001",
            params={"purpose": "rule_review", "record_id": proposal, **params},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    as_default = preview()
    as_mandatory = preview(enforcement="mandatory", authority=30)

    def roles(rules: object) -> dict[str, tuple[str, bool]]:
        return {r["record_id"]: (r["role"], r["proposed"]) for r in rules}  # type: ignore[union-attr]

    assert as_default["available"] and as_default["applies"] and as_default["case_ref"] == "O-EV-2"
    assert proposal not in roles(as_default["before"])
    assert roles(as_default["before"])[existing] == ("applied", False)
    # Approved as a mandatory rule, the proposal wins the label over the old default.
    assert roles(as_mandatory["after"])[proposal] == ("constraint", True)
    assert roles(as_mandatory["after"])[existing] == ("overridden", False)
