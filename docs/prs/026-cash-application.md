# PR 026 — Domain pack `cash_application`, payer resolution, golden set

Branch: `poc/026-cash-application` → `main` · Issue: [docs/tasks.md §26](../tasks.md) · Depends on: 25 (merged)

## What
- **The case is a payment plus the invoice it is matched with:** `{"payment": {...}, "invoice": {...}}`. The agent proposes the match, and Cortex governs it.
- **`resolver.py`: bank payer names → customers.**
  - **Normalisation:**
    - NFKC folds half- and full-width characters;
    - small kana are made large (`ｼﾖｳｼﾞ` = ショウジ);
    - spaces and `・.-` are dropped;
    - the legal-form marker is removed wherever it is: before (`ｶ)`, `ﾄﾞ)`, `ﾕ)`), after (`(ｶ`, …) or spelled out (`ｶﾌﾞｼｷｶﾞｲｼｬ`, `ｺﾞｳﾄﾞｳｶﾞｲｼｬ`, …).
  - **Matching against each customer's registered bank names:**
    - same form → `high`/`exact`;
    - same core name in another legal-form spelling → `high`/`corporate_suffix_form`;
    - a truncation (at least 4 characters) that only one name starts with → `high`/`truncated_prefix`;
    - a truncation that several names start with → `ambiguous`/`prefix_matches_several`;
    - one edit away from exactly one name → `low`/`edit_distance_1`;
    - otherwise `none`.
  - **Only `high` names a customer.** For `ambiguous` and `low`, the candidates go in the evidence. Customer-specific rules never apply to such a payer, and it is routed to review.
- **`pack.py`:**
  - **entities:**
    - `payer:<sha256 prefix>`, an opaque key: the canonical key format does not allow kana, and a payer name can be a person's;
    - `customer:<id>`, only for `high`;
  - **CEL variables:**
    - `payment`: amount, `value_date`, bank, the resolved `customer_id` (`""` when not confidently known), and `garnishment_registered` from the customer master;
    - `invoice`: total, `issue_date`, customer;
  - **targets:** `match.evidence`, `match.mode`, `match.difference`, `match.difference.writeoff`, `match.allocation_order`;
  - **`correction_to_rule`:** a reviewer's correction on a CUST-0042 shortfall becomes the fee rule `payment.customer_id == "CUST-0042" && invoice.total > payment.amount && invoice.total - payment.amount <= 880` (`FEE_LIMIT`: the bank transfer fee is at most ¥880, and an overpayment is never a fee), with the treatment (支払手数料) as a requirement. A payer not resolved with `high` confidence never yields a learned rule;
  - **`check_output`:**
    - a review rule on `match.mode` (差押 garnishment, credit hold) forbids an auto-match;
    - the write-off prohibition forbids writing the difference off;
    - the evidence must name the payment and the invoice;
  - **`route(case, applied rules)`:** `auto` only when the payer is `high`, no rule asks for review, and any difference is explained by a rule that says how to treat it (the learned fee rule).
