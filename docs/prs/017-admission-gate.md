# PR 017 — Admission gate: every write validated, authorised, proposed, queued

Branch: `poc/017-admission-gate` → `main` · Issue: [docs/tasks.md §17](../tasks.md) · Depends on: 015, 016 (merged)

## What
- **`POST /v1/cortex/records` (`propose_record`, 201).** This is the only way into the ledger from outside. It runs in this order:
  1. **Validation** (`app/services/admission.py::validate_proposal`). It is pure (no DB, no OPA) and returns **every problem at once**, each as `{code, detail}`, in a `422 invalid_proposal`. **Nothing is written.** The codes:
     - `unknown_domain`;
     - `unknown_schema_version` (expects `<pack>.<kind>/1`);
     - `non_canonical_entity`: only `type:key` keys of the pack's entity types are accepted, never a name;
     - `invalid_sensitivity`.
     - For rules, also:
       - `missing_enforcement`;
       - `invalid_body` (the pack's rule schema);
       - `unknown_target`;
       - `invalid_condition` (CEL compiled at admission, D22);
       - `unknown_field` (fields the pack does not expose);
       - `entity_mismatch` (Q6: an entity literal in the condition must appear in `entities[]`).
  2. **OPA read decision** for the purpose and scope: 403 `access_denied` with `policy_revision`.
  3. **Sensitivity ceiling.** A class above the purpose's ceiling gets 403 with the reason, e.g. `sensitivity restricted is above the confidential ceiling for purpose 'invoice_drafting'`.
  4. **OPA approval, action `propose`.** The actor is the end user if there is one, else the consumer.
  5. **Append as `proposed`** (`ledger.append_new`; the body is sealed by the issue-12 codec):
     - `asserted_by` is the user, or `{"type": "model", …}` for an agent alone;
     - a model is capped at the model ceiling, and anything above it gets 422 `model_ceiling`;
     - nothing is ever admitted as approved (rule 6).
  6. **Review queue.** Rules and facts open or join **one open case per conflict**. The conflict key is the sha256 of the sorted targets plus the sorted entities, so field order does not matter. Many proposals about the same thing share one case (scenario 6). The answer says `review_case_id` and `grouped`.
- **Migration `004_review_cases`:**
  - `review_case`, with status `open|decided`; the partial unique index `uq_review_case_open (tenant_id, conflict_key) WHERE status='open'` enforces one open case per conflict in the database too;
  - `review_case_item`, with unique `(case_id, record_id)`;
  - both are under RLS, ENABLEd and FORCEd;
  - the app gets SELECT and INSERT, plus UPDATE of `review_case.status` only, which issue 18 needs.
- **Business dates.** `valid_from` and `valid_to` are calendar dates, read as midnight **JST**. `valid_from` is required.
- **Core stays workflow-agnostic (D18).** Everything comes from the record's `DomainPack`: schema, targets, fields, entity types. The core tests use only the fake `widgets` pack, and the guard test still passes.

## Acceptance criteria — output
Integration tests run against real OPA and Postgres with the fake pack:
```
$ make test-integration ARGS="-rA tests/integration/test_admission_gate.py"
PASSED test_an_invalid_condition_is_422_and_nothing_is_written      # (1) counts of memory_record/review_case/item unchanged
PASSED test_a_personal_name_as_entity_is_422                         # (2) "田中太郎" → non_canonical_entity
PASSED test_sensitivity_above_the_purpose_ceiling_is_403             # (3) restricted > confidential, reason in detail, nothing written
PASSED test_two_proposals_for_the_same_conflict_share_one_case       # (4) +1 case, +2 items, grouped false → true
PASSED test_an_agent_alone_proposes_as_a_model_under_the_ceiling     # authority 20 without a person → 422 model_ceiling; with owner → 201 proposed
PASSED test_a_proposal_lands_sealed_and_proposed_in_the_ledger       # status proposed, approved_by NULL, asserted_by = user
6 passed
```
The unit tests in `tests/services/test_admission_validation.py` (16 passed) cover:
- one case per problem code;
- all problems reported together;
- facts not checked against the rule schema;
- the conflict key ignoring order and separating different conflicts.

Live, on the clean-clone dev stack (no real pack is registered yet; they arrive in issues 25–26):
```
POST /v1/cortex/records (no valid_from) → {"error":"invalid_request","detail":[{"loc":["body","valid_from"],"msg":"Field required","type":"missing"}]} [422]
POST /v1/cortex/records (no key)        → {"error":"unauthorized","detail":"missing bearer API key"} [401]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/017`, `make dev` healthy)
```
$ make test             → 151 passed, 45 deselected
$ make test-integration → 45 passed, 151 deselected
$ make test-all         → All test tiers passed (Rego 33/33)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec (gains propose_record)
$ make lint             → All checks passed!
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```

## Not in this PR
- Approve and reject, separation of duties on decisions, and moving a case to `decided` are issue 18.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
