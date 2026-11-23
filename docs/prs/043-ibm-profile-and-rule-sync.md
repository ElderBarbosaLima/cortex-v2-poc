# PR 043 — IBM's three operations: context, rule sync and outcome (issues 43 and 44)

Branch: `poc/043-ibm-profile-sync` → `main` · Issues: [docs/tasks.md §43, §44](../tasks.md) · Depends on: 36, 40, 42 (merged)

Slide 20 has IBM's AI talk to Cortex in three ways:
1. get approved rules for the rule engine, synced daily or on change;
2. get context for a payment;
3. report the outcome.

There is no release check: IBM's audit trail records the rule ID, version and approver instead (slide 22).

## What
- **IBM's profile** (`ibm-pr-agent`) grows from "pack only" (issue 36) to three scopes: `cortex:pack`, `cortex:rules-sync` and `cortex:outcomes`. Everything else still answers 403: corrections, verify, proposing records, the review queue, export, retire.
- **`GET /v1/cortex/rules/sync`** (`sync_rules`):
  - It returns the domain's rules that the scope can see, as changes since an **opaque cursor**. `upsert` carries an approved version, with its body (the engine fields and the guidance), approver, validity, enforcement and entities. `remove` means a rule is no longer in force: retired, superseded, stale or erased.
  - Without a cursor you get everything. `next_cursor` gives only what changed since; an empty page keeps the same cursor.
  - Proposals are never synced.
- **`POST /v1/cortex/outcomes`** (`report_outcome`, a fourth agent tool) runs the same writeback as a correction, under a scope that allows nothing else. A confirmed outcome becomes a past case (issue 42); a corrected one also feeds the learning loop. The specialist who decided is `X-End-User-Id`.
- **Every rule in a pack cites its approver** (`PackRecord.approved_by`). With `record_id` and `version`, that's what IBM's audit trail needs, and `pack_id` is the reference ID. The approver is stored with the served pack (migration `015`), so a replay cites the same person.

## Fixed while writing it
**The sync cursor was the ledger's internal row number.** The house-rule test (no numeric ID crosses the API) caught it. It is now opaque: the last version's time, record ID and version. A made-up cursor answers `422 invalid_cursor`.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_ibm_profile.py"
PASSED test_the_rule_engine_syncs_everything_then_only_what_changed
       # cursor 0: every rule as upsert (山田商事: engine {expected_fee 660, max_fee 880, fee_expense}, approved_by OWN-001)
       # a new approved rule → only it; retired → only it, as remove (no body); nothing new → [] and the same cursor
PASSED test_context_cites_each_rule_with_version_and_approver_and_the_outcome_is_remembered
PASSED test_everything_else_is_still_out_of_the_profile × 4    # corrections, verify, records, review queue → 403
PASSED test_a_cursor_is_opaque_and_a_made_up_one_is_refused
```
- `make test-integration` → 156 passed
- `make test` → 319 + 29 frontend
- `make e2e` (fresh stack) → 23/23 and 25/25. Phase 1's "everything else answers 403" still holds.
- `make lint`, `make typecheck`, `make contract-check` and `make migrate-check` are clean.

The contract changes are additions only: `sync_rules`, `report_outcome`, `PackRecord.approved_by`, `SyncedRule` and `RuleSyncPage`.

## Notes
- **D3 (verify in Phase 1) is now "no" for IBM**, as the brief describes. The route stays for the PoC's own agents.
- **One known limit of the cursor:** it orders by the time a version was recorded. If two transactions commit out of order, a sync can miss the later-committed one until the next full sync. That's acceptable for a daily PoC sync. Production needs a commit-ordered feed (the outbox).
- **An outcome cleared by IBM's AI with no specialist** has no `X-End-User-Id` today. Brief question 3 asks IBM whether they send those at all.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
