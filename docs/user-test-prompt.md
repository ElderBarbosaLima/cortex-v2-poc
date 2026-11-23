# Prompt: test the Cortex PoC as a user

Paste the block below into a Claude Code session opened on `~/cortex-v2/cortex-v2-poc`. It is written to be repeatable after any change.

```text
Test the whole Cortex PoC as a user would, not through the unit tests. Use the built-in browser
for the UI and the public API (curl or a stdlib Python script) for the agent side. Synthetic data
only. Read the dev consumer keys from .env and never print them; never touch LITELLM_API_KEY.

Setup
1. `make stop && make dev && make seed` (fresh stack). Confirm http://127.0.0.1:5008/health.
2. Through the API, create pending work, the way an agent and reviewers would:
   - custom_invoices: request a context pack for the case of correction C-001
     (data/synthetic/custom_invoices), then submit C-001 as its reviewer (R-001) → a review case;
   - a person (R-002) proposes a local `default` rule that loosens the global prohibition
     CI-G-NO-REGNO-OMIT (target invoice.registration_number) → expect a loosens_inherited_constraint warning;
   - an import (POST /v1/cortex/imports, by R-001) of one rule with a valid target and a valid_to;
     also try one with an unknown target → expect it rejected with unknown_target;
   - cash_application: same as the first item, with its first correction.

Decision Desk (http://localhost:5001/cortex/decision-desk), in the browser
3. As the corrector (R-001): open the learned rule's case. Expect the separation-of-duties reason,
   and Approve and Reject disabled; clicking sends nothing.
4. As the owner (OWN-001): approve the learned rule as mandatory, authority 30. It leaves Open.
5. Open the loosening rule: the warning must be visible. Reject it with a reason.
6. As the backup (OWN-002): approve the imported rule.
7. Status = Decided shows all three, with their outcome. As X-001 (other tenant): access denied, no cases.
8. Repeat 3–4 on the cash application scope (R-101 corrects; OWN-001 approves).
9. Note anything a person could not understand or do: missing context (who proposed, source,
   validity), raw ids instead of names, stale messages, buttons that look enabled but are not, dead ends.

Agent side, through the API
10. The next applicable case (custom_invoices INV-0021, CUST-0042 on Coupa) carries the learned rule
    as a constraint; an unrelated case (INV-0022) does not. The imported rule applies where nothing overrides it.
11. Verify: a correct output passes; an empty output is held. Retire a rule (owner, POST .../retire),
    then verify the earlier pack again → held with rule_no_longer_approved.
12. IBM consumer (ibm-pr-agent): whoami shows only cortex:pack; a cash_application pack with purpose
    payment_reconciliation works; verify, corrections, imports and records answer 403.
13. GET /v1/cortex/records/expiring?scope=…&within_days=200 lists the imported rule with its valid_to.
14. `make e2e` on a fresh stack: 23/23 and 25/25. `make scenarios`: 6 passed. `make replicate` runs.

Report
- One table: step, expected, what happened, pass/fail.
- A separate list of usability findings, each with where it is and a suggested fix, ranked.
- Do not fix anything; ask me which findings to turn into issues.
```
