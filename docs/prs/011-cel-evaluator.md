# PR 011 — `ConditionEvaluator` (CEL)

Branch: `poc/011-cel-evaluator` → `main` · Issue: [docs/tasks.md §11](../tasks.md#11-conditionevaluator-cel) · Depends on: 001 (merged)

## What
- `app/conditions/base.py`:
  - The `ConditionEvaluator` protocol (`compile(source, variables) → CompiledCondition`, `evaluate(condition, context) → bool`).
  - Typed errors `ConditionCompileError` / `ConditionEvalError`, each with a `code`.
  - The context rules, written down: int amounts, dates as day numbers, booleans only, business-event dates (Q8).
- `app/conditions/cel.py` (`CelExprEvaluator`, `cel-expr-python==0.1.4`, pinned exactly because the API is 0.x):
  - One cached environment per variable set; every variable is declared `map(string, dyn)`.
  - Custom function `date("YYYY-MM-DD") → int`.
  - **Compile** checks: `date(...)` literals must be real dates, the expression must compile against the declared variables, and the return type must be `BOOL`.
  - **Evaluate:** context values are converted first, refusing `float`, `datetime` and ints outside int64. A CEL error value becomes a `ConditionEvalError` carrying CEL's code (`OUT_OF_RANGE`, `NOT_FOUND`, …).
- `docs/discovery.md` §5: the characterisation result and the date decision. README: a "Rule conditions" section.

## The one finding that shaped the design
cel-expr-python accepts a Python `datetime` **only as a top-level variable**, never inside a map: `Non-CEL value type for 'invoice['issue_date']'`. Every condition in the brief reads a date inside a map (`invoice.issue_date`). So dates reach CEL as an `int`, the number of days since 1970-01-01 of the calendar date, and `date("2023-10-01")` returns the same int. Consequences:
- The brief's conditions keep their exact text.
- Comparisons are calendar-date comparisons, with no time zone involved.
- `payment.value_date - invoice.issue_date <= 30` is a difference in days.
- A `datetime` in the context is refused, so nobody passes a server timestamp by mistake.

## Acceptance criteria — output
`tests/conditions/test_cel_characterisation.py` (these pin behaviour the README does not document; a version bump that changes one fails here first):

| # | Criterion | Test | Result |
|---|---|---|---|
| 1 | Nested dict: `invoice.customer_id == "CUST-0042" && invoice.channel == "coupa"` | `test_1_nested_dicts` (also `invoice.lines[0].desc`) | ok |
| 2 | `invoice.issue_date >= date("2023-10-01")` before / on / after the day | `test_2_date_comparison[2023-09-30→False, 2023-10-01→True, 2026-12-03→True]`, `test_2_date_difference_is_in_days` | ok |
| 3 | `invoice.total - payment.amount <= 880` with 990000/989340 → True, 990000/989119 → False; int64 overflow is an error, never a wrap | `test_3_fee_condition[…]`, `test_3_int64_overflow_is_an_error_never_a_wrap` (`OUT_OF_RANGE`) | ok |
| 4 | Unknown variable, unknown function, bad syntax fail at **compile** | `test_4_bad_conditions_fail_at_compile_time`: `invoicex.total`, `nosuchfn(…)`, `invoice.total >`, plus `date("2023-13-01")`, `date(1)` and a non-boolean `invoice.total` | ok |
| 5 | A `float` in the context → error before evaluation | `test_5_unsafe_values_are_refused_before_evaluation` (also `datetime` and 2**63) | ok |
| 6 | Wheel on `linux/arm64` and `linux/amd64` | `make build-multiarch` exit 0, plus the evaluator run in each image | see below |

```
$ for p in linux/amd64 linux/arm64; do docker buildx build --platform $p --load … && docker run --platform $p … ; done
x86_64 True
aarch64 True
```

**Promised in issue 6: CEL ↔ golden-set agreement** (`tests/conditions/test_cel_agrees_with_golden_set.py`)
- `test_cel_and_predicate_agree_on_every_case[custom_invoices]`: 8 rules × 40 cases, 0 disagreements.
- `test_cel_and_predicate_agree_on_every_case[cash_application]`: 9 rules × 40 cases, 0 disagreements.
- `test_every_committed_rule_condition_compiles`: all `rules.json` conditions plus the learned rules.
- `test_the_agreement_check_does_catch_a_wrong_condition`: `<= 881` instead of `<= 880` disagrees on exactly `PAY-0127`, the ¥881 case. The agreement test is not vacuous.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/011`)
```
$ make test       → 86 passed, 28 deselected
$ make test-all   → … PASS: 4/4 … All test tiers passed
$ make lint       → exit 0
$ make typecheck  → 0 errors, 0 warnings, 0 informations (strict on app/; the untyped library sits behind one Any)
$ git status --short → (empty)
```

## Notes
- The fallback (`common-expression-language`) was not needed. The interface stays, so a swap is still one adapter.
- The library has no identifier introspection (only `eval`, `return_type`, `serialize`). The Q6 rule (a CEL condition on `customer_id` must match `entities[]`) stays a regex check over the condition text, done at admission (issue 17).
- What a `ConditionEvalError` means on the applicability path (e.g. `NOT_FOUND` on a mandatory rule) is decided in issue 19. The constraint must not silently drop out.
- Size: 200 lines in `app/`, 250 in tests.
