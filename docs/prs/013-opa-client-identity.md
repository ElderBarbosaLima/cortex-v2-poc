# PR 013 — OPA client (fail closed), tenant data in OPA, consumer identity

Branch: `poc/013-opa-client-identity` → `main` · Issue: [docs/tasks.md §13](../tasks.md#13-cliente-opa-carga-dos-dados-do-tenant-e-identidade-do-consumidor) · Depends on: 005, 008, 012 (merged)

## What
- **`app/clients/opa.py`**: `OpaClient` (httpx, 0.5 s timeout) with `read_decision`, `approval_decision`, `put_tenant`.
  - Connection errors, timeouts, HTTP ≥ 400, non-JSON, a missing `result`, or a result without a boolean `allow` all raise **`AccessGateUnavailable`**, which the app turns into **`503 {"error": "access_gate_unavailable"}`**. Nothing is read or decided.
  - A denial is a normal answer; routes return 403 with OPA's reason and `policy_revision`.
- **`app/seed/tenants.py`**: `data/synthetic/tenant.json` → one OPA document per tenant, in the arbitr-rbac access-context shape:
  - UUID node ids with a materialised `path_up` (as `org_nodes.path`);
  - grants for people (their role at their node) and for consumers (`cortex_consumer` at the tenant node);
  - the `memory.*` subset of the UAT v1 role catalog;
  - consumers with their purposes.
- **`app/services/policy_gate.py`**: `PolicyGate` loads every tenant into OPA. OPA keeps pushed data in memory, so after an OPA restart it answers `unknown tenant`; only then does the gate re-push that tenant and ask once more.
- **`app/identity.py`**, in the shape of integrations-service's `AuthedCaller` / `require_scope`:
  - `Authorization: Bearer <key>` → sha256 → constant-time match against the hashes loaded from **Secrets Manager** (`cortex-poc/consumer-api-keys`, provisioned in issue 5) → consumer → tenant.
  - `X-End-User-Id` must be a UUID. **The tenant never comes from the request.**
  - `caller_session` = one RLS-bound session per request (`SET LOCAL app.tenant_id`).
- **Routes** (`app/routers/cortex.py`):
  - `GET /v1/cortex/whoami`.
  - `GET /v1/cortex/records/{id}?purpose=`: under RLS, then the OPA read decision at the record's scope, then the purpose's sensitivity ceiling. Another tenant's record is a 404, the same as a missing one (no cross-tenant existence oracle).
- **Startup** (`create_app` lifespan): load the seed, read the key hashes from Secrets Manager, register tenants in the `tenant` table (idempotent), push tenants to OPA. OPA down at startup is logged, not fatal: reads fail closed and the gate reloads later. Tests inject `AppServices`.
- **`make seed`** (`app/cli/seed.py`).
- **Compose:**
  - `org-brain-service` now joins the **emulator's network namespace** (AWS client on `127.0.0.1`), mounts the emulator's CA/credentials read-only and `./data` read-only, and waits for the emulator (`restart: true`), OPA and the migration. Its port 5008 is published by `aws-emulator`.
  - `make dev` brings up the emulator, Postgres and OPA, provisions (so the secret exists), then starts the rest.
  - The integration test stack gains `opa-bundle` + `opa`.
- Seed: consumers gained `scopes` (`tenant.json` regenerated; workflow data unchanged).

## Acceptance criteria — output

**1. Unit (`httpx.MockTransport`): OPA 500, timeout and a response without `result` → explicit 503, never a read** (`tests/clients/test_opa_client.py`)
- `test_anything_but_a_clear_decision_fails_closed[http-500 | no-result | result-without-allow | allow-not-bool | not-json]`.
- `test_connection_errors_and_timeouts_fail_closed[ConnectError | ReadTimeout]`.
- Plus `test_the_gate_reloads_an_unknown_tenant_once_then_asks_again` (exact call sequence POST → PUT → POST) and `…does_not_reload_for_a_tenant_it_does_not_hold`.

**2. With OPA stopped (`docker compose stop opa`) every read → 503; when it is back, it works** (live stack)
```
--- OPA up:
{"record_id":"01a1fe63-…","status":"approved","policy_revision":"sha256:26cd5b8aedad9d2b"} [200]
--- OPA stopped:
{"error":"access_gate_unavailable","detail":"policy engine unavailable (ConnectError)"} [503]
--- OPA back (its pushed data was in memory, gone):
{"record_id":"01a1fe63-…","status":"approved","policy_revision":"sha256:26cd5b8aedad9d2b"} [200]
log: policy_gate.reloading_tenant
```
Integration also covers this: `test_an_unreachable_opa_is_a_503_never_a_read` and `test_after_opa_forgets_the_tenant_the_gate_reloads_it`.

**3. One tenant's API key never reads the other tenant, even when the other tenant is named in the query or body**
- Integration `test_another_tenants_record_is_a_404_even_when_the_request_names_that_tenant`: jp's key on other's record (with `?tenant_id=<other>`) → 404; other's key on jp's record → 404; other's key on its own record → 200.
- Unit `test_whoami_takes_the_tenant_from_identity_never_from_the_request`: `?tenant_id=` and `X-Tenant-Id:` are ignored.

**4. The decision carries a `policy_revision` equal to the active bundle's**
- Integration `test_an_allowed_read_carries_the_active_bundle_revision`: equal to `POST /v1/data/cortex/policy/revision`.
- Live: both are `sha256:26cd5b8aedad9d2b`.

**Also:** no key → 401, `Basic` → 401, a malformed `X-End-User-Id` → 400, a purpose that is not registered → 403, a `restricted` record under a `confidential` ceiling → 403. The key store loads from Secrets Manager (botocore `Stubber`). The seed builds one document per tenant, with no person from the other tenant.

```
$ make seed
tenant jp (442450b1-…): 5 nodes, 8 grants → OPA
tenant other (e94289dc-…): 3 nodes, 1 grants → OPA
consumers: askq2c-agent, decision-desk
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/013`)
```
$ make dev              → Stack up; whoami answers with the consumer's tenant
$ make test             → 105 passed, 33 deselected
$ make test-integration → 33 passed, 105 deselected
$ make test-all         → … PASS: 33/33 … All test tiers passed
$ make lint             → exit 0
$ make typecheck        → 0 errors, 0 warnings, 0 informations
$ git status --short    → (empty)
```

## Found while testing
- **`make dev` failed: `ModuleNotFoundError: httpx` in the runtime image.** httpx was a dev-only package (the monorepo lists it there, for TestClient); now the service needs it at runtime. Moved to `packages`. Unit tests could not catch this, because they run in the dev image; the live stack did.
- **OPA restart loses the pushed data**, which would turn into "unknown tenant" denials after OPA comes back. That is why the gate reloads.
- Typeshed now marks `@asynccontextmanager` with `AsyncIterator` as deprecated → `AsyncGenerator[None]`. boto3 has no stubs; it sits behind one `Any`.
- httpx logged every OPA call at INFO; its logger is now WARNING (the client logs decisions itself).

## Size
About 600 new lines in `app/` and 440 in tests. The `app/` diff also shows ~195 lines of pure reformatting in the two synthetic generators: I removed every `# fmt: skip` so Ruff can wrap long lines, and the generated data is byte-identical.
