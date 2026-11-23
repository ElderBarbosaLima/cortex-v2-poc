# PR 035 — Phase 1 is a Payment Reconciliation subset, on the cash_application pack

Branch: `poc/035-payment-reconciliation` → `main` · Issue: [docs/tasks.md §35](../tasks.md) · Depends on: 26, 36 (merged)

Lee (2026-11-22): Phase 1 of the IBM PoC is **not AskQ2C**. It is a small subset of Payment Reconciliation, which Lee and Erika will define in the December workshop. Following your instruction to take the most likely path, this PR fixes the following until the workshop:
- **The subset = the `cash_application` pack**: matching a payment to an open invoice, payer-name resolution, transfer fee, and 差押 (garnishment).
- **IBM calls as `ibm-pr-agent`**, purpose `payment_reconciliation`, through the pack-only profile (issue 36).
- **Reviewers stay OWN-001/OWN-002** from the seed. `custom_invoices` stays as the second pack (D18).

## What
- **The golden set runs as IBM will call.** `test_golden_cash_application` asks for all 40 packs as `ibm-pr-agent` with `payment_reconciliation`.
  - The reviewer side stays with the PoC agent and the Decision Desk: corrections, approvals, retirements and verify.
  - The harness gained `pack_key` and `pack_purpose`.
  - It also checks that IBM's consumer gets 403 on verify.
- **`make e2e` (cash_application) has two new Phase 1 checks:**
  - the IBM consumer gets exactly the expected pack;
  - verify, corrections and records each answer it with 403.
  - New key `CORTEX_E2E_IBM_KEY` (added by `make init-env`). Your `.env` now also lists `ibm-pr-agent` in `CORTEX_DEV_API_KEYS`.
- **Until D3 is decided, the release check on Phase 1 packs runs as `askq2c-agent`.** This PR registers the `payment_reconciliation` purpose for it. If IBM must call verify itself, give `ibm-pr-agent` `cortex:read` for `/verify`, and nothing else changes.
- **Docs:** the README now names the pilot slice as Payment Reconciliation, and a new deviation row records the assumption. The architecture docs keep IBM's original naming, because they are source documents. `askq2c-agent` stays as the PoC's full-loop agent.

## Acceptance criteria — output
1. **The subset's golden set is 100%, through IBM's profile:**
```
$ make test-integration
136 passed, 292 deselected
```
   (`test_golden_cash_application` is included: 40/40 packs as `ibm-pr-agent`, 3 corrections, payer resolution and route on every payment, IBM verify → 403.)
2. **`make e2e` runs on it** (fresh stack):
```
# e2e report — custom_invoices      23/23 checks passed
# e2e report — cash_application     25/25 checks passed
- ✓ phase 1: the IBM consumer gets the expected pack (payment_reconciliation)
- ✓ phase 1: everything else answers 403 to the IBM consumer
```
3. `make test` 290 passed + 18 frontend; `make test-policies` 39/39; `make lint`, `make typecheck` and `make contract-check` are clean. The contract is unchanged.

The clean-clone run for the whole batch (31–37) is in PR 032.

## Still open (the workshop)
- The real subset. It may mean renaming the pack to `payment_reconciliation` or extending `cash_application`. The golden set and synthetic data will follow.
- The real reviewers and owners per scope.
- Whether IBM calls verify in Phase 1 (D3).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
