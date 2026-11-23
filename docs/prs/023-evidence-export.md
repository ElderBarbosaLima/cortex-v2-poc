# PR 023 — Evidence: export a served pack to Object Lock S3

Branch: `poc/023-evidence-export` → `main` · Issue: [docs/tasks.md §23](../tasks.md) · Depends on: 22, 5 (merged)

## What
- **`POST /v1/cortex/context-packs/{id}/export`** (`export_context_pack`, 202) and **`GET …/export`** (`get_context_pack_export`).
  - The request writes an `evidence_export` row and an **outbox event** (`evidence.export_requested`) in one transaction. The worker (`org-brain-projector`) does the upload.
  - Asking again returns the same export, and one advisory lock per pack serialises concurrent requests.
  - A pack that does not exist, or belongs to another tenant, is 404. The read is decided by OPA, failing closed.
- **Manifest** (`app/services/evidence.py`, schema `cortex.evidence/1`). It is canonical JSON, so the same pack gives the same bytes. It holds:
  - the pack **exactly as the API serves it**, plus its `pack_sha256`. Rendering now lives in `context_pack.render`, so POST, GET and the manifest cannot drift apart;
  - for each `(record_id, version)` in the pack, that version's governance: status, `asserted_by`, `approved_by` (with the decision id), authority, enforcement, validity, `recorded_at`;
  - the policy revision, the case hash, and **every verification** of the pack so far.
- **Encrypted per tenant.** The worker gets a fresh data key from the tenant's KMS KEK (`GenerateDataKey`, with an encryption context of tenant, pack and purpose) and seals the manifest with AES-256-GCM, using that context as authenticated data. The object is a JSON envelope holding the wrapped key, nonce and ciphertext. Erasing the tenant's KEK (issue 24) makes every export unreadable.
- **Object Lock:**
  - the object key is deterministic: `tenants/<tenant>/context-packs/<pack>.json`;
  - `PutObject` sets **retention per object**: `ObjectLockMode` and `RetainUntilDate`, from config `EVIDENCE_RETENTION_MODE` (default COMPLIANCE) and `EVIDENCE_RETENTION_DAYS` (default 1 locally). Retention does not depend on the bucket's default;
  - before writing, the worker runs `HeadObject`. An object with the same `manifest-sha256` metadata is reused, so a retry never writes a second locked version. A different manifest under the same key is refused, never overwritten.
- **Migration `009_evidence_exports`**:
  - one row per pack (unique `pack_id`), status `pending | exported | failed`, with manifest sha256, version id, retention mode and retain-until date;
  - under RLS; the app may UPDATE only the result columns.
- **No emulator gap.** The probe passed Object Lock, so criterion 2 is a real test, not an `xfail` (Q11).

## Acceptance criteria — output
```
$ make test-integration ARGS="-rA tests/integration/test_evidence_export.py"
PASSED test_the_exported_object_opens_and_its_hash_matches_the_pack     # (1) pending → worker → exported COMPLIANCE; GetObject + KMS open;
                                                                        #     sha256(manifest) = export.manifest_sha256; manifest.pack_sha256 = sha256(GET pack);
                                                                        #     manifest.pack == GET == POST; (record_id, version) pairs; approved_by; verify "pass";
                                                                        #     ciphertext does not contain the rule text
PASSED test_deleting_the_version_under_retention_is_refused             # (2) DeleteObject(VersionId) → AccessDenied; retention COMPLIANCE; still 1 version
PASSED test_exporting_again_is_idempotent                               # (3) 3 requests → one export_id, one outbox event, one object version
PASSED test_a_retried_put_reuses_the_locked_object                      # (3) put again with the same manifest → reused, still 1 version
PASSED test_another_tenants_key_cannot_export_or_read_the_export        # 404 / 404
5 passed
```
**Does criterion 2 really catch mistakes?** I removed `ObjectLockMode`/`RetainUntilDate` from the `PutObject` on purpose. The deletion test failed: in the test bucket, which has no default retention, the delete then succeeded. The reuse test failed too, and showed a fragile spot: a `KeyError` on an object without a retention date. That path now uses `.get`. I restored the mutation afterwards.

Live, on the clean-clone dev stack:
```
org-brain-service: get_object_lock_configuration(cortex-poc-evidence) → Enabled {'Mode': 'COMPLIANCE', 'Days': 1}
org-brain-projector: archive() → COMPLIANCE 1
POST /v1/cortex/context-packs/<unknown>/export → {"error":"not_found","detail":"no such context pack"} [404]
```
A full export on the dev stack has to wait for a real domain pack (issues 25–26); the integration tests cover the full path.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/023`, `make dev` healthy)
```
$ make test             → 172 passed, 90 deselected
$ make test-integration → 90 passed, 172 deselected
$ make test-all         → All test tiers passed (Rego 38/38)
$ make contract-check   → openapi.json matches the code · ts/cortex.d.ts matches the spec
$ make lint             → All checks passed! · Regal: no violations
$ make typecheck        → 0 errors
$ make migrate-check    → ok (one head)
```
The contract gains `export_context_pack`, `get_context_pack_export` and `EvidenceExportView`.

## Notes
- **The manifest is a snapshot taken when the worker runs.** Verifications made after the export are not in it; the export is not re-made.
- **Not in this PR:** Merkle seals and watsonx.governance.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
