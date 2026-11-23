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
