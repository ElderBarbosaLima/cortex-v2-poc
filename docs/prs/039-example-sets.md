# PR 039 — Test in Japanese and in English: example sets you switch in the Decision Desk

Branch: `poc/039-example-sets` → `main` · Issue: [docs/tasks.md §39](../tasks.md) · Depends on: 28, 30, 38 (merged)

You asked (2026-11-23) for a way to test the app in Japanese and in English, with a control on screen to change which examples are used.

## What
- **Two example sets, one per tenant**, so switching never mixes data:
  - `ja`: the PoC's Japanese tenant `jp`. Its rules come from `make seed`.
  - `en`: a new English example tenant `en`, "Fictitious Group UK (example)". It has the scope `en/finance/q2c-invoicing`, the people EN-R-001, EN-R-002, EN-OWN-001 and EN-OWN-002 (owner and backup at the tenant root), and its own agent (`examples-agent-en`) and Desk consumer (`decision-desk-en`).
- **[`data/examples/ja.json` and `en.json`](../../data/examples/)** hold the same story in each language: invoice EX-INV-001 and its reviewer's correction, a local rule that would loosen the group's registration-number prohibition, and an import valid until 2027-01-31. The English set also has its three group rules; it starts empty, so they are imported and approved by its owner through the review queue.
- **On screen** (Decision Desk toolbar):
  - **Examples** selector, `日本語 (Japanese)` / `English`. Switching changes the tenant, the people, the scope and the Desk key. The choice is remembered in the browser.
  - **Load example cases** creates the set's cases through the public API and lists each step: Created, Already there or Failed.
  - The person list shows the set's own people first. X-001 (the other tenant) stays last, to show isolation.
- **Keys, server-side only.** The dev proxy chooses the set's Desk key from an `X-Cortex-Examples` header. Only `ja` and `en` are accepted, and the header is never forwarded. The loader (`server/utils/examples.ts`, `POST /api/dev/examples/load`) uses each set's agent and Desk keys from runtime config. New keys: `CORTEX_EXAMPLE_API_KEYS`, `CORTEX_DESK_API_KEY_EN` and `CORTEX_EXAMPLES_AGENT_KEY_EN`. Terraform merges `example_api_keys` into the consumer-key secret (hashes only), and `make init-env` adds the variables to an existing `.env`. Your `.env` already has them.
- **Loading twice changes nothing.** Two API changes make that hold:
  - `POST /imports` answers `pending`, with the waiting record and its review case, when the same item from the same importer already waits for a decision.
  - `POST /records` returns the waiting proposal (`already_pending: true`) when the same proposer sends the same rule, validity and `supersedes`.

  A different proposer is new evidence and still gets their own proposal (`test_review_queue` covers this). The loader also treats a correction already submitted by an earlier load (409 on its idempotency key) as "already there".
- **Drafts follow the note's language.** The `custom_invoices` pack's own draft is written in English when the reviewer's note is English. The model is now told to write in the baseline draft's language.
  - Before this fix, Sonnet 5.5 answered an English note in Japanese.
  - Live after the fix (`make test-llm`, new test): *"For invoices to Sample Logistics (CUST-0300) sent through Coupa, the PO number must be included in the remarks, otherwise the invoice is sent back."*
- **Contract changes are additions only:** `ImportItemResult.status` gains `pending`, and `ProposalAccepted` gains `already_pending`.
- **Not changed:** the UI language stays English (P7). Rule text stays in the `text_ja` field in both sets; it holds the set's language (see the README deviations).

## Acceptance criteria — output
Fresh stack (`make stop && make dev && make seed`), then **Load example cases** for each set, twice:
```
load 1  ja ['created']        en ['created']   (en: 3 group rules approved by EN-OWN-001, then the 3 cases)
load 2  ja ['already_there']  en ['already_there']
```
**In the browser, English set, as EN-OWN-001:**
- The learned rule: "For invoices to Sample Logistics (CUST-0300) sent through Coupa, put the PO number in the remarks (e.g. "PO number: PO-55120"), because invoices without it are sent back."
- The loosening rule shows both warnings, with the group rules' English text ("Always show the supplier's qualified invoice registration number…" and "Never issue an invoice without the registration number."), plus "Proposed directly by Invoice reviewer 2".
- Every case has 1 proposal.

**The Japanese set** gives the same three cases, in Japanese.

**Checks:**
```
$ make test             → backend 290 passed · frontend 29 passed (loader 4, selector and load button 2)
$ make test-integration → 144 passed (+ re-import is pending; the same proposal twice is one)
$ make test-llm         → 3 passed (+ an English correction gives an English rule)
$ make e2e              → 23/23 · 25/25          $ make test-e2e-ui → 2 passed
$ make lint · make typecheck · make contract-check → clean
```

## Notes
- **`make test-e2e-ui` rejects every open case on the stack**: it resets the stack before it runs. After running it, press Load example cases again; the cases come back.
- **A new tenant or new keys need `make dev`** (re-provision and restart). Your stack is fresh, with both sets loaded.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
