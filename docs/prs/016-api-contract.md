# PR 016 — `/v1/cortex`: one generated contract, one error model, pagination

Branch: `poc/016-api-contract` → `main` · Issue: [docs/tasks.md §16](../tasks.md#16-api-v1cortex-esqueleto-erros-e-contrato-único) · Depends on: 013, 015 (merged)

## What
- **One spec, code-first (D15 / Q7).**
  - `scripts/export_openapi.py` writes `libraries/cortex-contract/openapi.json` from FastAPI, with sorted keys so it is stable.
  - `openapi-typescript@7.13.0` (in a pinned `node:22.12.0-slim` container) writes `libraries/cortex-contract/ts/cortex.d.ts` for the Decision Desk.
  - `make contract` regenerates both. **`make contract-check`** compares both with what the code produces and fails on any difference. A unit test does the same for the spec, so `make test` catches drift too.
  - This is the monorepo's precedent: `arbitr-sdk/scripts/generate_models.py` + `make docs-drift`.
- **`app/api/errors.py`: one error shape**, `{"error": "<code>", "detail": …, …extra}`, for every failure: `unauthorized` 401, `invalid_end_user` 400, `invalid_request` 422 (validation, as a list of `{loc, msg, type}`), `insufficient_scope` / `access_denied` 403 (with `policy_revision`), `not_found` 404, `not_implemented` 501, `access_gate_unavailable` 503. It is documented in the spec as `ApiError`.
- **`app/schemas/cortex.py`**: the wire schemas, UUID-typed ids (`format: uuid`).
- **Routes** (`app/routers/cortex.py`, same prefix and auth shape as integrations-service's `/v1`):
  - `GET /whoami`.
  - **`GET /records`** (`list_records`), paginated with an opaque **cursor** (the last record id, a ULID-UUID, never a numeric key). Filtered to the scopes OPA allows for the purpose (upward only) and to sensitivity ≤ the purpose's ceiling, under RLS.
  - `GET /records/{record_id}` (`get_record`).
  - **`POST /context-packs`** (`request_context_pack`), **`POST /verify`** (`verify_output`), **`POST /corrections`** (`submit_correction`), with their request and response schemas and an **`x-mcp-tool`** extension (name + description an MCP server will expose). They answer `501 not_implemented` until issues 20–22.
- **`app/db.py`: tenant sessions are lazy.** The tenant is set in `after_begin`, when a connection is first taken. A new test found that a request rejected for validation was already opening a database connection, because the session dependency ran `set_config` eagerly. Now a rejected request never touches the database. RLS behaviour is unchanged: all integration tests still pass.

## Acceptance criteria — output

**1. `make contract-check` fails if a route changes without regenerating the spec**
```
(add a field to ContextPackRequest, do not run make contract)
$ make contract-check
/libraries/cortex-contract/openapi.json is out of date with the code: run make contract
make: *** [contract-check] Error 1
$ make test
FAILED tests/test_api_contract.py::test_the_committed_spec_matches_the_code
(revert)
$ make contract-check
/libraries/cortex-contract/openapi.json matches the code
ts/cortex.d.ts matches the spec
```

**2. A test fixes the list of `x-mcp-tool` operations** — `test_the_mcp_tool_list_is_fixed`: exactly `request_context_pack`, `verify_output`, `submit_correction`, each tool name equal to its `operationId`.
```
GET  /health                          health
POST /v1/cortex/context-packs         request_context_pack   MCP
POST /v1/cortex/corrections           submit_correction      MCP
GET  /v1/cortex/records               list_records
GET  /v1/cortex/records/{record_id}   get_record
POST /v1/cortex/verify                verify_output          MCP
GET  /v1/cortex/whoami                whoami
```

**3. No numeric `id` / `*_id` field in the schemas**
- `test_no_numeric_identifier_crosses_the_api` scans every component property and every path/query parameter: no `id`, `*_id` or `cursor` is an integer or number.
- `test_record_ids_are_uuids_on_the_wire`: `record_id`, `pack_id`, `tenant_id` and `scope` all carry `format: uuid`.
- `version` and `authority` are integers but are described in the spec as "not a database key" / "authority ladder rung".

**Also**
- `test_every_error_has_the_same_shape[401 | 404 | 422 query | 422 body]`.
- `test_broad_operations_answer_501_until_implemented[…]`.
- Integration `test_listing_pages_through_readable_records_only`: own scope and ancestor records listed; a `restricted` record, another tenant's record and a sibling scope's record are not; ordered by id; `limit=1` + `cursor` returns the next one.
- Live (clean clone):
```
GET  /v1/cortex/records?purpose=invoice_drafting&scope=<q2c-invoicing>
     {"items":[],"next_cursor":null,"policy_revision":"sha256:26cd5b8aedad9d2b"}
POST /v1/cortex/verify → {"error":"not_implemented","detail":"arrives in issue 21 (docs/tasks.md)"} [501]
GET  /v1/cortex/whoami (no key) → {"error":"unauthorized","detail":"missing bearer API key"} [401]
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/016`)
```
$ make test             → 135 passed, 39 deselected
$ make test-integration → 39 passed, 135 deselected
$ make test-all         → All test tiers passed
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → exit 0
$ make typecheck        → 0 errors, 0 warnings, 0 informations
$ git status --short    → (empty)
```

## Notes
- The response shape of the 403/404/422 bodies changed (`{"detail": {...}}` → `{"error", "detail"}` at the top level). The one integration test that read `detail.detail` is updated.
- Defining the three broad operations' schemas now, before they work, keeps the contract stable for the Decision Desk and MCP. Issues 20–22 may refine them, and `make contract` makes such a change visible in review.
- Size: 418 lines in `app/` + `scripts/`, 198 in tests, plus 2,074 generated lines (spec + TS types, not hand-written).