- **Golden-set harness (tests):** it gains `build_cases` (payment + invoice from separate files) and `extra_check` (per-case resolution and route). Nothing else changes, and custom_invoices still passes with it.
- **The pack commit changes no core code.** Outside `app/domain_packs/cash_application/` and `tests/`, nothing changed (`git status` at commit time). The verify fix below does change the core.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_golden_cash_application.py tests/integration/test_golden_custom_invoices.py"
seed: [('cash_application', 8, 0), ('custom_invoices', 7, 0)]
cash_application: {'packs': 40, 'extra checks': 40, 'verifications': 43, 'corrections': 3, 'outcomes': 1, 'isolation': 1} — 0 mismatch(es)
custom_invoices: {'packs': 40, 'verifications': 46, 'corrections': 3, 'outcomes': 1, 'isolation': 1} — 0 mismatch(es)
2 passed
```
- **Pack contract:** `test_the_pack_passes_the_contract`, over all 40 cases.
- **The three names resolve to CUST-0042 with `high`:**
  - `ｶ)ｶｸｳﾃﾞﾝｷ` (exact), `ｶｸｳﾃﾞﾝｷ(ｶ` (suffix form) and `ｶ)ｶｸｳﾃﾞﾝ` (truncated);
  - plus `ｶﾌﾞｼｷｶﾞｲｼｬｶｸｳﾃﾞﾝｷ`, full-width `カ）カクウデンキ`, and one with a space.
  - All 40 payers match the band and evidence that `expected.json` states by hand.
- **Ambiguous → `ambiguous`, routed to review:** for `ｶ)ｻﾝﾌﾟﾙ` the candidates are CUST-0017 and CUST-0105, no customer is named, and the route is `review`.
- **差押 always blocks:** every garnishment case holds `CA-G-GARNISH`, is routed to `review`, and `check_output` refuses an auto-match.
- **Golden set 100%**, including the per-payment resolution and route, scenarios 2, 3 and 4, and the learned fee rule (approved as default, authority 20, overriding `CA-G-EXACT-AMOUNT`).
- **Core unchanged in the pack commit.** The follow-up fix below changes the pack protocol.

**Do the tests catch mistakes?** I let the resolver pick the first candidate of an ambiguous truncation on purpose:
- 4 pack tests failed;
- the golden set reported `PAY-0117: rules ['CA-C-0017-HOLD', 'CA-G-EVIDENCE'] != expected ['CA-G-EVIDENCE']`, because the ambiguous payment got another customer's credit hold.

I restored the code afterwards.

Live, on the clean-clone dev stack (`make seed`: cash_application 8 rules, custom_invoices 7):
```
PAY-0108 garnishment  ｶ)ｻﾝﾌﾟﾙｹﾝｾﾂ → EVIDENCE + GARNISH constraints (= expected)
   verify auto-match + write-off → hold: "match.mode: this payment must be reviewed by a person, not auto-matched"
PAY-0113 learned_rule ｶ)ｶｸｳﾃﾞﾝｷ  → EVIDENCE, NO-WRITEOFF constraints; EXACT-AMOUNT applied; SMALL-WRITEOFF overridden (= expected)
   verify auto-match + write-off → hold: "match.difference.writeoff: the difference may not be written off"
PAY-0117 resolution   ｶ)ｻﾝﾌﾟﾙ    → EVIDENCE only (= expected)
   verify auto-match + write-off → pass   ← before the fix; now hold (see below)
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/026`, `make dev` + `make seed`)
```
$ make test             → 261 passed, 98 deselected
$ make test-integration → 98 passed, 261 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec (no API change)
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
```

## Gap found live, fixed in this PR (core change, approved 2026-11-22)
**Verify let an auto-match of an ambiguous payer pass.** The first live demo (PAY-0117) showed it:
- the pack routed the payment to review, but verify only asked `check_output(output, rules)`;
- no rule applies to an unknown payer except the evidence rule, so nothing forbade the auto-match.

**Fix** (commit `fix(verify): pass the case to DomainPack.check_output`):
- `DomainPack.check_output(output, rules, case)` now receives the case, which is already in the verify request and checked by hash. `verify.py` passes it on.
- `cash_application` refuses an auto-match whenever its `route` says review: an ambiguous, low or unknown payer, or a difference no rule explains.
- `custom_invoices` and the fake pack only take the new argument.

Tests:
- `test_an_auto_match_the_case_forbids_is_refused[…]` covers the 18 payments that go to review because of the payer or an unexplained difference;
- `test_the_ambiguous_payer_cannot_be_auto_matched_even_with_only_the_evidence_rule`;
- the cash golden test now also verifies PAY-0117 through the API: an auto-match is held, a review passes.

So **"core unchanged" no longer holds for this PR**: the pack protocol and `verify.py` changed, by your decision. The pack layer itself still names no workflow.

Live, on a second clean clone (`~/.cache/cortex-poc-checks/026b`):
```
PAY-0117 verify auto   → hold ['route: this match goes to review (ambiguous payer resolution or an unexplained difference), not auto']
PAY-0117 verify review → pass []
make test → 280 passed · make test-integration → 98 passed · make test-all → All test tiers passed · contract-check, lint, typecheck, migrate-check ok
```

## Notes
- **`check_output` judges rules by target,** because `RuleBody` carries no enforcement:
  - `match.mode` means review;
  - `match.difference.writeoff` means no write-off;
  - a fee treatment is recognised by its `requirements`.

  This holds for this pack's rules. A pack with an "allow" rule on a shared target would need an `effect` in the body.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
