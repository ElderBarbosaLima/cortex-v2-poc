# PR 050 — The demo review: conflict warning, evidence and preview, cash cases, sign-in, Japanese UI, a product look (issues 50–56)

Branch: `poc/050-demo-feedback` → `main` · Issues: [docs/tasks.md §50–56](../tasks.md)

You listed nine points from the demo (2026-11-23). Here is what was found and done for each.

## 1. The conflict warning in the Japanese tenant (issue 50)
- **Cause:** the check worked, but **the Japanese set relied on `make seed` for its group rules** (登録番号). I had restarted the stack without the seed, so the ¥5,000 rule had nothing to conflict with. The English set brings its own rules, which is why only it warned.
- **Fix:**
  - The Japanese set now brings its group rules too, proposed and approved by the group's new rule managers (**GRP-001** / **GRP-002**, owners of the tenant root). Reviewers at 経理本部 can't write group-wide rules, which is correct.
  - `make seed` now skips a rule that is already approved under another ID, so loading the examples first doesn't duplicate anything.
  - People's proposals are now also checked against constraints at their own scope.
- **Fixed on the way:**
  - **A false positive.** A broad rule (all customers) was treated as loosening one customer's mandatory rule. A constraint now counts only if it is global, or about an entity the proposal names.
  - **The learning loop.** Counting own-scope constraints also blocked machine drafts against a rule just approved, so the loop keeps checking scopes above only.
- **Checked:** on a fresh stack **with no seed**, loading 日本語 gives the ¥5,000 rule **2 warnings**, with the group rules' text. In Japanese, the enforcement labels are translated too (必須 / 禁止 / 既定).
- **On "no required rule is ever missed":** that guarantee is at read time, and it doesn't depend on this warning. Mandatory rules come from SQL + CEL, never from search (scenario 3; golden sets). The warning only tells the owner at intake.

## 2. What changes on approval (issue 52)
- **`GET /review-cases/{id}/preview`** returns the origin case's pack **today** and **as if approved**, with the enforcement and authority chosen in the form. It uses the same applicability and resolution code as real packs, and writes nothing.
- The Desk shows **"Once approved, it applies to every case where: <condition> · <entities>"** and a **Preview the effect** button, with the two columns side by side.
- **The preview caught a real data bug.** The invoice examples used CUST-0300, which isn't in the invoice pack's customer master, so the learned rule would **never** have applied once approved. The examples now use CUST-0121, and the learned rules for invoices and cash application both show `applies: true`.

## 3. Japanese interface (issue 55)
- There is a complete `ja` locale: every label, message and hint.
- A **language switch** in the header (English / 日本語) is independent of the example set, and the browser remembers it.

## 4. Evidence (issue 52)
- Each decision now keeps **the corrected case**, sealed under that record's key, so erasure shreds it.
- Each proposal shows **Evidence**: who corrected which case and when, the reviewer's note, the change (field: before → after), or for payments the exception, the result and the invoices chosen. **Show the case** expands the invoice or payment itself.
- No document link yet: the PoC data has no source documents. The case snapshot is the closest thing.

## 5. Developer controls (issue 54)
- The screen shows **no** "Acting as", `X-End-User-Id`, `make seed` or "Load example cases" unless the **Developer tools** switch (wrench, in the header) is on. That is the presentation mode.

## 6. Cash application cases (issue 53)
Both sets now also load the cash application team:
- the **same bank-fee fix on 6 payments**, so IBM's "6 times" threshold produces a learned rule;
- a local rule that loosens the group's "hold duplicates" (1 warning);
- an import.

The English tenant gets its cash application scope.

## 7. Authority (issue 51)
The form opens with the **proposed** authority, and a hint explains the machine/import cap of 10 and the usual 20 (team) and 30 (group). Bulk approval also uses the proposed authority.

## 8. Sign-in (issue 54)
- A **Sign in** page: choose the company (架空グループ日本 / Fictitious Group UK), then who you are. People are listed by role.
- **No password, on purpose.** It's a PoC, and the page says that production uses the company's single sign-on.
- The Desk requires sign-in (redirect), and decides as the signed-in person. The header shows them and has **Sign out**.

## 9. A product look (issue 56)
- An app shell: a header with the brand, navigation, language, user and dev switch; a footer that says "synthetic data only".
- A landing page with three feature cards.
- In the Desk: a compact toolbar (scope, an Open/Decided segmented switch, refresh), success and error alerts, an empty state, badges in the preview, and evidence in cards.

## Acceptance criteria — output
```
make test             → 323 passed · frontend 35 (+4: dev tools hidden, evidence + preview, Japanese, sign-in)
make test-integration → 164 passed (+2: evidence; preview — the rule joins as mandatory and the old default loses)
make test-policies 43/43 · make scenarios 7 passed · contract-check, migrate-check, lint (exit 0), typecheck clean
make e2e (fresh stack) → 23/23 · 25/25 · make test-e2e-ui → 2 passed (signs in through storage, dev tools on)
Load examples on a fresh stack, no seed: 日本語 and English → every step "created";
  ¥5,000 rule: 2 warnings (both languages) · cash: 1 warning · learned rules for invoices and cash: preview applies
```
**Browser** (Japanese, signed in as OWN-001, dev tools off): the sign-in page; the Desk with no dev controls; the two warnings with the group rules' text; the evidence with the note and the change; the preview showing the learned rule joining as "適用 · この提案".

## Notes
- **The contract changes are additions only:** `preview_approval`, `CaseProposal.evidence`, `CaseEvidence`, `ApprovalPreview`.
- **The decision now stores the case.** It's sealed, and erasure shreds it, but it is personal-data-bearing in production, so retention belongs with WS6 and Legal.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
