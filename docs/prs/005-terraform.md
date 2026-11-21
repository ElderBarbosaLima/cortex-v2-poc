# PR 005 — Idempotent provisioning against the AWS Emulator with Terraform in a container

Branch: `poc/005-terraform` → `main` · Issue: [docs/tasks.md §5](../tasks.md#5-provisionamento-idempotente-no-emulador-com-terraform-em-container) · Depends on: 003 (merged)

## What
- `infra/terraform/aws/`:
  - Terraform `~> 1.17.0`, providers `hashicorp/aws` **6.73.0** and `hashicorp/local` 2.6.1 (exact versions).
  - `.terraform.lock.hcl` with hashes for `linux_arm64` and `linux_amd64`.
  - Local backend in `/state`.
- Resources (12):
  - `cortex-poc-evidence`: versioned, Object Lock enabled with default **COMPLIANCE** retention (`evidence_retention_days`, default 1).
  - `cortex-poc-originals`: versioned.
  - Both buckets: public access blocked and SSE `AES256`.
  - Secret `cortex-poc/consumer-api-keys`: `{"<consumer>": {"sha256": "…"}}`. The input is `CORTEX_DEV_API_KEYS` from the git-ignored `.env`; only hashes are stored.
- `iam.tf`: org-brain's least-privilege policy as an `aws_iam_policy_document`, rendered to **`infra/iam/org-brain-service.json`** (versioned, reviewable). No IAM call is made to the emulator.
- Provider configured with `skip_credentials_validation`, `skip_requesting_account_id`, `skip_metadata_api_check`, `s3_use_path_style`. Endpoint, CA and credentials come **only from env** (`x-aws-client-env`).
- `docker-compose.yml`: `terraform` (shares the emulator's network namespace; state volume `terraform-state`; external plugin cache `cortex-poc-terraform-plugins`) and `terraform-lock`.
- `make provision` (and `make dev` runs it), `make tf-plan` (`-detailed-exitcode`), `make tf-lock`; `make lint`/`make format` now include `terraform fmt`.
- README "Provisioning" section; `docs/discovery.md` §6.9 records the Terraform pattern.

## Decisions worth a look
- **KMS stays out of Terraform:** one key per tenant, created by the app at onboarding (issue 14).
- **The rendered IAM policy is stable.** The secret's ARN gets a random suffix on every creation, so the policy uses AWS's documented `<name>-??????` pattern. Otherwise `infra/iam/org-brain-service.json` would change on every fresh stack.
- **KMS permissions** are scoped to keys behind `alias/cortex/tenant/*` (`kms:ResourceAliases`). An explicit **Deny** applies to `GenerateDataKey`/`Decrypt` without an `EncryptionContext` `tenant_id`, which matches the probe result that context binding works. `kms:CreateKey` must be `Resource: "*"`; the comment says how real AWS would narrow it.
- **State dies with the emulator** (`make stop` removes both). If only the emulator restarts, the refresh sees the resources gone and `apply` recreates them (tested below).
- **Provider cache in an external volume.** It is a download cache, not state, so `make stop` keeps it and `make dev` does not download ~100 MB each time.
- Raw API keys still pass through Terraform state, marked sensitive. That state lives on an ephemeral local volume; on real AWS, secrets would come from the platform's secret store, not from TF variables.

## Acceptance criteria — output
Clean clone at `~/cortex-v2/.clean-check-005`.

**1. `make provision` twice → the second plan says `No changes.`**
```
$ make dev
Apply complete! Resources: 12 added, 0 changed, 0 destroyed.
Stack up — org-brain-service: http://127.0.0.1:5008/health
$ make provision
No changes. Your infrastructure matches the configuration.
Apply complete! Resources: 0 added, 0 changed, 0 destroyed.
$ make tf-plan
No changes. Your infrastructure matches the configuration.
$ make tf-plan >/dev/null; echo "tf-plan exit=$?"
tf-plan exit=0
```

**2. Versioning `Enabled`; Object Lock as the probe allowed** (read back with boto3 through the `aws-probe` container)
```
cortex-poc-evidence  versioning: Enabled | object lock: {'ObjectLockEnabled': 'Enabled', 'Rule': {'DefaultRetention': {'Mode': 'COMPLIANCE', 'Days': 1}}} | public access blocked: True | SSE: AES256
cortex-poc-originals versioning: Enabled | object lock: ObjectLockConfigurationNotFoundError | public access blocked: True | SSE: AES256
secret keys: ['askq2c-agent', 'decision-desk'] | shapes: {'askq2c-agent': ['sha256'], 'decision-desk': ['sha256']}
raw key present: False
```

**3. `infra/iam/*.json` versioned and reviewable; no IAM call to the emulator**
[`infra/iam/org-brain-service.json`](../../infra/iam/org-brain-service.json), statements:
`EvidenceArchiveWriteRead` (Put/Get object + retention on `cortex-poc-evidence/*`, no delete) · `OriginalsReadWrite` · `CreateTenantKeys` · `AliasTenantKeys` · `UseTenantKeys` (condition `kms:ResourceAliases` like `alias/cortex/tenant/*`) · `RequireTenantEncryptionContext` (Deny if `kms:EncryptionContext:tenant_id` is null) · `ReadConsumerKeyHashes` (`…:secret:cortex-poc/consumer-api-keys-??????`).
The only resources are S3, Secrets Manager and `local_file`; the policy is a data source.
```
(restart the emulator, re-provision)
IAM JSON stable across stack recreation          ← diff against the previous render is empty
```

**4. No 501 from the provider:** the first `apply` created all 12 resources and the refresh calls (the whole `GetBucket*` family the probe covered) answered. No AWS CLI fallback is needed.

**Self-heal when only the emulator restarts**
```
$ docker compose restart aws-emulator && make tf-plan
  # aws_s3_bucket.evidence has been deleted
  # aws_s3_bucket.originals has been deleted
  # aws_secretsmanager_secret.consumer_api_keys has been deleted
Plan: 12 to add, 0 to change, 1 to destroy.
$ make provision
Apply complete! Resources: 12 added, 0 changed, 1 destroyed.     ← the 1 is local_file, rewritten with identical content
$ make tf-plan → exit 0
```

**Definition of done**
```
$ make ps   → aws-emulator, opa, org-brain-service, postgres: all (healthy)
$ make lint → exit 0 (ruff, terraform fmt -check, opa check --strict, regal)
$ make test-all → … PASS: 4/4 … All test tiers passed
$ make typecheck → 0 errors, 0 warnings, 0 informations
$ git status --short → (empty: the re-rendered IAM JSON matches the committed one)
```
`hashicorp/terraform:1.17.2` is multi-arch (`linux/amd64` + `linux/arm64`, checked with `imagetools inspect`), and the lock file carries both platforms.

## Found while testing
- The first render embedded the secret's full ARN, with its random suffix, so the versioned policy would have changed on every stack. Fixed with the `-??????` pattern.
- `terraform fmt` flagged alignment in `iam.tf`. `make format` fixes it, and `make lint` now checks it.
