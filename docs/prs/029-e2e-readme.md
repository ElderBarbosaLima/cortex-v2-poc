# PR 029 — End to end on both workflows, and the final README

Branch: `poc/029-e2e-readme` → `main` · Issue: [docs/tasks.md §29](../tasks.md) · Depends on: 24, 26, 28 (merged)

## What
- **`tests/e2e/run.py`, one harness for both workflows** (`custom_invoices`, `cash_application`).
  - It uses only the public API, as the agent (`CORTEX_E2E_AGENT_KEY`) and as the Decision Desk (`CORTEX_DESK_API_KEY`).
  - It runs in the org-brain-service image, inside the stack's network.
  - Everything per workflow comes from `data/synthetic/<workflow>/` (`expected.json`, `timeline.json`): cases, rules, corrections, the approver and the retirement.
- **`make e2e`** runs on a fresh running stack, in phases, with the admin steps in between. State lives in `.e2e/` (git-ignored).
  1. `make seed`.
  2. **before:**
     - the happy case: the pack holds exactly the expected rules, and verify passes;
     - scenario 2: the local default is `overridden_by` the global prohibition;
     - scenario 3: the contract clause applies without shared words;
     - scenario 4: the customer rule is in the pack;
     - a correction leads to a proposal and a review case, then to approval by the named owner (enforcement and authority from the timeline);
     - the next applicable case carries the learned rule and verify passes; the unrelated case does not carry it;
     - **attribution:** the approved proposal names its approver and decision, its corrector, the correction decision it came from, and the pack's policy revision;
     - the evidence export is archived under Object Lock (the harness polls until the worker reports `exported`).
  3. **`make reindex`**.
  4. **after-reindex:**
     - **criterion (a):** the same four cases give exactly the same packs;
     - **scenario 4:** the owner retires the rule, verify holds the earlier pack (`rule_no_longer_approved`), and a later case's pack no longer carries it.
  5. **`make erase-record`** of each learned rule.
  6. **after-erasure:**
     - the earlier pack stays whole: the rule's body is gone (`body_erased`, `body_hash` kept) and the other bodies are readable;
     - the record is `erased`, a new pack no longer carries it, and verify no longer passes the earlier pack.
  7. **report:** one report per workflow (`.e2e/report-<workflow>.md`), every check named. The run exits non-zero on any failure.

  A second run on the same stack refuses with *"run on a fresh stack (make stop && make dev)"*, because scenario 4 retires its rule.
- **`POST /v1/cortex/records/{id}/retire`** (`retire_record`). Scenario 4 needed a public way for the owner to withdraw an approved rule.
  - OPA gains the action `retire`: a person, `memory.approve`, the named owner or backup (Rego 39/39).
  - It appends a `deprecated` version (outcome `retired`) and a `kind=decision` record with the reason. A record that is not approved answers 409 `not_retirable`.
  - Integration test: the reviewer and the agent are refused, the backup retires the record, and retiring again is 409.
- **Erasure stays an admin CLI** (issue 24's design). `make e2e` calls `make erase-record` between phases, so the harness itself never needs more than the public API.
- **README, rewritten end to end:**
  - quick start (prerequisites, `make dev` / `seed` / `e2e` / `stop`);
  - **the demo**, step by step;
  - how it works;
  - the API operation table;
  - every check target, and the services;
  - **the IBM Cloud ↔ AWS map**;
  - **the emulator limits**;
  - **the deviations and decisions on record**;
  - synthetic data, layout, secrets.

## Found and fixed: a case whose proposal was erased broke the review queue
Running the Decision Desk e2e (`make test-e2e-ui`) **after** `make e2e` on the same stack failed. The review-case list and detail opened every proposal's sealed body to show a title, and the learned rule had just been erased.
- The title is now `(erased)`, and the body is `null` with `body_erased: true`.
- The Decision Desk says "This proposal was erased…".
- Covered by `test_an_erased_proposal_still_lists_with_no_title_or_body`.
- Rerunning the same sequence: both Playwright tests pass, and the decided list shows `['(erased)', …]`.

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/029b`, `cp .env.example .env`, then the issue's validation `make dev && make e2e && make test-all && make lint && make typecheck`, plus `make test-e2e-ui` on the same stack:
```
$ make e2e
== custom_invoices: before … == cash_application: before
== make reindex (criterion a)
== custom_invoices: after-reindex … == cash_application: after-reindex
== make erase-record 01a20622-…   == make erase-record 01a20622-…
== custom_invoices: after-erasure … == cash_application: after-erasure
23/23 checks passed      (custom_invoices)
23/23 checks passed      (cash_application)
$ make test-e2e-ui → 2 passed
$ make test-all    → 101 passed (integration) · Rego 39/39 · All test tiers passed
$ make lint        → All checks passed! · check-v2-styles: clean
$ make typecheck   → 0 errors
$ make contract-check → matches
```
1. **A clean clone passes `make dev && make e2e` on both workflows.**
2. **Scenarios 2, 3 and 4 and criterion (a) are named tests in the report.** The `custom_invoices` report:
```
- ✓ happy case: the pack holds exactly the expected rules
- ✓ happy case: verify passes
- ✓ scenario 2: the local default loses to the global prohibition
- ✓ scenario 3: the contract clause CI-C-0063-PAYTERMS applies without shared words
- ✓ scenario 4 (before): CI-C-0017-DEPT is in the pack
- ✓ correction: a rule is proposed and a review case opened
- ✓ approval: OWN-001 approves it (mandatory, authority 30)
- ✓ next applicable case (INV-0021): the learned rule is in its pack
- ✓ next applicable case (INV-0021): verify passes
- ✓ unrelated case (INV-0022): the learned rule is not in its pack
- ✓ attributable: the applied rule names its correction, corrector, approver and policy revision
- ✓ export: the pack's evidence is archived under Object Lock
- ✓ criterion (a): after make reindex, INV-0001 / INV-0008 / INV-0010 / INV-0021 gives the same pack   (4 checks)
- ✓ scenario 4: after retiring CI-C-0017-DEPT, verify holds the earlier pack
- ✓ scenario 4: CI-C-0017-DEPT is not in a later pack (INV-0035)
- ✓ erasure: the earlier pack stays whole, the erased rule's body is gone
- ✓ erasure: the pack's other bodies are still readable
- ✓ erasure: the record is erased in the ledger
- ✓ erasure: a new pack no longer carries it
- ✓ erasure: verify no longer passes the earlier pack
```
   The `cash_application` report has the same checks with `CA-C-0063-ALLOC`, `CA-C-0017-HOLD`, PAY-0125 (next), PAY-0129 (unrelated), PAY-0135 (later), and the fee rule approved as `default`, authority 20.
3. **Every applied rule is attributable:** its correction, its corrector, its approver and the `policy_revision`.

## Notes
- **What the erasure verify check asserts.** For cash_application the learned rule is a `default`. After its erasure, verify holds the earlier pack through the route check (the difference is no longer explained by any rule), not through `rule_no_longer_approved`, which is for constraints. The check therefore asserts "no longer passes".
- **`tests/e2e/run.py` is not covered by Ruff.** Lint covers `services/`; the harness is standard library plus httpx, and it runs only inside the stack.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
