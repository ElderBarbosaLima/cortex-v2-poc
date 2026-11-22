# PR 020 — Access gate (2/2): ledger-revalidated search and stored context packs

Branch: `poc/020-context-pack` → `main` · Issue: [docs/tasks.md §20](../tasks.md) · Depends on: 19 (merged)

## What
- **`POST /v1/cortex/context-packs`** (`request_context_pack`, `x-mcp-tool`, 201) replaces the 501. The pipeline (`app/services/access_gate/context_pack.py`):
  1. resolve the domain pack: unknown → 422 `unknown_domain`;
  2. ask OPA for a read decision on the purpose and scope: deny → 403, OPA down → 503;
  3. get the case facts from the pack: a malformed case → 422 `invalid_case`;
  4. **rules** come from issue 19's applicability and resolution, **never from search**;
  5. **knowledge** (`fact`, `content`) comes from the `SearchProjection` as candidates only. Each hit is **revalidated against the ledger**:
     - the record exists in this tenant;
     - it is `approved` *now* (the projection may lag the outbox);
     - it is valid at `as_of`;
     - its scope is readable;
     - its sensitivity is within the ceiling.

     The pack carries the ledger's current version, not the version the projection saw. Dropped hits are logged with their reason;
  6. **store, then return.** The response is built from the stored rows, so `POST` and `GET` cannot differ.
- **Held, never served without a constraint** (409, carrying the `policy_revision`). This closes the open item from issue 19:
  - `unevaluable_constraint`: a mandatory rule or prohibition whose condition cannot be evaluated for this case (for example a field missing from the case);
  - `constraint_above_ceiling`: a constraint applies but is above the purpose's sensitivity ceiling. A default above the ceiling is simply left out.
- **Migration `006_served_packs`**, both tables append-only (trigger plus ORM guard), under RLS, with the app allowed SELECT/INSERT only:
  - `served_pack` keeps:
    - the pack ID (ULID);
    - the **trace ID** (the request's `X-Request-ID`);
    - the consumer, end user, purpose, scope and domain;
    - `as_of`;
    - **`policy_revision`**;
    - the **sha256 of the case**. The case itself is the client's data, not Cortex memory.
  - `served_pack_item` keeps the **`(record_id, version)`** pairs with their role and `overridden_by`, plus `body_hash` and a body snapshot **sealed under the record's own data key** (`key_ref`). Erasing a lineage (issue 24) therefore shreds every pack copy too.
- **`GET /v1/cortex/context-packs/{pack_id}`** (`get_context_pack`) is the replay:
  - it is served from storage, and nothing is re-run;
  - another tenant's pack is a 404, exactly like a missing one;
  - reading it is a read of the pack's scope for its purpose, decided by OPA at read time.
- **`ContextPack`** gains `scope`, `purpose` and `served_at`. Records are ordered constraints, then applied defaults, then overridden defaults, then candidates.
- **D12 guard kept.** `main.py` gets the adapter from `app.projections.default_projection()`, so nothing outside `projections/`, `workers/` and `cli/` names `PostgresSearchProjection`. The existing guard test caught this on the first try.
- **Seed: second-tenant consumer `other-agent`** in tenant `other`, with its dev key in `.env.example`. It is provisioned into the Secrets Manager hashes by the existing Terraform. Your local `.env` was not touched; add `"other-agent":"local-dev-other-key"` to `CORTEX_DEV_API_KEYS` there if you want to use it.
- **Fake pack:** it also resolves `W-<8 hex>` codes, which integration tests mint per test, so their records never meet.

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_context_pack.py"
PASSED test_a_record_retired_in_the_ledger_but_still_projected_stays_out   # (1) deprecated in the ledger, outbox not drained: search still returns it, the pack does not
PASSED test_the_pack_stores_the_policy_revision_and_record_versions         # (2) served_pack.policy_revision = OPA's; items (record_id, 1, constraint|candidate); trace_id
PASSED test_replay_returns_the_original_pack_after_the_rules_change         # (3) rule retired + new prohibition: GET == original POST (sorted-key JSON), fresh POST differs
PASSED test_another_tenants_key_cannot_read_the_pack                        # (4) other-agent → 404 {"error":"not_found",…}; own key → 200
PASSED test_a_constraint_that_cannot_be_evaluated_holds_the_pack            # 409 unevaluable_constraint, nothing stored
PASSED test_a_constraint_above_the_ceiling_holds_the_pack                   # 409 constraint_above_ceiling
PASSED test_knowledge_above_the_ceiling_or_in_a_sibling_scope_is_dropped
PASSED test_unknown_domain_and_bad_case_are_422
8 passed
```
**Does criterion 1 really catch mistakes?** I turned off the status check in `revalidate` on purpose, and the test failed:
```
FAILED test_a_record_retired_in_the_ledger_but_still_projected_stays_out
1 failed, 7 passed
```
I restored the code afterwards.

Live, on the clean-clone dev stack (no real domain pack is registered until issues 25–26):
```
POST /v1/cortex/context-packs {domain: custom_invoices, …} → {"error":"unknown_domain",…} [422]
GET  /v1/cortex/context-packs/<unknown>  (other-agent key) → {"error":"not_found","detail":"no such context pack"} [404]
GET  /v1/cortex/whoami                   (other-agent key) → {"consumer_id":"other-agent","tenant_id":"e94289dc-…"} [200]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/020`, `make dev` healthy)
```
$ make test             → 167 passed, 71 deselected
$ make test-integration → 71 passed, 167 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```
The contract changes are the new `get_context_pack` operation, and `request_context_pack` now answers 201 or 409 instead of 501. It is still marked `x-mcp-tool`.

## Notes
- **`overridden_by` may name a hidden default.** If a default above the ceiling wins over a visible one, the visible default is still marked overridden by the hidden one. Only the hidden rule's id is exposed, never its body. Resolving without hidden rules would instead show a weaker default as applied, which is worse.
- **Search runs only after rules resolve.** A held pack never touches search or storage.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
