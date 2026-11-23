# PR 047 — Loading customer rules from a spreadsheet, and bulk approval in the Desk

Branch: `poc/047-rule-load` → `main` · Issue: [docs/tasks.md §47](../tasks.md) · Depends on: 37, 40 (merged)

Slide 13 asks IBM for their "customer rules (Excel, or your new database), with a column guide", to "load your 192 rules into Arbitr". Nothing goes live until its owner approves, and nobody should have to approve 192 rules one screen at a time.

## What
- **Spreadsheet → rules** (`cash_allocation/sheet.py`, `make load-rules`):
  - It reads the CSV export of the sheet through a **column guide**: which column holds the customer, the exception type, each engine field, the guidance and the dates, and how their exception names map to ours (振込手数料 → bank_fee, …).
  - Each row becomes one rule in the two-layer format, and is **validated before anything is sent**. A row that can't be read is reported with its row number and why; nothing is guessed.
  - The valid rows go through `POST /v1/cortex/imports` on behalf of the person loading them, so the existing gate checks apply too: tighten-only, the import authority cap, and duplicates.
  - Loading twice is safe: rows still waiting for review answer `pending`.
  - A sample sheet and guide are in [`data/examples/rule-sheet/`](../../data/examples/rule-sheet/). The CSV uses IBM-like Japanese headers and has seven rows, one per exception type plus two bad rows.
- **Bulk approval in the Decision Desk:**
  - With Open status and more than one case, each case gets a checkbox, plus **Approve selected (N)**.
  - Each case is approved **as proposed**, with its own enforcement and authority (at least 20). To change enforcement, authority or dates, open the case.
  - It is **still one decision per case**: OPA decides each, each is recorded on its own, and a case the person may not decide is never sent. It is listed as "Not approved: <title> — <OPA's reason>".

## Acceptance criteria — output
```
$ make load-rules        (live stack)
{"row": 7, "status": "not_read", "detail": "Value error, engine.expected_fee: Input should be less than or equal to 880"}
{"row": 8, "status": "not_read", "detail": "customer 'CUST-0207' / exception '前受金' not recognised"}
{"row": 2, "status": "proposed", …}   {"row": 3, "status": "proposed", …}   {"row": 4, "status": "proposed", …}
{"row": 5, "status": "rejected", "problems": [{"code": "loosens_inherited_constraint", "detail": "mandatory … governs ['allocation.duplicate_payment']; a lower scope may only tighten it"}]}
{"row": 6, "status": "proposed", …}
$ make load-rules        (again)  → rows 2, 3, 4, 6: "pending" (nothing queued twice)
```
**Row 5 is a real finding.** "Refund duplicate payments" for one customer loosens the group's mandatory "hold duplicates", so the gate refuses it, as issue 31 intends. IBM's real sheet may contain exactly these conflicts, and the load report lists them for the owners.

**In the browser** (Japanese, cash application team, as OWN-001): 5 cases (the 4 loaded rules and the issue-46 suggestion), all selected → **"Approved 5 case(s)."**, and the queue is empty.
```
$ make test            → 321 passed (+1: the sample sheet → 5 items, rows 7 and 8 reported, every item admissible) · frontend 30 (+1 bulk)
$ make test-integration → 160 passed · make test-e2e-ui → 2 passed · lint, typecheck clean
```

## Notes
- **The real column guide comes from IBM's sheet.** Slide 13 already asks for it. Any column we can't map is reported row by row, so the first load also tells us what the format is missing.
- **192 rules at once** would arrive as up to 192 cases. If most of them are for customers that have their own owner (issue 45), each specialist sees only theirs.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
