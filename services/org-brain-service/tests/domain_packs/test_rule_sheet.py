"""Customer rules from a spreadsheet (issue 47): the sample sheet through its
column guide. Readable rows become import items in the two-layer format; the
others are reported with their row number, never guessed."""

from __future__ import annotations

import datetime as dt
import json

from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.domain_packs.cash_allocation.sheet import read_sheet
from app.models import Kind
from app.services.admission import Proposal, validate_proposal
from tests.test_identity_and_seed import SEED_DIR

register_pack(PACK)
SAMPLE = SEED_DIR.parent / "examples" / "rule-sheet"


def test_the_sample_sheet_reads_five_rules_and_reports_two_rows() -> None:
    sheet = read_sheet(
        (SAMPLE / "rules.csv").read_text(encoding="utf-8"),
        json.loads((SAMPLE / "column-guide.json").read_text(encoding="utf-8")),
        scope="00000000-0000-0000-0000-000000000001",
    )

    assert sheet.rows == [2, 3, 4, 5, 6]
    assert [i["body"]["exception"] for i in sheet.items] == [
        "bank_fee",
        "combined_payment",
        "tax_difference",
        "duplicate_payment",
        "partial_payment",
    ]
    assert sheet.items[0]["body"]["engine"] == {
        "expected_fee": 660,
        "max_fee": 880,
        "treatment": "fee_expense",
    }
    assert sheet.items[4]["body"]["engine"] == {"action": "instalments", "instalments": 3}
    assert sheet.items[2]["valid_to"] == "2027-03-31"
    assert [p["row"] for p in sheet.problems] == [
        7,
        8,
    ]  # ¥990 is above the fee limit; 前受金 is unknown
    assert "less than or equal to 880" in sheet.problems[0]["detail"]
    for item in sheet.items:  # every item is admissible as is
        proposal = Proposal(
            domain="cash_allocation",
            kind=Kind.RULE,
            schema_version=item["schema_version"],
            body=item["body"],
            scope=item["scope"],
            entities=tuple(item["entities"]),
            enforcement=item["enforcement"],
            authority=item["authority"],
            sensitivity=item["sensitivity"],
            valid_from=dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
        )
        assert validate_proposal(proposal)[1] == [], item["entities"]
