# PR 032 — Regions: ADR 0002 (option b, proposed) and a replication prototype; batch clean-clone run for 31–37

Branch: `poc/032-regions` → `main` · Issue: [docs/tasks.md §32](../tasks.md) · Depends on: the decision (WS6 + Legal/IBM). Following your instruction to take the most likely path, the PoC prototypes **option (b)**.

## What
- **[`docs/adr/0002-regions.md`](../adr/0002-regions.md)** compares three options on residency, latency, erasure, lineage and cost:
  - (a) one deployment in Japan;
  - (b) a deployment per region, plus a published Global scope;
  - (c) federation at read time.

  It proposes (b), with status **Proposed**: WS6, Legal and IBM still decide. It also lists every simplification the prototype makes.
- **`app/services/replication.py`:**
  - It copies a home region's **approved, non-personal** records in the published Global scope (the tenant root), where non-personal means `personal_data` is false and the class is `public` or `internal`.
  - The copy keeps the `record_id` and is re-sealed under the destination's own data key (`ledger.append_new(key_ref=…)`), with `asserted_by {type: replication, id: region:hq, origin}` and `source.replica_of`.
  - It carries stale, deprecated, superseded and erased across. An erasure in the home region crypto-shreds the replica and everything derived from it.
  - It is idempotent, and it never re-exports a replica.
- **A replica is read-only.** `POST /records/{id}/retire` on one answers `409 replica_read_only`. This is the only contract change: the 409 description.
- **Two ledgers in one Postgres.** `infra/postgres/initdb` also creates `org_brain_hq` (same roles, RLS and extensions).
  - `make migrate-hq` migrates it.
  - `make replicate` runs the job (`app/cli/replicate.py`, a report per tenant, ids and counts only).

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_regions.py"
PASSED test_a_global_rule_approved_in_hq_reaches_a_japan_pack_as_a_read_only_replica   # constraint in the pack; own key; retire → 409
PASSED test_replication_is_idempotent
PASSED test_retired_in_hq_verify_holds_the_japan_pack_once_replicated                  # not before the job runs (the lag), held after
PASSED test_personal_proposed_and_team_records_never_leave_hq
PASSED test_a_supersede_in_hq_carries_across
PASSED test_an_erasure_in_hq_shreds_the_replica_in_japan                               # record_key gone in Japan too
6 passed
```
1. ADR: written and proposed. It is **not approved**, because that is a decision for WS6, Legal and IBM.
2. A Global rule approved in HQ shows up in the Japan pack. Retired in HQ, it makes verify hold the Japan pack. A personal record does not replicate.

## Batch clean-clone run (issues 31, 33, 34, 35, 36, 37 and 32)
Clean clone of `poc/032-regions` at `~/.cache/cortex-poc-checks/032`, with `cp .env.example .env` (so no LiteLLM key). Every target exited 0:
```
$ make dev            → stack up
$ make e2e            → 23/23 checks passed (custom_invoices) · 25/25 checks passed (cash_application, with the 2 Phase 1 checks)
$ make replicate ×2   → migrates org_brain_hq; no tenant there yet, so nothing is published (the tests cover the job)
$ make scenarios      → the six Rev. B scenarios, named 1–6: 6 passed
$ make test-all       → unit 290 + frontend 18 · integration 142 passed · Rego 39/39 · All test tiers passed
$ make lint           → All checks passed! · Regal: no violations · check-v2-styles: clean
$ make typecheck      → 0 errors
$ make contract-check → ts/cortex.d.ts matches the spec
$ make migrate-check  → one head, every revision id <= 32 chars
$ make test-e2e-ui    → 2 passed
```
After the run, `make replicate` on an HQ ledger with no tenants now prints a line that says so, instead of printing nothing.

## Notes
- **A new database needs a fresh Postgres volume.** `initdb` runs only on first boot, so an existing dev stack needs `make stop && make dev` before `make replicate`.
- **Before production**, the open points from the ADR:
  - a cross-region channel instead of a shared Postgres;
  - one KMS per region;
  - an explicit `global` scope or a published flag with a named Global owner;
  - whether the destination trusts the home region's approvers;
  - a scheduled job with a lag metric and an alert.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
