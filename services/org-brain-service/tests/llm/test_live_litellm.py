"""The real LiteLLM (issue 30), opt-in: `make test-llm` (needs LITELLM_API_KEY).
Synthetic data only (devs-ai.straker.ai is a UAT environment)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from arbitr_test_utils.network import allow_network

from app.clients.llm import LiteLlmClient, LlmSettings, install_llm
from app.config import OrgBrainConfig
from app.domain_packs.base import register_pack
from app.domain_packs.custom_invoices import PACK
from app.models import Kind
from app.services import admission, writeback
from app.utils.business_dates import NO_LOWER_BOUND, business_start

pytestmark = pytest.mark.llm
KEY = os.environ.get("LITELLM_API_KEY", "")
DATA = Path(OrgBrainConfig().seed_dir) / "custom_invoices"


@pytest.fixture(scope="module")
def client() -> LiteLlmClient:
    if not KEY:
        pytest.fail("LITELLM_API_KEY is not set: put it in .env")
    cfg = OrgBrainConfig()
    return LiteLlmClient(
        LlmSettings(cfg.litellm_base_url, cfg.litellm_chat_model, cfg.litellm_embedding_model), KEY
    )


def test_the_chat_model_drafts_an_admissible_rule_from_a_correction(client: LiteLlmClient) -> None:
    register_pack(PACK)
    case = next(
        json.loads(line)
        for line in (DATA / "cases.jsonl").read_text().splitlines()
        if '"INV-0007"' in line
    )
    correction = PACK.correction_schema.model_validate(
        json.loads((DATA / "corrections.jsonl").read_text().splitlines()[0])
    )
    draft = PACK.correction_to_rule(correction, case=case)
    baseline = admission.Proposal(
        domain=PACK.name,
        kind=Kind.RULE,
        schema_version=f"{PACK.name}.rule/1",
        body=dict(draft.body),
        scope="00000000-0000-0000-0000-000000000001",
        entities=draft.entities,
        enforcement=draft.enforcement,
        authority=10,
        sensitivity={"class": "internal"},
        valid_from=business_start(NO_LOWER_BOUND),
    )
    install_llm(client, rule_suggestion=True)
    try:
        with allow_network():
            drafted, problems, model = writeback._llm_proposal(
                baseline, pack=PACK, correction=correction, case=case
            )  # pyright: ignore[reportPrivateUsage]
    finally:
        install_llm(None)

    print(f"\nmodel: {model}\nproblems: {problems}\ndraft: {drafted.body if drafted else None}")
    assert model == "anthropic/claude-sonnet-5-5"
    assert drafted is not None, problems
    assert "customer:CUST-0042" in drafted.entities


def _japanese(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text)


def test_an_english_correction_gives_an_english_rule(client: LiteLlmClient) -> None:
    """The English example set (issue 39): the draft keeps the note's language."""
    register_pack(PACK)
    case = {
        "case_id": "EX-INV-001",
        "customer_id": "CUST-0300",
        "customer_name": "Sample Logistics Ltd. (fictitious)",
        "channel": "coupa",
        "issue_date": "2026-12-04",
        "currency": "JPY",
        "lines": [
            {"desc": "Warehouse management, December", "amount_excl_tax": 480000, "tax_rate": 10}
        ],
        "tax_by_rate": {"10": 48000},
        "tax_total": 48000,
        "total": 528000,
        "po_number": None,
        "attachments": [],
    }
    correction = PACK.correction_schema.model_validate(
        {
            "correction_id": "EX-C-en-1",
            "case_id": "EX-INV-001",
            "reviewer": "EN-R-001",
            "corrected_at": "2026-12-04T15:00:00+09:00",
            "changes": [
                {
                    "target": "invoice.remarks.po_number",
                    "action": "set",
                    "before": None,
                    "after": "PO number: PO-55120",
                }
            ],
            "note_ja": "Invoices to Sample Logistics through Coupa are sent back unless the "
            "remarks carry the PO number.",
        }
    )
    draft = PACK.correction_to_rule(correction, case=case)
    assert not _japanese(str(draft.body["text_ja"]))  # the pack's own draft follows the note
    baseline = admission.Proposal(
        domain=PACK.name,
        kind=Kind.RULE,
        schema_version=f"{PACK.name}.rule/1",
        body=dict(draft.body),
        scope="00000000-0000-0000-0000-000000000001",
        entities=draft.entities,
        enforcement=draft.enforcement,
        authority=10,
        sensitivity={"class": "internal"},
        valid_from=business_start(NO_LOWER_BOUND),
    )
    install_llm(client, rule_suggestion=True)
    try:
        with allow_network():
            drafted, problems, _ = writeback._llm_proposal(  # pyright: ignore[reportPrivateUsage]
                baseline, pack=PACK, correction=correction, case=case
            )
    finally:
        install_llm(None)

    print(f"\nenglish draft: {drafted.body if drafted else problems}")
    assert drafted is not None, problems
    assert not _japanese(str(drafted.body["text_ja"]))


def test_embeddings_have_the_models_dimension_and_meaning(client: LiteLlmClient) -> None:
    with allow_network():
        a, b, c = client.embed(
            ["請求書に注文番号を記載する", "インボイスの備考に発注番号を書く", "倉庫の在庫を数える"]
        )

    def cos(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True)) / (
            (sum(p * p for p in x) * sum(q * q for q in y)) ** 0.5
        )

    print(f"\ndims: {len(a)} · related: {cos(a, b):.3f} · unrelated: {cos(a, c):.3f}")
    assert len(a) == 1536
    assert cos(a, b) > cos(a, c)
