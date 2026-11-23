# PR 022 — Writeback: correction → decision → proposed rule

Branch: `poc/022-writeback` → `main` · Issue: [docs/tasks.md §22](../tasks.md) · Depends on: 21 (merged)

## What
- **`POST /v1/cortex/corrections`** (`submit_correction`, `x-mcp-tool`, 202) replaces the last 501. The flow lives in `app/services/writeback.py`:
  1. **Checks.** It needs:
     - **the reviewer** as `X-End-User-Id` (else 422 `reviewer_required`). The corrector's identity comes from authentication, never from the correction body;
     - **the pack** (404 if missing or another tenant's);
     - **the same domain and the same case** (checked by hash: 422 `domain_mismatch` / `case_mismatch`);
     - **OPA read and propose permission** on the pack's scope (403, or 503 if OPA is down).
  2. **Validation and draft.** The correction is validated by the pack's `correction_schema` (422 `invalid_correction`), then turned into a draft by `DomainPack.correction_to_rule`.
  3. **The correction is always recorded** as a `kind=decision` record (schema `cortex.correction/1`), asserted by the reviewer and `derived_from` the pack's records.
  4. **D17 thresholds** (`RuleSuggestionPolicy`):
     - counted on the draft's conflict key, over corrections within `window_days`: distinct work cases (`case_ref`) and distinct correctors;
     - demo default **1/1/30**; the golden set also runs **3/2/30**;
     - configurable as `RULE_SUGGESTION` in compose, and injectable per app in tests.
  5. **Proposal through the admission gate (17)**, once the thresholds are met:
     - `asserted_by {type: policy, id: d17-rule-suggestion}`, authority ≤ 10;
     - `source` holds `case_refs` and the **correctors as `contributors`**, so OPA's separation of duties keeps any of them from approving it;
     - `derived_from` the correction decision.

     If the pack's draft fails admission, the problems come back in `proposal_problems` and nothing is proposed.
  6. **`correction` row** (migration `008_corrections`), append-only under RLS:
     - unique `(tenant_id, idempotency_key)`;
     - `request_hash`;
     - links to the pack, decision, proposal and review case.
- **Idempotency.**
  - The same key with the same request returns the first result with `replayed: true` and writes nothing.
  - The same key with a different request → 409 `idempotency_conflict`.
  - Concurrent retries are serialised by a transaction-scoped **advisory lock on the key**, so the second waits and finds the first's row.
- **The ledger now caps `policy` actors like `model` actors** (`MACHINE_ACTORS`): authority above 10 needs a person's approval.
- **A learned rule holds from the corrected case's business date** (the pack's `as_of`), not from the clock (Q8). Testing found this: with "today" as `valid_from`, a rule learned on Nov 22 did not apply to the next case dated Nov 2.
- **No operation answers 501 any more.** `_not_yet` and its contract test are gone.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_writeback.py"
PASSED test_one_correction_opens_one_review_case_at_1_1_30                         # (2) met; proposal: proposed, authority 10, asserted_by policy,
                                                                                   #     contributors [R-001], derived_from [decision]; decision asserted by R-001
PASSED test_the_same_idempotency_key_writes_nothing_twice                          # (1) replayed, same ids; correction/memory_record/review_case_item counts unchanged;
                                                                                   #     same key + other body → 409 idempotency_conflict
PASSED test_at_3_2_30_the_case_opens_only_on_the_third_case_with_two_correctors    # (2) (1,1,no) → (2,1,no) → (3,2,yes); case_refs O-1..O-3, both correctors
PASSED test_the_approved_rule_reaches_the_next_case_and_not_an_unrelated_one       # (3) corrector OWN-002 → 403 separation of duties; OWN-001 approves;
                                                                                   #     next case's pack: role applied, label 取扱注意; unrelated pack: absent;
                                                                                   #     verify of a wrong label → hold
PASSED test_a_correction_needs_the_reviewer_and_the_packs_case                     # reviewer_required, case_mismatch, invalid_correction
PASSED test_concurrent_retries_of_one_key_write_one_correction                     # 4 parallel calls, one key → one correction, 1 fresh + 3 replays
6 passed
```
The unit tests in `tests/services/test_writeback_policy.py` (7 passed) cover parsing and refusing bad thresholds, and the `model`/`policy` ceiling until a person approves.

**Does the lock really matter?** I replaced the advisory lock with a no-op on purpose. The concurrency test failed 3 times out of 3 (`1 failed`); with the lock it passes. I restored the code afterwards.

Live, on the clean-clone dev stack:
```
docker compose exec org-brain-service printenv RULE_SUGGESTION → 1/1/30
POST /v1/cortex/corrections (no X-End-User-Id) → {"error":"reviewer_required",…} [422]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/022`, `make dev` healthy)
```
$ make test             → 172 passed, 85 deselected
$ make test-integration → 85 passed, 172 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```
The contract changes:
- `CorrectionRequest` gains `case`;
- `CorrectionAccepted` gains `correction_id`, `replayed`, `rule_suggestion` and `proposal_problems`;
- `submit_correction` answers 202, 404, 409 and 422.

## Notes
- **Below the thresholds,** a correction is still a decision record. It counts towards later thresholds but proposes nothing yet.
- **Above the thresholds, each further correction** on the same conflict adds another proposal to the same open review case. They are grouped, so the owner decides once, and the duplicates become `superseded` (issue 18).
- **Not in this PR:** LLM-suggested rules.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
