# PR 040 — IBM's workflow as a pack: two-layer rules and the input IBM sends (issues 40 and 41)

Branch: `poc/040-cash-allocation-pack` → `main` · Issues: [docs/tasks.md §40, §41](../tasks.md) · Depends on: 19, 22 (merged)

IBM's Dev Brief (22 Nov) and the Workshop 1 draft change Cortex's role in the flow: **IBM's AI matches; Cortex is its memory.** You asked for issues 40–49 and to start on them. This PR covers 40 and 41: a rule format the rule engine and the LLM share, and the case in the shape IBM's AI sends.

## What
- **New pack `cash_allocation`**, found by discovery like the others (D18: the core has no new branch). `cash_application` stays as the earlier pack proving D18; its golden set and `make e2e` are unchanged.
- **Two layers, one copy** (slides 15–16):
  - `engine`: the exact fields IBM's rule engine runs. There is one schema for each of the five exception types IBM listed:
    - combined payment: `pick`, `max_invoices`;
    - bank fee: `expected_fee`, `max_fee` ≤ ¥880, `treatment`;
    - tax difference: `rounding`, `tolerance`, `treatment`;
    - duplicate payment: `action`;
    - partial payment: `action`, `instalments`.

    Unknown fields are refused.
  - `text_ja`: the guidance IBM's LLM reads.
  - `condition` (CEL): when the rule applies.
  - A rule without `exception` is guidance only, so it has no engine fields.
- **The admission gate names the field.** An invalid body is refused with its path, for example `engine.expected_fee: Input should be less than or equal to 880`, so whoever wrote the rule knows what to fix. This change is in the core and applies to every pack.
- **The case IBM's AI sends** (slide 22):
  - It carries the customer number, the payment, the gap, the candidate invoices and the remittance details.
  - The customer comes from CAT, so it is taken as given and the payer-name normaliser is not used.
  - The gap is IBM's own, or computed when they don't send one. CEL sees the customer, amount, date, gap, number and total of the candidates, and whether there is a remittance.
- **Learning from an outcome:** a reviewed outcome with an exception type becomes a rule with both layers, in the note's language. A bank-fee outcome on a ¥440 shortfall gives `expected_fee: 440`.
- **Synthetic data** (`data/synthetic/cash_allocation/`):
  - 5 fictitious customers, including 山田商事, the slide 15 example;
  - 6 rules, at least one for each exception type;
  - 10 payments in IBM's shape;
  - a golden set from independent predicates.
- **[`docs/examples/rule-format.md`](../examples/rule-format.md): the example slide 15 asks the dev team for.** It shows the Excel row → the Cortex rule, what each layer holds, the engine fields for each exception type, and why "Ask Tanaka" becomes the rule's owner, not its text.

## Fixed while writing it
- **A ¥1 overpayment also matched the duplicate-payment rule.** In the first draft, "duplicate" meant any negative gap. It now means paid at least twice the candidates' total.

## Acceptance criteria — output
1. **Each type has a schema checked at admission, and an invalid field is refused with its path:**
```
$ make test → 319 passed (29 new: the contract, the slide 15 example, 7 invalid engines with their paths,
              5 exception types, guidance-only, IBM's input and the gap, CEL vs oracle × 6 rules,
              6 seed rules admissible, outcome → rule)
```
2. **The example is admitted and appears in the pack of a case it matches; a customer's rules never reach another customer:**
```
$ make test-integration → 145 passed
  test_golden_cash_allocation: 10/10 payments sent by ibm-pr-agent get exactly their rules
  PAY-A001 (山田商事, ¥660 short) → AL-C-0101-FEE, engine {expected_fee: 660, max_fee: 880, treatment: fee_expense}
  over HTTP, expected_fee 990 → 422 with "engine.expected_fee"
$ make seed → pack cash_allocation: 6 rules added
```
`make lint`, `make typecheck`, `make contract-check` and `make migrate-check` are clean. The contract does not change.

## Next
- 42: past cases as memory (`POST /outcomes`, `similar_cases` in the pack).
- 43–44: IBM's profile (context, rule sync, outcome) and the sync endpoint.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
