# PR 014 — Keys: a KMS KEK per tenant, a data key per record lineage

Branch: `poc/014-tenant-keys` → `main` · Issue: [docs/tasks.md §14](../tasks.md#14-chaves-kek-por-tenant-kms--dek-por-registro) · Depends on: 005, 009 (merged)

## What
- **`app/keys/kms.py`**:
  - **`KeyService`** protocol (Rev. C: "behind one key interface") and its KMS implementation, **`KmsKeyService`**.
    - `ensure_tenant_key`: `DescribeKey(alias)`; if not found, `CreateKey` (no tags) + `CreateAlias alias/cortex/tenant/<id>`.
    - `data_key(session, tenant, key_ref, create)`: `record_key` lookup → `Decrypt`, or `GenerateDataKey(AES_256)` + insert. **`EncryptionContext = {tenant_id, key_ref}`**, matching the IAM policy's Deny without `tenant_id` from issue 5. The plaintext DEK is cached in memory only; `forget()` drops it.
  - **`AesGcmCodec`** (`aes-gcm/1`): AES-256-GCM, 96-bit random nonce, **associated data = tenant + lineage root**. A body moved to another tenant or record, or tampered with, fails authentication. With its key deleted, it raises `KeyErasedError`.
- **Migration `003_record_keys`**:
  - `memory_record.key_ref`: the lineage root, set on every new row.
  - `record_key` (tenant, key_ref unique, wrapped DEK, KEK alias): RLS ENABLEd/FORCEd; the app may SELECT/INSERT/**DELETE**, never UPDATE.
  - The `memory_record_current` view is rebuilt to expose the new column.
- **Ledger:**
  - `append_new` computes `key_ref`: the record's own id, or its first `derived_from` parent's `key_ref`, so a whole lineage shares one key. `transition` carries it to new versions.
  - `open_body(session, row)` opens with the codec the row names.
- **`app/services/body_codec.py`**: the codec protocol now takes the session and `key_ref`. A **codec registry** maps a row's `body_codec` to its codec; `install_codec(…, default=True)` sets the codec for new bodies.
- **`app.keys.install()`** builds the KMS client from the standard SDK variables, ensures each tenant's KEK and makes `aes-gcm/1` the default. It is called by the service (startup), the **projector** (it projects sealed bodies, so it now shares the emulator's network namespace), `make seed` and `make reindex`.
- **Integration test stack** gains the AWS Emulator; `org-brain-it` shares its network namespace with the CA and credentials mounted read-only.

## Acceptance criteria — output

**1. The body in the database does not contain the plaintext (substring search fails); the app reads it back** (`test_the_body_is_never_stored_in_clear_and_the_app_reads_it_back`, real KMS)
- `body_codec = aes-gcm/1`.
- The UTF-8 bytes of `架空電機の請求書には検収書を添付する` are not in `body_sealed`; `position(:p IN body_sealed)` finds 0 rows tenant-wide.
- After `forget()`, so through KMS `Decrypt` and not the cache, `open_body` returns the original body.

Live stack:
```
ledger: aes-gcm/1 | plaintext in body_sealed: False | record_key rows: 1
projection (written by the projector, another process): 振込手数料は支払手数料に計上する
```

**2. Two records of one tenant have different DEKs; a record with `derived_from` uses the root's DEK** (`test_records_get_distinct_keys_and_derived_records_use_the_roots`, real KMS)
- Two `record_key` rows, for `a` and `b`; none for the child; the wrapped and plaintext DEKs differ; `child.key_ref == a.record_id`.
- KMS refuses to unwrap `a`'s DEK with `b`'s context (`InvalidCiphertextException`).
- Unit, with a fake KMS: a grandchild shares the root's key too, a new version keeps `key_ref`, a tampered or cross-tenant body fails authentication, and a deleted key → `KeyErasedError`.

**3. `record_key` is the only data table `org_brain_app` may DELETE from** (`test_record_key_is_the_only_ledger_table_the_app_may_delete_from`)
- DELETE grants for `org_brain_app` = `{record_key, search_doc}`. `search_doc` is the rebuildable projection (issue 10), not data, so among data tables `record_key` is the only one.
- No UPDATE on `record_key` or `memory_record`.

**4. No `if emulator` in the code; only env** (`test_no_application_code_knows_about_the_emulator`): no `app/**/*.py` mentions `9297`, `endpoint_url`, `aws-emulator` or `127.0.0.1`. boto3 picks everything up from the standard SDK variables.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/014`)
```
$ make dev              → Stack up (service and projector install the KMS codec)
$ make test             → 111 passed, 38 deselected
$ make test-integration → 38 passed, 111 deselected   (now with the emulator in the test stack)
$ make test-all         → … PASS: 33/33 … All test tiers passed
$ make lint             → exit 0   ·   make migrate-check → ok
$ make typecheck        → 0 errors, 0 warnings, 0 informations
$ git status --short    → (empty)
```

## Decisions worth a look
- **Lineage root = the first `derived_from` parent's root.** It covers what the PoC derives: a learned rule derives from the correction's decision record. A record with several parents that must vanish when *any* of them is erased would need several keys; I left that out of scope and noted it for issue 24.
- **Governance metadata stays in clear** (status, scope, validity, entities, enforcement), as agreed in Q10. The applicability SQL needs it, and the admission check keeps `entities` opaque.
- **The projection holds plain text** (`search_doc.search_text`), so erasure (issue 24) must also delete projection rows. Unchanged from Q10.
- **SQLite-portable insert** for `record_key` (`add` + `flush`, no `ON CONFLICT`): two processes creating the same root's key at once is not a PoC path. Noted as a known limit.

## Found while testing
- My first draft had an `import json` inside a function (house rule: imports at the top). Moved.
- Integration-test fixes: the fixture must install the codec the way the service does (`install_codec`), and raw SQL needed `uuid[]` casts and a bytea `position()` search.

## Size
343 lines in `app/` + `alembic/`, 346 in tests.
