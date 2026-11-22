# PR 019 — Access gate (1/2): applicability and resolution

Branch: `poc/019-access-gate-applicability` → `main` · Issue: [docs/tasks.md §19](../tasks.md) · Depends on: 10, 18 (merged)

## What
- **`app/services/access_gate/applicability.py`** decides which approved rules apply to one case (Rev. B rule 4).
  - **Candidates come from SQL over `memory_record_current` and nothing else.** Neither search nor the case's words can add or remove a rule. A rule is a candidate when all of these hold:
    - `kind = rule`, `status = approved`, and any enforcement. Mandatory, prohibition **and** default are all needed, so defaults can be resolved against constraints;
    - its `schema_version` belongs to the case's domain pack (`<pack>.rule/%`). This is generic: the core never branches on the workflow (D18);
    - `scope = ANY(readable scopes)`. OPA's `readable_scopes` are already the requested scope plus its ancestors, nearest first, so this is readable ∩ `path_up`. A sibling scope never applies;
    - `entities` is empty, or shares at least one key with the case's (`&&`);
    - it is **valid at `as_of`**, the business date of the event, never the clock (Q8). `valid_from` is inclusive and `valid_to` exclusive, both at midnight JST.
  - **Then each candidate's CEL condition runs** against the pack's context for the case (`case_facts(pack, case)`: the pack's resolved entities plus `condition_context`).
  - **A condition that cannot be evaluated is reported in `unevaluable`, never dropped.** Examples are a missing field or an invalid body. Issue 20 must hold the pack rather than answer without a constraint it could not check.
- **`app/services/access_gate/resolve.py`** resolves rules per target:
  - a prohibition or mandatory rule is a **constraint** and never loses, not even to another constraint;
  - a default that shares a target with a constraint is **`overridden_by`** that constraint (a prohibition is credited before a mandatory rule);
  - between defaults, higher authority wins, then the nearer scope;
  - an exact tie overrides nothing, so a person sees both rules.
- **`app/utils/business_dates.py`**: `JST` and `business_start()` are now shared by the API (issue 17) and the gate.
- **Not in this PR:** search, context-pack storage and HTTP (issue 20).

## Acceptance criteria — output
These are integration tests on real Postgres and OPA with the fake pack. Readable scopes come from OPA's real read decision.
```
$ make test-integration ARGS="-rA tests/integration/test_access_gate_applicability.py"
PASSED test_a_mandatory_rule_with_no_word_in_common_with_the_case_still_applies       # (1) role: constraint
PASSED test_a_global_prohibition_overrides_a_local_default_on_the_same_target[local-higher]   # (2) local default authority 30 at the team
PASSED test_a_global_prohibition_overrides_a_local_default_on_the_same_target[global-higher]  # (2) …and 10; prohibition at the tenant either way
PASSED test_a_rule_about_another_customer_never_appears                               # (3) appears only for its own entity
PASSED test_validity_is_judged_at_the_events_date_not_the_clock                       # (4) 2025-only rule applies on 2025-06-01 and 2025-12-31, not on 2026-01-01
PASSED test_a_false_condition_or_an_unapproved_rule_does_not_apply
PASSED test_a_sibling_scope_never_applies                                             # cash-application rule never at q2c-invoicing
PASSED test_a_condition_that_cannot_be_evaluated_is_reported_not_dropped              # mandatory rule lands in `unevaluable`
PASSED test_case_facts_come_from_the_pack
9 passed
```
The unit tests in `tests/services/access_gate/test_resolve.py` (8 passed) cover:
- the prohibition beating the default under any authority and in any input order;
- constraints never being overridden;
- the order of precedence: prohibition before mandatory, then authority, then the nearer scope, then ties;
- rules on different targets not competing;
- **agreement with the golden-set oracle** (`app/synthetic/common.py::expected_rules`, written separately for the golden set) over 300 seeded random rule sets.

**Do the tests catch mistakes?** I broke the code twice on purpose; the tests failed both times, and I restored the code afterwards:
```
-- mutation 1: drop the entity filter from the SQL
FAILED test_a_global_prohibition_overrides_a_local_default_on_the_same_target[global-higher]
FAILED test_a_rule_about_another_customer_never_appears
-- mutation 2: let a default with authority > 20 escape a prohibition
FAILED test_resolve.py::test_a_global_prohibition_beats_a_local_default_whatever_the_rank[30-10]
FAILED test_resolve.py::test_a_prohibition_is_credited_before_a_mandatory_rule
FAILED test_resolve.py::test_resolution_agrees_with_the_golden_set_oracle
FAILED test_access_gate_applicability.py::…[local-higher]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/019`, `make dev` healthy)
```
$ make test             → 168 passed, 63 deselected
$ make test-integration → 63 passed, 168 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec (no API change)
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
```

## Notes for issue 20
- **Sensitivity ceiling.** The gate does not filter rules by the purpose's sensitivity ceiling. If a constraint above the ceiling applies, issue 20 must **hold** the pack, not drop the rule. A rule the agent may not see must not silently vanish from its constraints.
- **Bodies are opened one by one.** Each candidate is opened and compiled separately. This is fine at PoC volumes; caching compiled conditions by `(record_id, version)` would be an easy later step.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
