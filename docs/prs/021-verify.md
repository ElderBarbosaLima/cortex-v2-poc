# PR 021 — Verify: the release check against a served pack

Branch: `poc/021-verify` → `main` · Issue: [docs/tasks.md §21](../tasks.md) · Depends on: 20 (merged)

## What
- **`POST /v1/cortex/verify`** (`verify_output`, `x-mcp-tool`) replaces the 501. The agent sends the pack id, the case and the output it is about to post, and gets back `pass`, or `hold` with reasons, in the shape of the UAT's Cortex Assurance result. Every check reads the **ledger, never the search projection** (`app/services/access_gate/verify.py`):
  1. **Each constraint in the pack** (mandatory or prohibition) must still be `approved` and valid at the pack's `as_of`. If not, the reason is `rule_no_longer_approved`, with the record id and its current status.
  2. **Applicability (issue 19) is re-run** for the same case, as of the same business date, at the scopes OPA allows *now*:
     - a constraint that applies now but was not in the pack → `new_mandatory_rule`;
     - one that cannot be evaluated → `constraint_unevaluable` (a new reason code).
  3. **The domain pack checks the output** with `DomainPack.check_output`, against the pack's constraint and applied rules → `output_check_failed`, with the pack's message.
- **The case is sent again, checked by hash.**
  - `served_pack` keeps only the sha256 of the case, because the case is the client's data. Re-running applicability needs the case itself, so `VerifyRequest` gains a required `case` field.
  - If its hash differs from the pack's, the answer is 422 `case_mismatch`. This keeps the no-client-data-at-rest choice from issue 20.
- **Every result is stored** in an append-only `verification` table (migration `007_verifications`), linked to `served_pack`. Each row has:
  - the result and reasons;
  - the **sha256 of the output** (not the output itself);
  - the trace id, consumer, end user and policy revision.

  It is under RLS, and the app may SELECT/INSERT only.
- **Other errors.** A missing pack, or another tenant's, is 404 (RLS). The read is decided by OPA on the pack's scope and purpose, and fails closed (503).
- **D3:** Cortex never blocks the IBM system itself. A `hold` is the client's signal to stop.
- **`VerifyResult`** gains `verification_id`, `policy_revision` and `verified_at`.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_verify.py"
PASSED test_a_rule_retired_after_the_pack_holds_even_with_a_stale_projection   # (1) (c) hold [rule_no_longer_approved, <rule>]; outbox not drained
PASSED test_a_new_mandatory_rule_after_the_pack_holds                          # (2) hold [new_mandatory_rule, <newcomer>]
PASSED test_nothing_changed_passes                                             # (3) pass, no reasons, policy_revision
PASSED test_a_retired_default_does_not_hold                                    # only constraints can hold
PASSED test_a_missing_or_other_tenants_pack_is_404                             # (3) unknown id and other-agent's key → 404
PASSED test_the_pack_checks_the_output                                         # label "通常" → output_check_failed "label must be '取扱注意'"; right label → pass
PASSED test_another_case_than_the_packs_is_422                                 # case_mismatch
PASSED test_every_result_is_stored_append_only_and_linked_to_the_pack         # pass then hold rows; output sha256; app role: permission denied; owner: append-only trigger
8 passed
```
**Does criterion 1 really catch mistakes?** I turned off the ledger status check on purpose, and the tests failed:
```
FAILED test_a_rule_retired_after_the_pack_holds_even_with_a_stale_projection
FAILED test_every_result_is_stored_append_only_and_linked_to_the_pack
2 failed, 6 passed
```
I restored the code afterwards.

Live, on the clean-clone dev stack:
```
POST /v1/cortex/verify {pack_id: <unknown>, …} → {"error":"not_found","detail":"no such context pack"} [404]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/021`, `make dev` healthy)
```
$ make test             → 166 passed, 79 deselected
$ make test-integration → 79 passed, 166 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```
The contract changes are: `verify_output` answers 200 or 404 instead of 501 and is still `x-mcp-tool`; `VerifyRequest` gains `case`; `HoldReason` gains `constraint_unevaluable`.

## Notes
- **What testing found about append-only.** A first version of the test ran the forbidden UPDATE as the table owner without naming a tenant, and nothing happened. That is not a hole: FORCE RLS hides every row from the owner too, so the row trigger never fires. With the tenant named, the trigger refuses. The test now checks both layers: the app role's grants, and the owner hitting the trigger.
- **Not in this PR:** blocking in the IBM system (D3); corrections (issue 22).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
