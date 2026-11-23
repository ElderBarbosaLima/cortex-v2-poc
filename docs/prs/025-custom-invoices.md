# PR 025 — Domain pack `custom_invoices`, seed rules, golden set

Branch: `poc/025-custom-invoices` → `main` · Issue: [docs/tasks.md §25](../tasks.md) · Depends on: 22, 6 (merged)

## What
- **`app/domain_packs/custom_invoices/pack.py`**, the first real pack (D18):
  - **entities:**
    - `customer:<id>`, resolved by `customer_id` against the customer master. An unknown id, or a name only, resolves to nothing: names are never identities;
    - `channel:<name>`.

    Only `invoice.customer_id` is an entity field in conditions. The channel is a property of the case, so `invoice.channel == "coupa"` needs no entity (Q6);
  - **CEL variable `invoice`:**
    - fields `case_id`, `customer_id`, `channel`, `issue_date` (a `date`), `currency`, `total`, `tax_total`, `tax_rates`, `line_count`, `has_po_number`;
    - int JPY amounts only;
  - **targets:**
    - `invoice.registration_number`, `invoice.tax_breakdown`;
    - `invoice.remarks.payment_terms`, `invoice.remarks.department_code`, `invoice.remarks.po_number`;
    - `invoice.attachments.kenshusho`;
  - **schemas:**
    - `InvoiceRuleBody` (`RuleBody`, plus optional `requirements` per target);
    - `InvoiceCorrection` (the `corrections.jsonl` shape);
  - **`correction_to_rule`:** customer + channel condition, the corrected targets and their values. It is a suggestion: enforcement `default`, authority 10;
  - **`check_output`:** every target a constraint or applied rule names must be present in the drafted invoice, and a registration number must be `T` plus 13 digits.
- **Discovery, not names:** `app.domain_packs.discover()` registers every subpackage that exports `PACK`. The service calls it at startup and `make seed` calls it too. The D18 guard still passes: no core module names a workflow.
- **`make seed` loads each pack's `rules.json`** (`app/seed/rules.py`, generic over packs):
  - each rule is checked by the admission gate first;
  - it is appended as approved, with its fixed `record_id`, to its own tenant, which is how the other tenant's rule lands in `other`;
  - seeding again adds nothing.
- **Golden-set harness** (`tests/golden/harness.py`, generic; issue 26 reuses it). It replays `timeline.json` through the real API:
  - packs for every case, compared exactly against `expected_rules` and `overridden`, which also covers the absences;
  - verify at every step `expected.json` names;
  - the corrections, compared field by field with `expected_rule`;
  - the named owner's approval, and the review outcomes (one approved, the duplicates superseded);
  - the retirement with the projection left lagging;
  - tenant isolation.

  It collects every mismatch before failing.

## Two decisions you made (2026-11-22)
The generator's oracle ignores validity, and two places disagreed with the gate:
1. **A learned rule has no lower validity bound** (`NO_LOWER_BOUND`, 1900-01-01, in `writeback.py`; core, one line).
   - A correction teaches a requirement that already existed, and nobody knows since when.
   - Issue 22 dated it from the corrected case, so INV-0012 (21/11) and INV-0019 (19/11) passed after approval where the golden set expects `hold: new_mandatory_rule`.
   - Putting the old dating back on purpose gives exactly those two mismatches.
2. **A reissue is judged as of its reissue date** (pack generator only). For INV-0020 (`pre_qis_reissue`, issued 2023-09-28), `expected.json`'s `as_of` is now the first day of its batch, 2026-11-16. Its CEL conditions still read the 2023 `invoice.issue_date`, so only the prohibition applies, as the oracle says. `make gen-data` changed `expected.json` (1 line) and the manifest hash.

## The criterion "the core did not change in this issue"
It was met only partly, so here is everything outside the pack:
- `services/writeback.py`: decision 1 above;
- `utils/business_dates.py`: the `NO_LOWER_BOUND` constant;
- `main.py` and `domain_packs/__init__.py`: calling and defining pack discovery;
- seed code (`seed/rules.py`, `cli/seed.py`), which the criterion allows.

Nothing in the core branches on or names a workflow.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_golden_custom_invoices.py"
seed: [('custom_invoices', 7, 0)]
custom_invoices: {'packs': 40, 'verifications': 46, 'corrections': 3, 'outcomes': 1, 'isolation': 1} — 0 mismatch(es)
PASSED tests/integration/test_golden_custom_invoices.py::test_the_custom_invoices_golden_set_passes_in_full
```
- **Pack contract:** `tests/domain_packs/test_custom_invoices_pack.py` covers:
  - the contract over all 40 cases;
  - each CEL condition (6 seed rules plus the learned rule), evaluated with the pack's context, against the generator's independent Python predicate on all 40 cases;
  - every seed rule admissible;
  - the resolver by id, never by name;
  - correction → rule, where the draft has the same condition, targets and entities as the expected learned rule;
  - `check_output`.
- **Scenarios:**
  - **2:** the local omission (`CI-L-SMALL-OMIT-REGNO`) is overridden by the global prohibition in INV-0008/9/16/40;
  - **3:** the contract clause (`CI-C-0063-PAYTERMS`) is in every CUST-0063 pack without any shared words;
  - **4:** a pack held after the retirement with `rule_no_longer_approved`, and the rule absent in batch C.

Live, on the clean-clone dev stack:
```
make seed (1st) → pack custom_invoices: 7 rules added, 0 already present
make seed (2nd) → pack custom_invoices: 0 rules added, 7 already present
INV-0008 scenario_2 → constraints NO-REGNO-OMIT, QIS-REGNO, QIS-TAX; CI-L-SMALL-OMIT-REGNO overridden by CI-G-NO-REGNO-OMIT  (= expected)
INV-0010 scenario_3 → + CI-C-0063-PAYTERMS constraint  (= expected)
INV-0013 unrelated  → only the three global rules  (= expected)
verify with an empty output → hold, output_check_failed (3 / 4 / 3 reasons: what the rules require)
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/025`, `make dev` + `make seed`)
```
$ make test             → 192 passed, 97 deselected
$ make test-integration → 97 passed, 192 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec (no API change)
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
```

## Notes
- **What `check_output` cannot check yet.** It checks presence and the registration-number format. An "omit" default (the small-invoice omission) would need an `effect` in the rule body. That rule is always overridden by the prohibition, so the golden set never exercises it.
- **The customer master is the pack's synthetic `CUSTOMERS`.** In production it would be the client's customer master.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
