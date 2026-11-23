# PR 038 — Decision Desk: what the user test found

Branch: `poc/038-desk-usability` → `main` · Issue: [docs/tasks.md §38](../tasks.md) · Depends on: 28, 31, 33 (merged)

You asked to fix the seven usability findings from the user test (2026-11-23). Everything in that test worked; these are the things a person could not understand or do. The test prompt is versioned with this PR: [`docs/user-test-prompt.md`](../user-test-prompt.md).

## What
| # | Finding | Fix |
|---|---|---|
| 1 | The Desk did not show validity or let the owner set it | A "Valid" row (JST business dates; "always" for learned rules that have no start, "no end date"). The form gains `Valid from` and `Valid until`, prefilled with what was proposed. **Only a change is sent**, so approving never moves dates by accident |
| 2 | "Came from" read "suggestion from cases —, corrected by —" | In words, by who asserted it: the model and its cases and correctors; the pack's rule; "Proposed directly by <person>"; "Imported from <system> by <person>", plus the authority asked for and the cap of 10; "Replicated from <region>" |
| 3 | The loosening warning named the inherited rule by a raw id | `CaseProposal.inherited_conflicts` gives each rule's enforcement, text and targets. They are resolved **when the case is read**: the text is opened from the sealed body and never copied into `source`, so erasure still removes it (an erased rule shows no text). New proposals also store the constraint's `record_id` in the problem; older ones are read from the detail text |
| 4 | `/cortex` was a dead end | An "Open the Decision Desk" link |
| 5 | Disabled Approve and Reject looked enabled | `disabled:opacity-40`, `cursor-not-allowed` |
| 6 | "Approved." stayed after switching person | Changing person, scope or status clears the notice, the problem and the selection |
| 7 | Expiring rules were only in the API | An "Expiring soon" panel (90 days) under the case list. `ExpiringRecord.title` gives each rule's text, read from the sealed body |

The contract changes are additions only: `InheritedConflict`, `CaseProposal.inherited_conflicts` and `ExpiringRecord.title`.

## Acceptance criteria — output
**In the browser** (dev stack, fresh cases created through the API):
- `/cortex` → "Open the Decision Desk" → the Desk.
- The loosening rule (R-002) shows two warnings:
  - 「適格請求書発行事業者の登録番号（T＋13桁）を必ず記載する。」 (inherited mandatory rule);
  - 「登録番号を記載しない請求書は発行しない。」 (inherited prohibition);
  - plus "Valid 2026-11-01 → no end date" and "Proposed directly by 請求書レビュアー 2".
- The import shows "Imported from legacy-rule-list by 請求書レビュアー 1 · Asked for authority 20; an import is capped at 10 until you decide", and "Valid 2026-11-01 → 2027-01-15".
- OWN-001 changed the end date to 2027-01-31 and approved. "Expiring soon" then listed 「備考に担当部署を記載する（全顧客）。」 until 2027-01-31.
- Switching to R-002 cleared "Approved.". R-002 sees Approve and Reject faded, with the separation-of-duties reason.

**Checks:**
```
$ make test             → backend 290 passed · frontend 23 passed (5 new desk tests, 1 home)
$ make test-integration → 142 passed (tighten-only: conflicts resolved with their text, no text in source; expiry: title)
$ make test-e2e-ui      → 2 passed
$ make lint · make typecheck · make contract-check · make migrate-check → clean
```

## Not done
- **The other tenant's agent key.** Your `.env` predates `other-agent`, and `make init-env` adds missing variables, not consumers inside `CORTEX_DEV_API_KEYS`. Tenant isolation through the Desk (X-001) still works. If you want that key back, `make stop`, delete `.env`, then `make dev`; you would then need to put your LiteLLM key and switches back in.
- Date inputs show in the browser's locale format. The labels say JST.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
