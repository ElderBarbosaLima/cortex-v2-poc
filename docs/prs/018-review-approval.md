# PR 018 — Review queue: the named owner approves or rejects

Branch: `poc/018-review-approval` → `main` · Issue: [docs/tasks.md §18](../tasks.md) · Depends on: 017 (merged)

## What
- **Routes** (`app/routers/review_cases.py`, the Decision Desk's API, under `/v1/cortex/review-cases`):
  - `GET ""` (`list_review_cases`)
    - lists open (or decided) cases in the scopes OPA lets the purpose read, and only cases whose most sensitive proposal is within the purpose's ceiling;
    - each case shows its title, proposal count, sensitivity and **named owners**;
    - paging uses a cursor.
  - `GET /{case_id}` (`get_review_case`)
    - the proposals, with their bodies opened and their **`outcome`**;
    - the evidence (`source`, `derived_from`);
    - **`affected_cases`**: the work cases the proposals came from (`source.case_ref` / `case_refs`);
    - **`contributors`**: who may *not* decide;
    - every decision so far.
  - `POST /{case_id}/approve` (`approve_review_case`)
    - the approver sets the **final** `authority` and, for rules, `enforcement`;
    - `record_id` picks which pending proposal to approve; the others in the case (the same conflict) become `superseded`.
  - `POST /{case_id}/reject` (`reject_review_case`)
    - needs a `reason`;
    - every pending proposal becomes `deprecated`.
  - Errors: 404 for no case (or another tenant's), 409 `case_decided`, 422 `invalid_decision`.
- **Who may decide is OPA's decision** (`approval.rego`). It returns a 403 with OPA's reason when:
  - the actor is not a person: *"only a person can approve or reject"*;
  - the actor is a contributor: *"separation of duties…"*. Contributors are the people who asserted a proposal, plus anyone in `source.contributors` (for example whoever corrected the work case);
  - the actor lacks **`memory.approve`** at the scope;
  - **new**: the actor is not the **named owner or backup** of the nearest owned node at or above the scope. The reasons are *"only the named owner or backup of this scope can decide"* and *"no named owner for this scope"*.
- **Owner and backup per scope in the seed.**
  - `OWNERS` in `app/synthetic/common.py` sets OWN-001 as owner and OWN-002 as backup for each Q2C team, and X-001 for the other tenant.
  - They reach `tenant.json` through `make gen-data` (deterministic, 17 lines added).
  - From there they go to OPA's data (`owners`) and to `named_owners()`, which the API uses for display. That function does the same lookup as the policy.
- **One decision does the following, in one transaction** (`app/services/review.py`):
  1. writes a **`kind=decision` ledger record** carrying who, what and why. Its `record_id` is the `decision_id`. Its `derived_from` holds the case's proposals, so it shares their lineage key: erasing the proposal erases the decision too (Q10);
  2. **on approval**, appends the next version of the chosen proposal as `approved`, with `approved_by {approver_type: "user", user_id, decision_id}` and the final authority and enforcement;
  3. **on rejection**, appends a `deprecated` version with `approved_by` null for each pending proposal;
  4. inserts an **append-only `review_decision`** row (trigger plus ORM guard, under RLS, with the app allowed SELECT/INSERT only) that records the reason, the final authority and enforcement, and the `policy_revision`;
  5. moves the case to `decided`. The case row is locked (`FOR UPDATE`) first, so two people cannot decide the same case at once.
- **`outcome`** (`pending|approved|rejected|retired|superseded`) is derived from history:
  - `deprecated` without any approved version → `rejected` (Q5);
  - `deprecated` after an approval → `retired`;
  - `erased` → `retired`, until issue 24 handles erasure.
- **Fails closed on writes too.** OPA down → 503 `access_gate_unavailable`, and the transaction is rolled back.
- **Fix to issue 17: `review_case.title` is dropped** (migration `005_review_decisions`).
  - It held the start of a proposal's `text_ja` in clear, outside the sealed body.
  - Crypto-shredding a lineage would have left that text behind.
  - The title is now read from the first proposal's sealed body. A test checks that the column does not exist.
- **`ledger.append_new(record_id=…)`** is a new optional argument, so a decision record can be named by its decision id.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_review_queue.py"
PASSED test_the_corrector_cannot_approve_and_nothing_is_written            # (1) 403 "separation of duties: …", policy_revision; still v1 proposed, case open, 0 decisions
PASSED test_a_contributor_named_in_the_evidence_cannot_decide_either       # (1) the backup corrected the source case → 403
PASSED test_an_agent_alone_cannot_decide                                   # 403 "only a person can approve or reject"
PASSED test_the_named_owner_approves_with_final_authority_and_a_decision   # (2) v2 approved, approved_by {approver_type:user, user_id, decision_id}; authority 25 mandatory;
                                                                           #     duplicate → superseded; decision record kind=decision derived_from both; second approve → 409
PASSED test_the_backup_rejects_and_the_outcome_is_rejected                 # (2) proposed → deprecated, approved_by never set; GET shows outcome rejected, reason, decided_by, affected_cases
PASSED test_a_rejection_needs_a_reason                                     # 422, case still open
PASSED test_approval_with_opa_down_is_503_and_nothing_is_written           # (3) 503 access_gate_unavailable; v1 proposed, case open, 0 decisions
PASSED test_the_queue_lists_open_cases_with_title_and_named_owners
PASSED test_the_title_is_not_stored_in_clear
9 passed
```
The OPA policy tests (`make test-policies`, **38/38**, Regal clean) add these cases:
- the backup decides;
- a final validator who is not the named owner is denied, even though RBAC allows them;
- the nearest owned node up the tree applies;
- no owner anywhere up the tree → denied;
- proposing needs no owner.

The unit tests in `tests/services/test_review_outcome.py` cover the outcome table and the seed's owner and backup per team.

Live, on the clean-clone dev stack:
```
GET  /v1/cortex/review-cases?purpose=invoice_drafting&scope=<q2c-invoicing>
     {"items":[],"next_cursor":null,"policy_revision":"sha256:80a4034d8697a6a7"} [200]
POST /v1/cortex/review-cases/<unknown>/approve → {"error":"not_found","detail":"no such review case"} [404]
(docker compose stop opa)
GET  /v1/cortex/review-cases?… → {"error":"access_gate_unavailable","detail":"policy engine unavailable (ConnectError)"} [503]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/018`, `make dev` healthy)
```
$ make test             → 160 passed, 54 deselected
$ make test-integration → 54 passed, 160 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```
The contract gains four operations: `list_review_cases`, `get_review_case`, `approve_review_case` and `reject_review_case`.

## Notes
- **Status of a non-chosen proposal.** When a case holds several proposals and one is approved, the others become `superseded` (outcome `superseded`), not `rejected`. They were the same conflict, resolved by the approved one. The approved record carries no `supersedes` pointer to them; the `review_decision` row lists them all.
- **Not in this PR:** the UI (issue 28). The D17 thresholds (`min_distinct_cases`, `min_distinct_correctors`, `window_days`) belong to correction → proposal (issue 22). Separation of duties is always on here.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
