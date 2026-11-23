# PR 045 — Owners per customer, and customer groups

Branch: `poc/045-customer-owners` → `main` · Issue: [docs/tasks.md §45](../tasks.md) · Depends on: 18, 41 (merged)

Slide 18 asks for two things:
- **The owner is the customer's cash specialist (to confirm), with a backup.** Today's approval goes to a named owner per *scope* (a team).
- **Customer groups:** a parent pays for its subsidiaries, so its rules must reach them.

## What
- **Owners per customer:**
  - The seed gets `customer_owners`: an entity (`customer:<id>`), an owner and a backup, per tenant. There are two fictitious cash specialists, CS-101 and CS-102.
  - In the OPA data they become `entity_owners`. The policy sees the record's entities.
  - **A record that names a customer with its own owner is decided by that owner or backup, not by the scope's owner.** The scope owner is refused with "only the named owner or backup of this customer can decide".
  - A record that names several owned customers needs someone who owns or backs up all of them.
  - A customer with no owner of its own falls back to the scope's owner, as before.
  - Separation of duties is unchanged.
- **The Desk shows the right person:** the case detail's `named_owners` and `can_decide` use the same lookup as the policy. Retire passes the record's entities too.
- **Customer groups** (`cash_allocation`):
  - A subsidiary (CUST-0106 → parent CUST-0104) also resolves to its group's entity, and CEL gets `payment.group_id`.
  - A group rule (`payment.group_id == "CUST-0104"`) applies to the parent and to every subsidiary. The group's owner decides on it.
  - Past cases (issue 42) are shared across the group, because they match on any shared entity.
  - The golden set gains PAY-A011: the subsidiary's ¥440 shortfall gets the group's "hold for review" rule.

## Acceptance criteria — output
```
$ make test-policies → PASS: 43/43   (+4: the customer's owner and backup decide; the scope's owner doesn't;
                                        a customer without an owner falls back; two owned customers need both)
$ make test-integration ARGS="-rA tests/integration/test_customer_owners.py"
PASSED test_the_customers_cash_specialist_decides_not_the_scopes_owner
       # named_owners = CS-101 / CS-102; OWN-001 → 403 "only the named owner or backup of this customer can decide"; CS-101 → 200
PASSED test_a_customer_with_no_owner_of_its_own_is_decided_by_the_scopes_owner
$ make test-integration → 158 passed   (golden cash_allocation: 11/11, PAY-A011 included)
$ make test → 320 passed
```
`make lint`, `make typecheck` and `make contract-check` are clean. The contract does not change.

## Notes
- **Where the data comes from:** in the PoC, the owners and the groups are synthetic seed data. For IBM they come from the workshop: who owns each of the 164 customers, their backups, and the parent–subsidiary links (slide 13: "how CAT links payer names to customers, and parent–subsidiary links").
- **The case list** still shows the scope's owner. Only the detail, where the decision is made, uses the customer's.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
