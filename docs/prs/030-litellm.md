# PR 030 — Every AI use goes through LiteLLM: rule drafts and embeddings

Branch: `poc/030-litellm` → `main` · Issue: [docs/tasks.md §30](../tasks.md) · Depends on: 22, 20 (merged)

Your answers (2026-11-22): LiteLLM is for **every** use of AI in the PoC; Sonnet 5.5 is on it; synthetic data may be sent (`devs-ai.straker.ai` is UAT).

## What
- **`app/clients/llm.py`: the only way to a model.**
  - `LiteLlmClient` speaks LiteLLM's OpenAI-compatible API with httpx (TLS verified, `temperature: 0`).
  - It retries only what can change (5xx, 429, a dropped connection) and **fails closed**: anything unusable raises `LlmUnavailable`, and the caller takes the deterministic path.
  - **The key never shows** in an error message, a log line or `repr`. Errors name only the status or the exception type, never the request.
  - The models are config: `anthropic/claude-sonnet-5-5` (chat) and `azure/text-embedding-ada-002` (1536 dimensions). Both are listed by `GET /v1/models`, which exposes 49 models.
- **The key, end to end:**
  - `LITELLM_API_KEY` in the git-ignored `.env`;
  - Terraform (`TF_VAR_litellm_api_key`, sensitive, default empty) stores it in Secrets Manager as `cortex-poc/litellm-api-key`;
  - the service, the projector and `make reindex` read it from there at startup (`llm.configure`). The secret is read only if a use is switched on, and an empty or missing secret means AI is off, never a startup failure;
  - the IAM policy's `ReadServiceSecrets` now covers both secrets (`infra/iam/org-brain-service.json` regenerated).
- **Use (a): rule suggestion** (`LLM_RULE_SUGGESTION`, `app/services/writeback.py`).
  - The D17 thresholds still count on the pack's deterministic draft, so the model is called only when a rule is due.
  - The model gets the correction, the case, the pack's baseline draft and the pack's vocabulary: CEL variables and fields, targets, entity fields. It returns a JSON draft.
  - **The draft goes through the admission gate like any other.** A valid one is proposed as `asserted_by {type: model, id: <model>}`, with authority ≤ 10, `source.suggested_by: model`, and still a person's to approve.
  - An invalid one (bad CEL, unknown target, malformed, model down) is **never proposed**: the pack's own draft is, and the reasons come back as `proposal_problems` (`llm_invalid_condition`, `llm_unavailable`, …).
  - `CorrectionAccepted.suggested_by` says which draft was used.
- **Use (b): embeddings** (`LLM_EMBEDDINGS`).
  - The projector embeds each record's search text, and the context pack embeds its query.
  - `search_doc.embedding` becomes a plain `vector` (migration `011`), so real embeddings (1536) and the fixed test vectors (8) coexist. Search compares vectors of the same dimension only.
  - A meaning-only match needs closeness ≥ **0.88**. This was measured live: related Japanese sentences scored 0.905 and unrelated ones 0.841. **Every candidate is still revalidated against the ledger**, and rules never come from search.
  - A record that cannot be embedded is still found by its words. Embedding lives in `app/projections/embeddings.py`, so the D12 guard still holds: no core module names the Postgres adapter.
- **No model on the applicability, resolution or verify path.**
- **Tests:**
  - `make test` stays hermetic: 9 client tests on `httpx.MockTransport`, including a canary key that must not appear in errors, `repr` or output;
  - integration tests use a fake model;
  - **`make test-llm`** (marker `llm`, the only test container with network) calls the real LiteLLM.

## Acceptance criteria — output
1. **Without a key, everything runs as before.** On the clean clone (`.env` from `.env.example`, so no key): `make dev` → AI off; `make e2e` → **23/23 on both workflows**; `make test-all` → 107 integration passed, Rego 39/39. **With the key**, `make test-llm` calls the real LiteLLM:
```
model: anthropic/claude-sonnet-5-5
problems: ()
draft: {'text_ja': 'CUST-0042 向けの Coupa 経由の請求書では、備考に注文番号（例: 注文番号: PO-88231）を記載し、検収書を Coupa に添付すること。',
        'targets': ['invoice.remarks.po_number', 'invoice.attachments.kenshusho'],
        'condition': 'invoice.customer_id == "CUST-0042" && invoice.channel == "coupa"', 'requirements': {…}}
dims: 1536 · related: 0.905 · unrelated: 0.841
2 passed
```
2. **A model draft with invalid CEL never becomes a proposal; a valid one becomes `proposed`, authority ≤ 10, `asserted_by.type=model`, never approved on its own.**
```
$ make test-integration ARGS="-rA tests/integration/test_llm_uses.py"
PASSED test_a_valid_model_draft_becomes_the_proposal                         # model, authority 10, proposed; pack fields kept; brief named the fields
PASSED test_a_bad_or_missing_model_draft_falls_back_to_the_packs_draft[bad-cel]         # llm_invalid_condition → the pack's draft (policy)
PASSED …[unknown-target] · …[malformed] · …[down]                           # llm_unknown_target / llm_malformed / llm_unavailable
PASSED test_embeddings_bring_knowledge_that_shares_no_word_with_the_case    # off: not found · on: found, revalidated; vector_dims 16
6 passed
```
3. **The key appears in no log, image, bundle or response.**
   - `test_the_key_never_shows_in_errors_repr_or_logs` uses a canary key.
   - After the live runs, the real key appeared in **0** repository files besides `.env`, in **0** lines of git history, and in none of the run logs.

**Live, on the dev stack with both uses on** (`LLM_RULE_SUGGESTION=true`, `LLM_EMBEDDINGS=true` in `.env`):
```
org-brain-service   llm.configured {'on': True, 'rule_suggestion': True,  'embeddings': True, 'chat_model': 'anthropic/claude-sonnet-5-5'}
org-brain-projector llm.configured {'on': True, 'rule_suggestion': False, 'embeddings': True, …}
$ make e2e → 23/23 checks passed (custom_invoices) · 23/23 checks passed (cash_application)
learned rules: asserted_by = model / anthropic/claude-sonnet-5-5 (both workflows)
search_doc: 17 of 17 embedded, 1536 dimensions
```
**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/030`, no key): `make dev`, `make e2e` (23/23 ×2), `make test-all`, `make lint`, `make typecheck`, `make contract-check` and `make migrate-check` all pass. The contract change is the new `CorrectionAccepted.suggested_by`.

## Notes
- **Switches belong in `.env`, not on the command line.**
  - My first live run passed them on the command line, so the `make reindex` inside `make e2e` ran without them and rebuilt the projection unembedded: only 4 of 17 docs had vectors.
  - With the switches in `.env` (as now in yours, both on), all 17 are embedded.
  - After switching embeddings on, run `make reindex` once.
- **The Terraform state holds the key.** It lives on the `terraform-state` volume, which `make stop` deletes with the emulator.
- **You pasted the key in this conversation, so it is in its history.** Rotate it if it reaches beyond UAT.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
