# PR 033 — Supersede and expiry through the review queue

Branch: `poc/033-supersede-expiry` → `main` · Issue: [docs/tasks.md §33](../tasks.md) · From Nake's review

## What
- **Supersede.**
  - A proposal may declare `supersedes: <record_id>`. It must name an approved or stale record of the same domain and kind; otherwise it is refused with 422 `unknown_superseded`, `not_supersedable` or `supersede_mismatch`.
  - The pointer is written on the proposal itself (`memory_record.supersedes`, a field of the envelope since issue 8).
  - **When the named owner approves it,** the old record gets a `superseded` version in the same decision. Verify then holds packs that relied on it (`rule_no_longer_approved`), and the next pack carries the new record.
  - The case detail shows `supersedes`, and the evidence manifest now carries `supersedes` and `derived_from` for each record.
- **Validity set by the owner.** `ApproveRequest` gains `valid_from` and `valid_to`, as business dates at midnight JST, with `valid_to` exclusive. `ledger.transition` accepts them on approval only, and `valid_to ≤ valid_from` gets a 422.
- **Basic expiry.** `GET /v1/cortex/records/expiring?scope&purpose&within_days=30` (`list_expiring_records`) lists approved records whose validity ends within N days or has ended, soonest first, under OPA read rules.
- **A decision I took on expiry: no status change.** Expiry is `valid_to` itself, and the access gate and verify already apply it by the event's business date. A job that turned expired records into `stale` would drop a rule from a pack for an event dated before the expiry, which is wrong in business time. So the "job" is the expiring list, for owners to act in time; `stale` stays for recertification (stage 4).

## Acceptance criteria
```
$ make test-integration ARGS="-rA tests/integration/test_supersede_expiry.py"
PASSED test_a_supersede_through_the_review_queue_holds_old_packs_and_serves_the_new_rule
       # decision: {new approved, old superseded}; old pack → hold rule_no_longer_approved; next pack: new, not old; case shows supersedes
PASSED test_only_an_approved_record_of_the_same_kind_can_be_superseded       # unknown_superseded / not_supersedable
PASSED test_the_owner_sets_validity_and_packs_follow_the_business_date
       # valid_to ≤ valid_from → 422; pack of 5 Nov carries it, pack of 20 Nov does not;
       # the 5 Nov pack still verifies (business time); listed as expired, valid_to 2026-11-09T15:00Z (= 10 Nov 00:00 JST)
PASSED test_a_rule_ending_soon_is_listed_before_it_expires                   # within 10 days: listed, not expired; within 2: not listed
4 passed
```
Criterion 2 of the issue says an expired rule makes verify hold the earlier pack. It is met for packs dated after the expiry. A pack for an event the rule still covered keeps passing, by design (see the decision above).

`make lint`, `make typecheck` and `make test` pass, and `make test-integration` gives 117 passed. The contract gains `ProposeRecordRequest.supersedes`, `ApproveRequest.valid_from/valid_to`, `CaseProposal.supersedes` and `list_expiring_records`. The clean-clone run covers the whole batch: see PR 032.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
