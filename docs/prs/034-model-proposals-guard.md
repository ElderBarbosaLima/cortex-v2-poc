# PR 034 — Machines propose, people approve, enforced by the database

Branch: `poc/034-model-proposals-guard` → `main` · Issue: [docs/tasks.md §34](../tasks.md) · From Nake's review

## The decision I took (you asked me to assume the likely path)
Nake: *"model-proposed rules go through the review queue like everything else. They never write to the ledger directly."*
- **(a) Keep proposals in the ledger as `proposed`.** Packs read only approved records, so a proposal is invisible to every agent until a person approves it. Rev. B's envelope has `proposed` as a status for this reason, and a rejection stays auditable in the same history (Q5).
- **(b), keeping proposals out of the ledger until approval,** would split the audit trail and the erasure lineage for no gain in safety.

I took (a) and made the database refuse what Nake is guarding against.

## What
- **Migration `013_machine_proposals_guard`**: a `BEFORE INSERT` trigger on `memory_record`. A record asserted by a `model` or a `policy`:
  - **must start as `proposed`**;
  - **can become `approved` only with `approved_by.approver_type = user`.**

  The application already behaved this way (admission, writeback, review queue). The trigger makes it impossible to bypass, even from SQL or a future code path. People and imports are untouched.

## Acceptance criteria
```
$ make test-integration ARGS="-rA tests/integration/test_machine_proposals_guard.py"
PASSED test_a_machine_record_cannot_be_born_approved[model | policy]          # "a model record starts as proposed (review queue first)"
PASSED test_a_machine_proposal_is_approved_only_by_a_person[model | policy]   # approver_type model → refused; user → approved
PASSED test_people_and_imports_are_untouched
5 passed
```
The whole integration suite gives 136 passed. That includes the LLM-drafted and policy-drafted rules approved by owners, the golden sets and the review queue.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
