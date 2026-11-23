# PR 031 — Scopes only tighten: checked at intake

Branch: `poc/031-tighten-only` → `main` · Issue: [docs/tasks.md §31](../tasks.md) · From Nake's review

## What
- **`admission.inherited_conflicts(session, proposal, ancestors)`.** A `default` rule at a lower scope **loosens** an approved, still valid constraint (mandatory or prohibition) when all of these hold:
  - the constraint sits at an **ancestor** scope (OPA's readable scopes, minus the scope itself);
  - it is in the same domain;
  - its entities are compatible with the proposal's;
  - the two share a **target**.

  Such a rule is reported as `loosens_inherited_constraint`, with the constraint's id. A constraint proposed at a lower scope **tightens** and passes. Other targets, other entities and sibling scopes never count.
- **What happens on a loosening rule.** Your answer was "assume the likely path", so I took the default the issue proposed:
  - **machines are refused:** an agent alone (`propose_record` without an end user, asserted as `model`) gets a 422, and a writeback suggestion, by the pack's policy or by the LLM, is never proposed: the reason comes back in `proposal_problems`;
  - **a person may propose it.** `ProposalAccepted.warnings` lists the conflict, the proposal's `source.inherited_conflicts` records it, and the Decision Desk shows it ("This rule would loosen an inherited constraint…"), so the owner decides knowingly.
- **Read time is unchanged:** a constraint still never loses (scenario 2). Seed rules are imported legacy rules: they skip this check, so the golden sets keep their scenario 2 rule.

## Acceptance criteria
```
$ make test-integration ARGS="-rA tests/integration/test_tighten_only.py"
PASSED test_a_person_may_propose_a_loosening_default_but_the_owner_is_told      # 201 + warnings + source.inherited_conflicts
PASSED test_a_machine_may_not_loosen_an_inherited_constraint                    # 422 loosens_inherited_constraint
PASSED test_what_tightens_or_does_not_overlap_passes[stricter local rule | another target]
PASSED test_a_constraint_about_another_entity_or_a_sibling_scope_does_not_count
PASSED test_a_suggested_rule_that_would_loosen_is_not_proposed                  # writeback: no proposal, problem reported
6 passed
```
`make lint`, `make typecheck` and `make test` pass, and `make test-integration` gives 113 passed, golden sets included. The contract gains `ProposalAccepted.warnings`. The clean-clone run covers the whole batch (31–37): see PR 032.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
