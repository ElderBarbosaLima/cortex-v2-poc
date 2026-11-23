# Example sets (issue 39)

The same review story in two languages, for testing the Decision Desk by hand. Pick one in the
Desk's **Examples** selector and press **Load example cases**:

| Set | Tenant | What loading does |
|---|---|---|
| `ja.json` | `jp` (the PoC's Japanese tenant) | Adds the work to review on top of `make seed`'s rules |
| `en.json` | `en` (Fictitious Group UK, example) | Proposes and approves its three group rules through the review queue, then adds the work |

The work in each set:
1. A context pack for invoice EX-INV-001, then the reviewer's correction: a learned rule for the owner to decide.
2. A local rule that would loosen the group's registration-number prohibition: the owner sees the warning.
3. An import from a legacy rule list, valid until 2027-01-31: it shows under "Expiring soon" once approved.

Loading is idempotent: a correction replays by its idempotency key, an import of the same rule
is reported as `duplicate`, and the same proposal joins its open case. Everything is fictitious.
Rule text lives in the `text_ja` field in both sets: the field keeps its name and holds the text
in the set's language.
