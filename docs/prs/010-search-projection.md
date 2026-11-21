# PR 010 — Search projection (`SearchProjection`), outbox drainer, `make reindex`

Branch: `poc/010-search-projection` → `main` · Issue: [docs/tasks.md §10](../tasks.md#10-projeção-de-busca-searchprojection--worker-do-outbox--make-reindex) · Depends on: 009 (merged)

## What
- **Migration `002_search_projection`**:
  - `tenant`: ids only, no tenant data, so no RLS. It lets the drainer iterate tenants.
  - `search_doc`: one row per record, the latest version the projector has seen.
    - RLS ENABLEd and FORCEd.
    - Indexes: `gin (search_text gin_bigm_ops)` for Japanese, a generated `tsvector` on the `simple` config, and `vector(8)` for fixed test vectors.
    - Mutable for `org_brain_app`, because a projection is rebuildable.
- **`app/projections/base.py`**: `SearchProjection` protocol (`apply`, `rebuild`, `search`), plus `SearchQuery`, `SearchFilters` (kinds, readable scopes, statuses, business time) and `Candidate` (record id, the version the projection saw, score).
- **`app/projections/postgres_search.py`** (`PostgresSearchProjection`):
  - **Score:** query terms found as substrings (`LIKE likequery`, pg_bigm index) + `bigm_similarity` + `ts_rank` + cosine closeness when a test vector is given. Ties are broken by `record_id`, so the order is deterministic.
  - **Candidate:** any term hits, or bigram similarity ≥ 0.3, or the tsquery matches.
  - **Upsert:** `WHERE search_doc.version <= EXCLUDED.version`, so a late or replayed event never moves a document backwards. `apply` reads the ledger, not the payload.
  - **`rebuild`:** deletes the tenant's documents and re-projects the latest version of each record.
- **`app/workers/outbox.py`**, in the shape of project-orchestrator's `outbox_registry` and drainer:
  - `register(event_type)`; an unregistered type fails loudly.
  - `drain(engine, tenant_id)`: `FOR UPDATE SKIP LOCKED`, `claimed_at`/`dispatched_at`, **one SAVEPOINT per row**. A raising handler rolls back only its own writes and records `attempt_count`/`last_error`; after `max_attempts` the row is `failed`; `SKIPPED` is supported.
  - `drain_all` iterates the `tenant` registry: the outbox is under RLS, so it is drained one tenant at a time, never with a bypass role.
- `app/workers/handlers.py` (`record.appended` → projection), `app/workers/projector.py` (loop or `--once`), service **`org-brain-projector`**, `app/cli/reindex.py` + **`make reindex`**.
- The test stack mounts `./data` read-only, so integration tests can load the synthetic rules.

## Acceptance criteria — output

**1. `備考` and `振込手数料` find the right records through pg_bigm** (`test_japanese_substrings_are_found_through_pg_bigm`)
- All 17 synthetic rules of both workflows, including the learned ones, are in the ledger and projected by draining the outbox.
- `備考` → `CI-C-0063-PAYTERMS` and `CI-L-0042-COUPA`, and no cash rule.
- `振込手数料` → `CA-L-0042-FEE` first.

**2. (D12 criterion a) Deleting `search_doc` + rebuilding → the same answers (ids, order, scores) for 20 fixed queries** (`test_rebuilding_the_projection_gives_identical_results`)
- Queries mix Japanese terms, codes (`D-77`, `customer:CUST-0042`), ASCII (`Coupa`, `Ariba`) and a two-term query. At least 18 of the 20 return hits before the delete.
- After `DELETE FROM search_doc`, the search is empty. After `rebuild`, the results dict compares equal to the one before.
- On the live stack: `make dev && make reindex` → `reindexed tenant …: 0 records` (the dev stack has no seed until issue 13).

**3. A raising handler → `failed`, `attempt_count` grows, other events go on** (`test_a_failing_handler_is_retried_then_failed_while_other_rows_go_on`)
```
drain 1: dispatched=1 retried=1 failed=0      ← the real record.appended row still projects
drain 2: dispatched=0 retried=0 failed=1
row: status=failed attempt_count=2 last_error="RuntimeError: projection target unavailable"
```

**4. Ledger and gates do not import `PostgresSearchProjection`** (`tests/test_projection_boundaries.py`)
- Scans `app/**` outside `projections/`, `workers/` and `cli/`: no import.
- A second handler for the same event type is refused.

**Also tested**
- `test_search_never_crosses_tenants`: the other tenant only sees `OT-CA-GARNISH`.
- `test_a_lagging_projection_still_shows_a_retired_record_until_drained`: after `CI-C-0017-DEPT` is retired in the ledger, search still returns it until the drain. This is exactly the lag that scenario 4 needs, and why the access gate must revalidate (issue 20).
- `test_fixed_test_vectors_rank_the_closer_document_first`.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/010`)
```
$ make test              → 63 passed, 28 deselected
$ make test-integration  → 28 passed, 63 deselected
$ make test-all          → … PASS: 4/4 … All test tiers passed
$ make lint              → exit 0
$ make typecheck         → 0 errors, 0 warnings, 0 informations
$ make dev               → Stack up (org-brain-projector running)
$ git status --short     → (empty)
```

## Found while testing
- Two of my test expectations were wrong, not the code:
  - the other tenant holds two rules (one per workflow), not one;
  - a `proposed` fact is correctly not returned under the default `statuses=("approved",)`.
- SQLAlchemy deprecates `.distinct(column)` for `DISTINCT ON`. `rebuild` now keeps the first (highest) version per record in Python, which is also portable to another index adapter.
- `# fmt: skip` stops Ruff from wrapping a line, so it caused line-length errors. Removed; Ruff formats those lines now.

## Notes
- Size: 516 lines in `app/` + `alembic/`, 301 in tests.
- Plain text is in `search_doc` (`search_text`). That is why erasure (issue 24) must delete projection rows as well as shred keys (Q10).
