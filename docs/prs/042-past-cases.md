# PR 042 — Past cases as memory: every reviewed outcome, and similar cases in the pack

Branch: `poc/042-past-cases` → `main` · Issue: [docs/tasks.md §42](../tasks.md) · Depends on: 41 (merged)

IBM's brief (§03, items 2 and 3): "When their AI sends a customer and payment, return approved rules, **similar past cases** and a reference ID." And: "Save each **confirmed or corrected** match with the specialist's reason."

## What
- **Every reviewed outcome is remembered, not only corrections.**
  - An outcome that confirms what was done is stored as a past case and proposes no rule. A pack says so by raising `NoRuleToLearnError` from `correction_to_rule`; in `cash_allocation`, that is `result: confirmed` or an outcome with no exception type.
  - A corrected outcome still runs the learning loop (D17 thresholds), and is also remembered.
- **Past cases are the `decision` records the PoC already writes for each correction.** They are sealed, approved, tagged with the case's entities, and erasable. Each one now also carries:
  - the pack's **`case_summary`**: a new pack hook, implemented in all three packs and the test pack. It is a small, non-identifying summary, for example customer, amount, gap, exception, result, chosen invoices and reason;
  - **`source.domain`**.
- **The context pack carries up to 5 `similar_case` records**:
  - decisions in the same domain, in a readable scope, within the purpose's ceiling, that share an entity with the case (the same customer);
  - ranked by the closest `gap`, then the most recent;
  - read from the ledger, never from search.

  They are stored with the pack (migration `014` adds the role), so a replay shows exactly what was served, and erasure shreds their bodies in old packs too. Verify ignores them: it reads only rules.
- **Contract:** `PackRecord.role` gains `similar_case`. That is an addition only.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_past_cases.py"
PASSED test_a_confirmed_outcome_is_remembered_and_proposes_nothing
       # no proposal, no review case; the next pack for that customer carries it, with
       # {customer_id, amount 549340, gap 660, candidate_count 1, result confirmed, exception bank_fee,
       #  selected_invoices [INV-A100], reason "手数料 660 円。いつも通り。"}
PASSED test_a_corrected_outcome_is_remembered_and_still_suggests_a_rule
PASSED test_the_closest_cases_come_first_and_never_another_customers   # gap 700 before gap 5000; the other customer's is absent
PASSED test_a_stored_pack_replays_its_cases_and_erasure_shreds_them      # replay equal; after erasure: body null, gone from new packs
```
- `make test-integration` → 149 passed
- `make test` → 319 + 29 frontend
- `make e2e` (fresh stack) → 23/23 and 25/25
- `make lint`, `make typecheck`, `make contract-check` and `make migrate-check` are clean.

## Notes
- **Similarity is deliberately simple:** the same customer, then the closest gap. When IBM's real cases arrive (issue 49, replay), we'll see whether it should also weigh the exception type or the remittance pattern. Brief question 2 asks IBM how they define "similar".
- **A case of another customer in the same customer group** comes in with issue 45, the customer hierarchy.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
