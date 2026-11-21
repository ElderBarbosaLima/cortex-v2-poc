# PR 008 — Ledger: schema, migrations, RLS and append-only

Branch: `poc/008-ledger` → `main` · Issue: [docs/tasks.md §8](../tasks.md#8-ledger-schema-migrações-rls-e-append-only) · Depends on: 002 (merged)

## What
- **Migration `001_ledger`**, run as `org_brain_owner`:
  - **`memory_record`**: one row per immutable version, `(record_id, version)` unique, bigint identity `id` (never leaves the service).
    - It carries the 19 envelope fields. `body` is stored as `body_sealed` + `body_codec` + `body_hash`, and `sensitivity` as `sensitivity_class` + JSON.
    - Checks: closed vocabularies (kind, status, enforcement, assurance level, sensitivity class), authority 0–40, a rule must have an enforcement, `approved` must have an `approved_by`, `valid_to > valid_from`.
    - Indexes: (tenant, record, version desc), (tenant, kind, enforcement, scope) for the applicability path, GIN on `entities`.
  - **`memory_record_current`**: `DISTINCT ON (record_id) … ORDER BY version DESC`, `security_invoker = true`, so RLS applies through it.
  - **`outbox`**, in the shape of project-orchestrator's `Outbox`.
  - **Row-level security**, ENABLEd **and FORCEd** on `memory_record` and `outbox`. The policy is `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` for both USING and WITH CHECK, so an unset tenant reads nothing and writes nothing.
  - **Append-only**: a `BEFORE UPDATE OR DELETE` row trigger plus a `BEFORE TRUNCATE` statement trigger. A trigger rather than REVOKE, because the owner cannot be revoked from (the monorepo's `014_journal_hardening` reasoning).
  - **Grants**: `org_brain_app` gets SELECT/INSERT on `memory_record`, SELECT on the view, and SELECT/INSERT/UPDATE on `outbox` (whose status moves).
- **App code:**
  - `app/models/`: SQLAlchemy 2 typed models with SQLite variants (monorepo pattern), and the ORM append-only guard `immutable.py` (blocks unit-of-work and bulk paths).
  - `app/utils/ids.py`: ULID record ids written as UUIDs.
  - `app/db.py`: `tenant_session(tenant_id)` = one transaction with `set_config('app.tenant_id', …, true)`.
  - `OrgBrainConfig.app_database_url` / `owner_database_url`.
- **Alembic**: `alembic.ini` (`prepend_sys_path`, `path_separator = os`), `env.py` (owner URL from config), template with the ≤ 32-character reminder.
- **Compose and make**:
  - One-shot `org-brain-migrate`: `org-brain-service` now starts only after it completes (init-container pattern).
  - `make migrate`.
  - `make migrate-check` (`scripts/check_migrations.py`, Alembic's own script loader), also run inside `make lint`. Ruff now covers `alembic/`.

## Decisions worth a look
- **`body_codec` per row.** Issue 9 writes records before issue 14 brings keys. Rows written now say `plain-json/1`; issue 14 adds `aes-gcm/1` with a DEK per record, without a schema change. A reader always knows how to open a row.
- **Policy expression:** `NULLIF(…, '')` covers both "never set" and "set to empty".
- **FORCE RLS** makes the owner tenant-blind too. Migrations run DDL only, so nothing in a migration needs to read rows.
- **Outbox under RLS.** The projection worker (issue 10) will therefore drain per tenant, never with a bypass role. I'll settle the details there.

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/008`.

**1. UPDATE/DELETE fail with the trigger's message; the ORM guard blocks unit-of-work and bulk**
- Integration `test_the_owner_cannot_change_history[UPDATE|DELETE|TRUNCATE]`: as the owner, with the tenant set so rows are visible, each statement raises `memory_record is append-only; append a new version instead`.
- Integration `test_the_app_role_has_no_update_or_delete_grant[UPDATE|DELETE]`: `permission denied`.
- Unit `tests/models/test_append_only_guard.py`: mutate-then-flush, `session.delete`, bulk `update()` and bulk `delete()` each raise `LedgerImmutableError`; inserting a new version works.

**2. As `org_brain_app`: no `app.tenant_id` → 0 rows; tenant A → only A, even with raw SQL**
- `test_without_a_tenant_every_table_reads_empty`: 0 rows for the app role on both tables, and 0 for the owner too (FORCE).
- `test_a_tenant_reads_only_its_own_rows_even_with_raw_sql`: `SELECT DISTINCT tenant_id` → only A, and `WHERE tenant_id = B` → 0.
- `test_writing_into_another_tenant_is_refused`: `new row violates row-level security policy`.
- `test_current_view_shows_the_latest_version_under_rls`: version 2 (`deprecated`), and B's record is invisible.

From the live stack:
```
    relname    | rls | forced          table_name            | app_privileges
---------------+-----+--------        -----------------------+----------------------
 memory_record | t   | t               memory_record         | INSERT,SELECT
 outbox        | t   | t               memory_record_current | SELECT
                                       outbox                | INSERT,SELECT,UPDATE
 tgname: memory_record_no_mutation, memory_record_no_truncate      alembic_version: 001_ledger
```

**3. `make migrate-check` fails on two heads or an id over 32 characters** (fake migration directories)
- Unit tests `test_two_heads_are_reported`, `test_a_revision_id_over_32_chars_is_reported`, and `test_the_real_migrations_pass_the_check`.
- Output: `migrate-check: ok (one head, every revision id <= 32 chars)`.

**4. Record ids are valid UUIDs and grow with time** — `test_record_ids_are_uuids`, `test_record_ids_sort_by_creation_time_across_milliseconds`, `test_record_ids_do_not_collide` (10,000 ids).

**Also:** `make dev` migrates before the service starts (`Running upgrade -> 001_ledger`), `/health` answers, and a second `make migrate` runs nothing. The database also rejects authority 50 (`memory_record_authority_check`) and a rule without enforcement (`ck_rule_has_enforcement`).

**Definition of done**
```
$ make test              → 47 passed, 17 deselected
$ make test-integration  → 17 passed, 47 deselected
$ make test-all          → … PASS: 4/4 … All test tiers passed
$ make lint              → exit 0 (includes migrate-check)
$ make typecheck         → 0 errors, 0 warnings, 0 informations (strict on app/)
$ git status --short     → (empty)
```

## Found while testing
- `make dev` first failed: the `alembic` CLI could not import `app` (the integration tests had passed only because they call Alembic from inside pytest). Fixed with `prepend_sys_path = .`. The Alembic deprecation that came with it is fixed with `path_separator = os`.
- Pyright strict: `@contextmanager` returning `Iterator` is deprecated in the current typeshed → `Generator[Session]`. A module-level "constant" flag was redefined → the bulk listener is now registered once at import.

## Size
About 590 lines in `app/`, `alembic/` and `scripts/` (the migration alone is ~150 lines of DDL), plus 320 of tests, against the ~400 target. The ledger, RLS and trigger only make sense tested together, so I kept them in one PR.
