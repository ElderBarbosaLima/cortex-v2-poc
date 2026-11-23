# PR 049 — Replay and the success measures from slide 26

Branch: `poc/049-replay-metrics` → `main` · Issue: [docs/tasks.md §49](../tasks.md) · Depends on: 42, 46 (merged)

Slide 26 proposes the PoC's success measures, "measured in replay and shadow". This PR builds the harness that produces them, so that when IBM's history arrives (CAT matches since April, with the open invoices of each day), it is one command.

## What
- **`make replay`** (`app/cli/replay.py`):
  - It reads resolved payments (each with what the specialist found) and asks Cortex for each one **as IBM's AI would**: `ibm-pr-agent`, purpose `payment_reconciliation`, as of the payment's date.
  - It scores the answers (`app/services/replay.py`, pure) against slide 26's targets, and prints JSON plus a Markdown table:
    - **Payments the rules would have covered**: a plain match, or an applied rule handles the exception the specialist found. This is the PoC's proxy for "cleared without a specialist" (74% today, target 85%);
    - **Matches with rule evidence**: of the payments a rule settled, how many cite it with ID, version and approver (target: every one);
    - **Agreement with the specialist** where a rule applied: do its engine fields match what they did? (Target 90%, the trust bar before clearing alone.)
  - It also lists the misses, each with why: no rule for that exception, or a rule whose fields differ.
- **`GET /v1/cortex/metrics/suggestions`** (`suggestion_metrics`): of the rules Cortex suggested (asserted by a model or a policy), how many were accepted, rejected or still pending, and the acceptance rate (target 70%).
- **A synthetic replay set**, `data/synthetic/cash_allocation/replay.jsonl`: 30 December payments mixing plain matches, the customers' own rules, the group rule (45), the mined suggestion (46), a rule loaded from the sheet (47), a gap no rule covers, and one where the rule says "hold" and the specialist booked a fee.

## Acceptance criteria — output
On the live stack, after the demo flow in PRs 046 and 047 (mined suggestion and sheet rules approved):
```
$ make replay
| Measure | Today | Target | Replay |
|---|---|---|---|
| Payments the rules would have covered | 74% (CAT today) | 85% | 93% |
| Matches with rule evidence (ID, version, approver) | not recorded | 100% | 100% |
| Agreement with the specialist, where a rule applied | not measured | 90% | 89% |
| Suggested rules owners accepted | none | 70% | 100% |
```
**These numbers are synthetic and prove only the harness.** The misses are the planted ones: the ¥1,100 tax gap no rule covers, and CUST-0104, where the rule says hold and the specialist booked the fee. That is exactly the kind of finding the replay exists for.
```
$ make test → 323 passed (+2: the measures from five planted payments; nothing to score is null, not 0)
$ make test-integration → 162 passed · lint, typecheck, contract-check clean
```

## Notes
- **Fixed while building it:** the first version counted "rule evidence" over every payment, so plain matches that need no rule pulled it down to 60%. Slide 26 means "every match a rule settled cites its rule", so it is now measured over those.
- **Shadow mode** is the same scoring on live payments: IBM's AI reports each outcome (issue 43), and the pack it was served is stored. Scoring a period of shadow is this harness over `/outcomes` instead of a file. It's a small step once there is live traffic.
- **IBM's real data** belongs in its own environment (synthetic only here, as the PoC brief requires). The harness reads a file, so it runs there unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
