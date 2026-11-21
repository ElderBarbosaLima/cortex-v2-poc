# PR 015 — Domain pack interface, fake pack, contract check, workflow guard

Branch: `poc/015-domain-pack-interface` → `main` · Issue: [docs/tasks.md §15](../tasks.md#15-interface-de-domain-pack--pack-falso-de-teste) · Depends on: 011 (merged)

## What
- **`app/domain_packs/base.py`**:
  - **`DomainPack`** (Protocol). Read-only members: `name`, `entity_types`, `condition_variables`, `condition_fields`, `entity_fields`, `targets`, `rule_body_schema`, `correction_schema`.
  - Methods: `resolve_entities`, `condition_context`, `correction_to_rule`, `search_text`, and `check_output` (optional, for verify).
  - `EntityMatch(key, confidence ∈ high|low|ambiguous|none, evidence, candidates)`; `RuleDraft(body, entities, enforcement, authority)`; `RuleBody` base schema (`text_ja`, `targets`, `condition`).
  - Generic helpers the core will use at admission (issue 17):
    - **`entity_literals(condition, entity_fields)`**: `invoice.customer_id == "CUST-0042"` → `customer:CUST-0042`, in either operand order. It makes the **Q6 rule** (a condition naming a customer must have it in `entities[]`) workflow-agnostic.
    - **`referenced_fields(condition, variables)`**: the `var.field` names a condition reads, string literals ignored, to reject a typo'd field the CEL type checker cannot see, since variables are `map(string, dyn)`.
    - `is_entity_key`.
  - Registry: `register_pack` / `get_pack` / `registered_packs`.
- **`tests/fakes/fake_pack.py`**: `FakeWidgetPack`, a made-up domain (widget orders, entity `widget:W-1`, targets `order.label` / `order.colour_check`). Core tests use this pack, never the real ones.
- **`tests/domain_packs/contract.py`**: **`assert_pack_contract(pack, cases, correction, corrected_case)`**, reusable for the real packs in issues 25–26. It checks:
  - declarations are consistent (entity fields exist and are typed);
  - every match uses a known band, names a canonical typed key, and names one *only when* confident;
  - the condition context has exactly the declared variables and nothing CEL must not see (via `to_cel_value`);
  - the rule from a correction validates against the pack's schema, has known targets, compiles, reads only allowed fields, passes Q6, stays under the model ceiling, and **applies to the case it came from**.
- **`tests/test_core_is_workflow_agnostic.py`**: the D18 guard (below).

## Acceptance criteria — output

**1. Typed interface (Pyright strict on the module)**
```
$ make typecheck → 0 errors, 0 warnings, 0 informations     (app/ is strict; tests type-check the fake pack against the protocol)
```
Pyright first rejected the fake pack as a `DomainPack`: mutable Protocol attributes are invariant, so a `dict` did not satisfy `Mapping`. The members are now read-only properties, so a pack declares plain class attributes. Its maps are `MappingProxyType`, which also satisfies Ruff's RUF012.

**2. The fake pack passes a reusable contract test**
- `test_the_fake_pack_honours_the_contract`.
- The contract is not vacuous: `test_the_contract_rejects_a_correction_whose_rule_misses_its_entity` (a Q6 breach) and `test_the_contract_rejects_a_float_in_the_condition_context` both fail it.
- Helpers: `test_referenced_fields_ignore_string_literals`, `test_entity_literals_in_either_operand_order`, `test_entity_keys_are_canonical_and_typed[…]` (a raw `架空電機` is not a key), `test_registry`.

**3. The import guard is active**
- `test_no_core_module_imports_names_or_branches_on_a_workflow`: outside `app/domain_packs/{custom_invoices,cash_application}/` and `app/cli/`, no module may contain `domain_packs.custom_invoices|cash_application`, the quoted names `"custom_invoices"`/`"cash_application"`, or `workflow ==|!=|in`.
- `test_the_guard_does_catch_an_offence` proves the pattern matches.
- **It caught something on its first run**: my own docstring in `base.py` quoted the forbidden `if workflow == ...`. I reworded the sentence and kept the guard strict.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/015`)
```
$ make test       → 123 passed, 38 deselected
$ make test-all   → … PASS: 33/33 … All test tiers passed
$ make lint       → exit 0
$ make typecheck  → 0 errors, 0 warnings, 0 informations
$ git status --short → (empty)
```

## Notes
- No pack is registered automatically yet. The service will register the real packs by configured name (issues 25–26), so the core still imports none of them.
- Size: 154 lines in `app/`, 284 in tests (the fake pack and the contract are test code by design).
