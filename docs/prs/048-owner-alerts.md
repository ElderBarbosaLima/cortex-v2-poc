# PR 048 — Owner alerts, and the April renewal through the queue

Branch: `poc/048-owner-alerts` → `main` · Issue: [docs/tasks.md §48](../tasks.md) · Depends on: 33, 42 (merged)

Slide 18 says owners "check rules Arbitr flags: review date due, frequent overrides, or a conflict". And: "Why April matters: contracts renew and customers merge, so many rules change at once."

## What
- **`GET /v1/cortex/rules/alerts`** (`list_rule_alerts`) lists the approved rules in a scope that need a look, each with why:
  - **`review_due`:** the current version is older than `REVIEW_EVERY_DAYS` (365 by default);
  - **`expiring`:** `valid_to` falls within `within_days`;
  - **`often_overridden`:** specialists corrected 3+ cases in 90 days where the rule was applied. A confirmed outcome is not an override;
  - **`keeps_losing`:** the rule was served but overridden by another rule in 3+ packs in 90 days (a conflict).

  Every count comes from what was actually served and corrected: `served_pack_item` and the correction log.
- **`POST /v1/cortex/rules/renew`** (`renew_rules`), the April changeover:
  - For each rule, it proposes a **new version with the new end date** as a supersede, made by the person asking.
  - Nothing changes until the owner approves. Then the old version becomes `superseded` and the new one applies.
  - Asking twice queues it once (`pending`).
  - Combined with bulk approval (issue 47), renewing a hundred rules takes two steps.
- **The Desk:** the "Expiring soon" panel becomes **"Needs your attention"**. It shows each rule with its alerts, and for expiring rules a checkbox, a new end date (defaulting to next 31 March) and **Renew selected (N)**.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_owner_alerts.py"
PASSED test_each_kind_of_alert          # expiring · often_overridden (3 corrections where it applied) · keeps_losing
                                        # (lost to a group prohibition in 3 packs) · review_due (with a 0-day cycle)
PASSED test_april_renewal_goes_through_the_queue
                                        # proposed → asked again: pending → owner approves → old superseded,
                                        # new approved with valid_to 2028-03-31
$ make test-integration → 162 passed · make test → 321 + frontend 31 (+1: alerts and renew)
```
`make lint`, `make typecheck` and `make contract-check` are clean. The contract changes are additions only: `list_rule_alerts`, `renew_rules` and their schemas.

## Notes
- **The thresholds (3 in 90 days, review every 365 days)** are starting points for the workshop. Slide 18 lists "how often owners check suggestions" as an open point.
- **A customer merger**, the other half of April, is a supersede with new entities. That works today through proposing with `supersedes`. A dedicated "merge customer" flow waits for IBM's examples.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
