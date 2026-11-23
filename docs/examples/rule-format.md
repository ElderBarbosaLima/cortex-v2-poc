# One rule, one shared format: the example for slide 15

For the Workshop 1 deck (slide 15, "Placeholder for the Arbitr dev team"). The format runs in the PoC as the `cash_allocation` pack (issue 40). The customer is fictitious.

## The Excel row
> **Yamada Shoji · bank fee**: "Usually ¥660 short, sometimes more. Check tax if bigger. Ask Tanaka."

## The same rule in Arbitr: two layers, one copy
```json
{
  "exception": "bank_fee",
  "engine": { "expected_fee": 660, "max_fee": 880, "treatment": "fee_expense" },
  "condition": "payment.customer_id == \"CUST-0101\" && payment.gap > 0 && payment.gap <= 880",
  "text_ja": "山田商事は振込手数料を差し引いて支払う。通常は 660 円不足。880 円を超える不足は手数料ではない可能性があるため、消費税の差額を確認し、不明なら担当者へ回す。",
  "entities": ["customer:CUST-0101"],
  "owner": "Yamada Shoji's cash specialist (backup: …)",
  "version": 3, "valid_from": "2026-04-01", "approved_by": "…"
}
```

| Layer | Who reads it | What it holds |
|---|---|---|
| **Engine fields** | IBM's rule engine | Exact, typed values: expected fee ¥660, accept up to ¥880, book it as a fee expense. A value out of range is refused when the rule is saved. |
| **Condition** | Both | When the rule applies: this customer, paid short by ¥1–880. |
| **Guidance** (`text_ja`) | IBM's LLM | Plain words for what the engine fields can't hold: "sometimes more", "check tax if bigger", "if unclear, send it to a person". |
| **Envelope** | Audit trail, owners | Owner and backup, version, effective date, approver. These are the rule ID, version and approver that IBM's audit trail cites. |

**"Ask Tanaka" does not go into the rule.** A person's name becomes the rule's **owner**: they are the one who approves changes and gets asked. The rule text says "send it to a person", and the owner is whoever that person is today.

## The engine fields for each exception type (slide 16)
| Exception | Engine fields |
|---|---|
| Combined payment | `pick`: oldest first, PO number, billing period, or the remittance list; optional `max_invoices` |
| Bank fee deducted | `expected_fee`, `max_fee` (≤ ¥880), `treatment`: fee expense, or hold for review |
| Consumption tax difference | `rounding`: floor, round half up, or ceil; `tolerance` in ¥; `treatment`: write off, or hold |
| Duplicate payment | `action`: hold, refund, or apply to the next invoice |
| Partial payment | `action`: leave the balance open, or instalments, with the count |
| Guidance only | no engine fields: e.g. "send payments from this group to review" |

The final fields come from IBM's feedback at the workshop, and from their 192 rules once we have them (issue 47 maps their columns).
