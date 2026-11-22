"""A made-up domain ("widgets") the core is tested with until the real packs exist
(issues 25–26), and after: core tests never depend on invoices or payments.

Cases are widget orders; the entity is the widget; a reviewer correction says
"this widget's orders need this label".
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from app.domain_packs.base import EntityMatch, RuleBody, RuleDraft

KNOWN_WIDGETS = {"W-1", "W-2", "W-3"}
# Codes integration tests mint per test (W-<8 hex>) so their records never meet.
_TEST_WIDGET = re.compile(r"^W-[0-9a-f]{8}$")


class WidgetRuleBody(RuleBody):
    label: str | None = None


class WidgetCorrection(BaseModel):
    case_id: str
    target: str
    after: str
    reviewer: str


class FakeWidgetPack:
    name = "widgets"
    entity_types = frozenset({"widget"})
    condition_variables = ("order",)
    condition_fields = MappingProxyType(
        {"order": frozenset({"order_id", "widget_code", "colour", "qty", "order_date"})}
    )
    entity_fields = MappingProxyType({"order.widget_code": "widget"})
    targets = frozenset({"order.label", "order.colour_check"})
    rule_body_schema = WidgetRuleBody
    correction_schema = WidgetCorrection

    def resolve_entities(self, case: Mapping[str, Any]) -> list[EntityMatch]:
        code = str(case.get("widget_code", ""))
        if code in KNOWN_WIDGETS or _TEST_WIDGET.match(code):
            return [EntityMatch(f"widget:{code}", "high", {"source": "widget_code", "value": code})]
        return [EntityMatch(None, "none", {"source": "widget_code", "value": code})]

    def condition_context(
        self, case: Mapping[str, Any], entities: Sequence[EntityMatch]
    ) -> dict[str, Any]:
        order = {k: case[k] for k in self.condition_fields["order"] if k in case}
        order["order_date"] = dt.date.fromisoformat(str(case["order_date"]))
        return {"order": order}

    def correction_to_rule(self, correction: BaseModel, *, case: Mapping[str, Any]) -> RuleDraft:
        c = WidgetCorrection.model_validate(correction.model_dump())
        code = str(case["widget_code"])
        body = {
            "text_ja": f"ウィジェット {code} の注文には「{c.after}」ラベルを付ける。",
            "targets": [c.target],
            "condition": f'order.widget_code == "{code}"',
            "label": c.after,
        }
        return RuleDraft(body=body, entities=(f"widget:{code}",))

    def search_text(self, case: Mapping[str, Any]) -> str:
        return f"ウィジェット {case.get('widget_code', '')} {case.get('colour', '')}"

    def check_output(self, output: Mapping[str, Any], rules: Sequence[RuleBody]) -> list[str]:
        problems: list[str] = []
        for rule in rules:
            label = getattr(rule, "label", None)
            if "order.label" in rule.targets and label and output.get("label") != label:
                problems.append(f"label must be {label!r}")
        return problems


PACK = FakeWidgetPack()

CASES: list[dict[str, Any]] = [
    {"order_id": "O-1", "widget_code": "W-1", "colour": "赤", "qty": 3, "order_date": "2026-11-02"},
    {
        "order_id": "O-2",
        "widget_code": "W-2",
        "colour": "青",
        "qty": 10,
        "order_date": "2026-11-03",
    },
    {"order_id": "O-3", "widget_code": "W-9", "colour": "緑", "qty": 1, "order_date": "2026-11-04"},
]
CORRECTION = {"case_id": "O-1", "target": "order.label", "after": "取扱注意", "reviewer": "R-001"}
