# PR 046 — Spotting patterns: "the same fix 6 times" becomes a rule suggestion

Branch: `poc/046-patterns` → `main` · Issue: [docs/tasks.md §46](../tasks.md) · Depends on: 42 (merged)

IBM's brief (§02): "Cortex spots a pattern, e.g. same fix 6 times". Slide 17 promises figures from IBM's own history: "repeated patterns found in [__] months of CAT matches; rules Arbitr would suggest".

## What
- **A threshold per domain.**
  - `RULE_SUGGESTION_BY_DOMAIN` defaults to `{"cash_allocation": "6/1/90"}`: 6 different cases, at least 1 corrector, within 90 days.
  - It counts per conflict key, which for `cash_allocation` means exception type × customer.
  - The other domains keep the default (`1/1/30`), so their golden sets and `make e2e` are unchanged.
  - The live outcome loop (`/outcomes`, `/corrections`) uses the same per-domain threshold.
- **Mining a history** (`app/services/patterns.py`, `make mine-patterns`):
  - It reads a history of manual resolutions: one line per payment, with its case, outcome and `decided_at`. That's CAT's export once IBM shares it; until then it's synthetic.
  - It groups the resolutions by conflict key, inside the window.
  - Every group that reaches the threshold gets **one** suggested rule: the pack's own draft from the group's **most typical case** (its most frequent gap). For a ¥330 fee seen 6 times and a ¥440 fee once, it suggests `expected_fee: 330`.
  - The suggestion is asserted by `policy` (pattern-mining), at authority ≤ 10, and only becomes a rule once the customer's owner (issue 45) or the scope owner approves it. Its `source` records how many cases, which ones, the window and the threshold.
  - Running it again is safe: an approved or waiting suggestion is reported as `already_approved` or `already_waiting`, not proposed twice.
  - The command prints one line per pattern: entities, exception type, number of cases and what happened. These are the numbers slide 17 asks for.
- **Synthetic history** (`data/synthetic/cash_allocation/history.jsonl`):
  - CUST-0105 paid short by the bank fee 7 times in three months (6 × ¥330, 1 × ¥440): a pattern.
  - CUST-0102 had 3 combined payments: below the threshold.

## Acceptance criteria — output
```
$ make mine-patterns        (live stack)
{"policy": "6/1/90", "rows": 10}
{"entities": ["customer:CUST-0102"], "targets": ["allocation.combined_payment"], "cases": 3, "outcome": "below_threshold", …}
{"entities": ["customer:CUST-0105"], "targets": ["allocation.bank_fee"], "cases": 7, "outcome": "proposed", "record_id": "01a2089d-…", …}
$ make mine-patterns        (again)
… "cases": 7, "outcome": "already_waiting", "record_id": "01a2089d-…"   ← the same one, nothing new
```
```
$ make test-integration ARGS="-rA tests/integration/test_patterns.py"
PASSED test_a_repeated_fix_becomes_one_suggestion_and_only_once
       # proposed, policy/pattern-mining, authority 10, engine expected_fee 330, source.pattern {7, 90, 6};
       # again → already_waiting; 200 days later → nothing (outside the window)
PASSED test_the_threshold_is_per_domain
```
- `make test-integration` → 160 passed
- `make test` → 320 passed
- `make lint`, `make typecheck` and `make contract-check` are clean.

## Notes
- **What counts as "the same fix"** is the conflict key: the same exception type for the same customer. Brief question 5 asks IBM whether it should count per customer or across customers, and over what window. Both are configuration.
- **The acceptance metric** (suggestions the owners accept, slide 26 target 70%) is part of issue 49.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
