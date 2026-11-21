# PR 006 — Synthetic data generator (base + `custom_invoices`) and golden set

Branch: `poc/006-synthetic-custom-invoices` → `main` · Issue: [docs/tasks.md §6](../tasks.md#6-gerador-de-dados-sintéticos-base--custom_invoices) · Depends on: 001 (merged)

## What
- `app/synthetic/common.py` (shared with issue 7):
  - Fixed seed `20261120` and UUIDv5 ids.
  - Fictitious corporate numbers with a correct check digit.
  - Qualified Invoice System tax: one floor rounding per rate per invoice.
  - Shared tenant `tenant.json`: scope tree `org → jp → jp/finance → q2c-invoicing | q2c-cash-application`, plus a second tenant `other`; people as role labels only (no names); consumers and their purposes.
- `app/domain_packs/custom_invoices/synthetic.py`: the generator lives in the pack (brief: each pack brings its data and golden set). Outputs under `data/synthetic/custom_invoices/`:
  - `customers.json`: 6 fictitious customers, and the issuer with registration number `T` + 13 digits.
  - `cases.jsonl`: 40 cases. INV-0007 is exactly the brief's example (plus `tax_by_rate`).
  - `rules.json`: 7 rules in the `memory_record` shape with their CEL condition.
  - `corrections.jsonl`: 3 corrections and the rule they should become.
  - `timeline.json`: seed → process A → corrections → approve → process B → retire (projection lagging) → process C; thresholds `1/1/30` and `3/2/30`.
  - `expected.json` and `manifest.json` (counts + sha256 of each file).
- `app/cli/gen_synthetic.py` + `synthetic` tool service (no network) + `make gen-data`.
- `tests/synthetic/test_custom_invoices_data.py`: 16 tests. The dev container now mounts `./data` read-only, so the drift test compares the committed data with a fresh run.
- Ruff: `E501` relaxed for `app/domain_packs/*/synthetic.py` (literal tables), and `allowed-confusables` for the full-width punctuation that is correct in Japanese text.

## The golden set (how to read `expected.json`)
For each case: `batch`, `group`, `as_of` (the issue date: rules apply as of the business event, Q8), `expected_rules` (**the exact set** a correct pack holds), `overridden` (local rule → the constraint that beat it) and `verify` (outcomes after named timeline steps).

It is computed from **hand-written Python predicates, one per rule, independent of CEL**, applying tenant, scope (`path_up` from `jp/finance/q2c-invoicing`, never sideways), `entities`, phase and "constraints beat defaults on the same target". Issue 11 will add a test that each CEL condition agrees with its predicate on all 40 cases.

| Group | n | What it proves |
|---|---|---|
| `happy` | 7 | Global QIS rules + the prohibition apply; nothing else |
| `learned_rule` | 10 | INV-0007/0012/0019 are corrected (R-001, R-002, R-001); after `approve-0042` the rule `CI-L-0042-COUPA` is in every CUST-0042 Coupa pack. Earlier packs are expected to `hold: new_mandatory_rule` |
| `unrelated` | 9 | CUST-0042 by email, CUST-0088 on Coupa, other customers: the learned rule never appears. One case is a pre-QIS reissue (2023-09-28): only the prohibition applies |
| `scenario_2` | 5 | `CI-L-SMALL-OMIT-REGNO` ("< ¥30,000: omit the number") is overridden by `CI-G-NO-REGNO-OMIT`. The boundary case (exactly ¥30,000) does not trigger it |
| `scenario_3` | 4 | `CI-C-0063-PAYTERMS` (contract clause) is expected even though the case text shares no word with it |
| `scenario_4` | 5 | `CI-C-0017-DEPT` is retired while the projection lags: earlier packs `hold: rule_no_longer_approved`; later packs must not contain it |

A rule in tenant `other` never appears (isolation).

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/006`.

**1. `make gen-data` twice → `git diff --exit-code data/synthetic` clean**
```
$ make gen-data
custom_invoices: cases=40, corrections=3, group:happy=7, group:learned_rule=10, group:scenario_2=5, group:scenario_3=4, group:scenario_4=5, group:unrelated=9, rules=7
$ git diff --exit-code data/synthetic && echo clean      → clean
$ make gen-data && git diff --exit-code data/synthetic   → clean
```
Drift detection, checked by hand: editing `"total": 990000` → `990001` in the committed `cases.jsonl` makes `make test` fail (`1 failed, 19 passed`); `make gen-data` restores it.

**2. No `float`; every total adds up** — `test_no_floats_anywhere`, `test_amounts_add_up_with_one_rounding_per_tax_rate` (one rounding per rate; e.g. INV-0002: 10% on ¥256,000 → ¥25,600, 8% on ¥8,000 → ¥640).

**3. No real company; every name carries a fictitious marker** — `test_every_company_is_clearly_fictitious` (marker 架空/サンプル/テスト on every customer and the issuer, plus a spot block list).

**4. `expected.json` covers the 6 groups** — `test_golden_set_covers_every_required_group` (each ≥ 4, total 38–42), plus one test per scenario and for the learned rule, tenant isolation, the Q6 rule (CEL on `customer_id` ⇒ that customer is in `entities[]`), the strict-threshold window (3 corrections, 2 reviewers, < 30 days) and timeline references.

**Definition of done**
```
$ make test       → 20 passed, 7 deselected
$ make lint       → exit 0
$ make typecheck  → 0 errors, 0 warnings, 0 informations (strict on app/)
$ make test-all   → … PASS: 4/4 … All test tiers passed
$ git status --short → (empty)
```

## Over the size budget
About **970 code lines** (data excluded), against the ~400 target:
- generator: ~350
- shared helpers + tenant seed (reused by issue 7): ~160
- tests: ~240
- CLI, compose, Makefile, README: the rest

Splitting would have shipped either a generator without tests or a tenant seed with no consumer. I preferred one reviewable unit. Tell me if you want me to hold future issues to the limit more strictly.

## Decisions worth a look
- **Scope keys, not UUIDs, in the data** (`jp/finance/q2c-invoicing`). The seed in issue 13 maps them to the UUIDv5 values already in `tenant.json`.
- **`targets` is a list** (the learned rule covers two fields: 備考 and the 検収書 attachment). Conflicts are resolved per shared target.
- **The proposed rule from a correction** is `default` / authority 10 / asserted by `policy` (under the model ceiling). The `approve-0042` step sets `mandatory` / 30, and that is why earlier packs are then expected to hold.
- **`overridden_by` attribution order:** prohibition, then mandatory, then key. This matters in scenario 2, where both `CI-G-QIS-REGNO` (mandatory) and `CI-G-NO-REGNO-OMIT` (prohibition) share the target.
- Cash application reviewers (R-101, R-102) are already in `tenant.json` so issue 7 adds no shared-file churn.
