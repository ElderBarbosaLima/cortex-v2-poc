# PR 007 — Synthetic data generator for `cash_application` and its golden set

Branch: `poc/007-synthetic-cash-application` → `main` · Issue: [docs/tasks.md §7](../tasks.md#7-gerador-de-dados-sintéticos-cash_application) · Depends on: 006 (merged)

## What
- `app/domain_packs/cash_application/synthetic.py` writes `data/synthetic/cash_application/`. It uses the same files as custom invoices, plus `invoices.json`:
  - `customers.json`: 6 fictitious customers with their bank-registered names in half-width kana (全銀 style) and a garnishment (差押) flag. CUST-0105 has one registered.
  - `cases.jsonl`: 40 payments in the brief's format. **PAY-0113 is verbatim** and pays INV-0007 (¥990,000) minus ¥660.
  - `invoices.json`: the open invoice each payment is matched against.
  - `rules.json` (8), `corrections.jsonl` (3: R-101, R-102, R-101), `timeline.json`, `expected.json`, `manifest.json`.
- `expected.json` adds `resolution` per case (`payer_name_raw`, `expected_entity`, `band` high/low/ambiguous/none, `evidence`) and `route` (auto/review). These are **hand-labelled**: issue 26's resolver is tested against them, not against itself.
- **Refactor (neutral):** `RuleSpec`, `rule_document()` and the golden-set oracle `expected_rules()` move from the custom_invoices generator to `app/synthetic/common.py`. The oracle now also resolves **two default rules on one target** (higher authority, then nearer scope); cash application needs this for the learned fee rule against the global "check any difference" rule. **`data/synthetic/custom_invoices/` is byte-identical** after the move: `make gen-data` left no diff and the drift test passes.
- `tests/synthetic/test_cash_application_data.py`: 16 tests.

## The rules and what the golden set proves
| Rule | Enforcement | Condition | Group |
|---|---|---|---|
| `CA-G-EVIDENCE` | mandatory | `true`, in every pack | all |
| `CA-G-GARNISH` | mandatory | `payment.garnishment_registered == true` | garnishment (4): always review |
| `CA-G-EXACT-AMOUNT` | default (10) | `invoice.total != payment.amount` | any difference: review |
| `CA-G-NO-WRITEOFF` | prohibition | `invoice.total != payment.amount` | scenario 2 |
| `CA-L-SMALL-WRITEOFF` | default (20), team scope | `0 < diff < 1000` | scenario 2 (4): overridden by the prohibition; the `diff = 1000` case does not trigger it |
| `CA-C-0063-ALLOC` | mandatory | `payment.customer_id == "CUST-0063"` | scenario 3 (3): contract clause, no shared words |
| `CA-C-0017-HOLD` | mandatory | `payment.customer_id == "CUST-0017"` | scenario 4 (5): credit hold lifted with the projection lagging; earlier packs `hold`, later ones do not contain it |
| `CA-L-0042-FEE` (learned) | default (approved at 20) | see below | learned_rule (8) |

**Fee rule (brief):** after `approve-0042-fee` it is in the pack only for CUST-0042, confidently resolved, with a difference of 1–880. Boundary cases:
- 880 → applies;
- 881 → does not;
- 0 → does not;
- overpayment −1,000 → does not.

The same ¥660/¥440 difference from CUST-0088 or CUST-0121 never gets it. When it applies, `CA-G-EXACT-AMOUNT` is `overridden_by` it (authority 20 > 10) and the route becomes `auto`.

> **One change to the brief's condition:** I added `invoice.total > payment.amount`. The brief's `invoice.total - payment.amount <= 880` is also true for an overpayment (negative difference), which would book a refund-worthy surplus as a bank fee.

**Payer resolution:**
- `ｶ)ｶｸｳﾃﾞﾝｷ` (exact), `ｶｸｳﾃﾞﾝｷ(ｶ` (corporate-suffix form) and `ｶ)ｶｸｳﾃﾞﾝ` (truncated) → CUST-0042, **high**.
- `ｶ)ｻﾝﾌﾟﾙ` → **ambiguous** (サンプル商事 and サンプル建設), so no entity.
- `ｶ)ﾃｽﾄｼﾖｸﾋﾑ` → **low** (one edit away from テスト食品).
- `ｶ)ﾐﾄｳﾛｸｼﾔ` → **none**.

Every non-high payment routes to review, and no customer-scoped rule applies to it.

> **Conflict in the brief, resolved:** the truncated `ｶ)ｶｸｳﾃﾞﾝ` must resolve to CUST-0042, *and* ambiguity must go to review. A customer like 架空電工 (`ｶｸｳﾃﾞﾝｺｳ`) would make that truncated name ambiguous. So no such customer exists, and the ambiguity case uses a different shared prefix (`ｶ)ｻﾝﾌﾟﾙ`).

The cash rules are scoped to `jp/finance/q2c-cash-application` (or `jp/finance`). The invoicing team's local rules sit on a sibling node, and a rule in tenant `other` never appears.

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/007`.

**1–4 (as issue 6, for this workflow)**
```
$ make gen-data
custom_invoices: cases=40, corrections=3, group:happy=7, group:learned_rule=10, group:scenario_2=5, group:scenario_3=4, group:scenario_4=5, group:unrelated=9, rules=7
cash_application: cases=40, corrections=3, group:garnishment=4, group:happy=7, group:learned_rule=8, group:resolution=4, group:scenario_2=4, group:scenario_3=3, group:scenario_4=5, group:unrelated=5, rules=8
$ git diff --exit-code data/synthetic   → clean (first and second run)
```
- No `float`, JPY integers: `test_no_floats_and_amounts_are_yen_integers`.
- Fictitious names: `test_every_customer_is_fictitious`.
- Groups: `test_golden_set_covers_every_required_group` (40 cases, each required group ≥ 3) plus one test per scenario, the fee rule, the unrelated-fee cases, garnishment, tenant isolation and the Q6 entity rule.

**Plus: `expected_entity` and confidence band per resolution case**
`test_three_spellings_resolve_to_cust_0042_with_high_confidence` and `test_ambiguous_low_and_unknown_payers_go_to_review`. The field is `resolution.band`; the issue text called it `expected_confidence_band`.

**Definition of done**
```
$ make test       → 36 passed, 7 deselected
$ make lint       → exit 0
$ make typecheck  → 0 errors, 0 warnings, 0 informations
$ make test-all   → … PASS: 4/4 … All test tiers passed
$ git status --short → (empty)
```

## Notes
- Size: **+741 / −77** code lines (data excluded). About 300 are the generator, 230 the tests and 100 the moved oracle. Above the ~400 target again, mostly because the shared oracle was generalised here instead of in a separate PR.
- Process slip: my first commit on this branch had one line over 100 characters in a test (`make lint` exit 2). It is fixed in the next commit (`bff08b6`). From now on I run `make lint` before every commit, not after.
- The CEL ↔ predicate agreement test for both workflows comes with the evaluator (issue 11).
