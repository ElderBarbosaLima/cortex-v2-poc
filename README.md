# Cortex v2 PoC

Proof of concept of the Cortex v2 core (governed organisational memory, arbitr), stage 1 of the
Memory Architecture Rev. B: *an approved reviewer correction improves the next applicable case,
stays out of unrelated work and remains attributable.* Pilot slice: IBM Japan AskQ2C (custom
invoices and cash application), with synthetic Japanese data only.

- What to build: [`docs/architecture/`](docs/architecture/) (Rev. B, Rev. C, pilot scope, high-level proposal).
- Decisions and findings: [`docs/discovery.md`](docs/discovery.md). Plan: [`docs/tasks.md`](docs/tasks.md).
- Backend name is `org-brain` (service, routes, symbols); the product and UI name is **Cortex**.

> Status: issues 1–8 of 29 (skeleton, Postgres, AWS Emulator, OPA, Terraform, synthetic data for both workflows, ledger schema). Most of what the docs describe is not built yet.

## Prerequisites

Only Docker. On a Mac with Apple Silicon the team uses Colima (open source; see `docs/tasks.md`, issue 0):

```bash
brew install colima docker docker-compose docker-buildx
colima start --vm-type vz --arch aarch64 --cpu 4 --memory 8 --disk 60 --mount-type virtiofs
```

Add `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` to `~/.docker/config.json`
so `docker compose` and `docker buildx` are found.

Keep the clone under your home directory: Colima only shares `$HOME` with its VM, so a clone in
`/tmp` builds fine but the test containers see an empty bind mount ("no tests ran").

## Run

```bash
make dev                              # build + start, wait until healthy, provision AWS resources (Terraform)
curl -s 127.0.0.1:5008/health         # {"status":"ok","service":"org-brain-service","version":"0.1.0"}
make stop                             # stops everything and deletes volumes: the stack is ephemeral
```

`make help` lists every target. Never call `docker compose`, `pipenv` or `pytest` directly.

## Checks (all in containers)

| Target | What it runs |
|---|---|
| `make test` | hermetic unit tests (no network, no stack) — see [ADR 0001](docs/adr/0001-test-tiers.md) |
| `make test-integration` | integration tests on an ephemeral stack (`-p cortex-poc-test`, no host ports, wiped after) |
| `make test-policies` | `opa test` on `policies/` |
| `make test-all` | `test` + `test-integration` + `test-policies`: the definition of "tests pass" |
| `make lint` / `make format` | Ruff check + format check, `opa check --strict` + Regal / Ruff format + fix |
| `make typecheck` | Pyright (strict on `app/`) |
| `make build-multiarch` | builds the images for `linux/arm64` and `linux/amd64` |
| `make probe-emulator` | AWS Emulator fidelity probe → [`docs/emulator-probe-1.0.545.md`](docs/emulator-probe-1.0.545.md); exits non-zero if a required operation is missing |
| `make provision` / `make tf-plan` | Terraform apply / plan against the emulator (`make dev` runs `provision`; plan exits 0 when nothing changes) |
| `make tf-lock` | refresh provider hashes for `linux_arm64` + `linux_amd64` |
| `make opa-reload` | rebuild the policy bundle and restart OPA after editing `policies/` |
| `make gen-data` | regenerate `data/synthetic/` (deterministic; a rerun leaves `git diff` empty) |
| `make migrate` / `make migrate-check` | Alembic upgrade as the table owner (`make dev` runs it before the service) / one head, ids ≤ 32 chars (also in `make lint`) |
| `make lock SVC=<service>` | regenerates `Pipfile.lock` with the pinned pipenv |

## Services

| Service | What | Host port |
|---|---|---|
| `aws-emulator` | AWS Emulator 1.0.545 (S3, KMS, Secrets Manager), built from the signed RPM (`infra/aws-emulator/`) | `127.0.0.1:9297` (HTTPS) |
| `opa` | Open Policy Agent 1.22.4, serving the `policies/` bundle (built by the one-shot `opa-bundle`) | `127.0.0.1:8181` (`CORTEX_OPA_PORT`) |
| `postgres` | PostgreSQL 16.16 + pgvector 0.8.7 + pg_bigm 1.2 (`infra/postgres/`) | `127.0.0.1:5432` (`CORTEX_POSTGRES_PORT`) |
| `org-brain-service` | Cortex backend (FastAPI) | `127.0.0.1:5008` |

The ledger (`memory_record`, one immutable row per version; `memory_record_current` view; `outbox`) is
append-only: a trigger refuses UPDATE, DELETE and TRUNCATE even for the owner, and row-level security
(ENABLEd and FORCEd) keeps every tenant's rows invisible to the others.

Postgres roles: `org_brain_owner` owns the database and runs migrations; `org_brain_app` is what the
service uses, owns nothing and cannot bypass row-level security. Their local passwords come from
`.env` (see `.env.example`); `make init-env` adds keys a new issue introduces without touching yours.

### OPA

Authorisation decisions (who may read, propose or approve what) live in Rego under `policies/`, never
in Python (D22). `opa-bundle` builds them into a bundle whose revision is a content hash; the policy
exposes it as `data.cortex.policy.revision` so every context pack can record which policies it was
authorised under. Tenant trees and grants are pushed through OPA's Data API under `cortex/tenants`.

```bash
curl -s -X POST 127.0.0.1:8181/v1/data/cortex/policy/revision -d '{"input":{}}'   # {"result":"sha256:…"}
```

### AWS Emulator

AWS services (S3, KMS, Secrets Manager) come from the [AWS Emulator](https://github.com/aws/aws-emulator),
standing in for IBM COS, Key Protect and Secrets Manager (Rev. C). The application uses the normal
AWS SDK, configured only by environment variables (`x-aws-client-env` in `docker-compose.yml`).

- Its certificate is valid for `localhost`/`127.0.0.1` only, so AWS clients join the emulator's
  network namespace (`network_mode: service:aws-emulator`) and call `https://127.0.0.1:9297` with
  TLS verification on.
- It keeps all state in memory and regenerates its CA and credentials on every start. Clients read
  them from a shared read-only volume; nothing is copied into `.env` or code. Restarting the
  emulator loses every bucket, secret and key: that is why the whole stack is ephemeral.
- Telemetry is off (`--telemetry off`).
- It does not enforce IAM: "works locally" is not proof of a permission.

### Provisioning (Terraform)

`infra/terraform/aws/` creates the AWS side (Rev. C: cloud differences live only in this module):
`cortex-poc-evidence` (versioned, Object Lock COMPLIANCE), `cortex-poc-originals` (versioned), both
private with SSE, and the `cortex-poc/consumer-api-keys` secret (sha256 of each consumer's API key
from `CORTEX_DEV_API_KEYS` in `.env`, never the key itself). KMS keys are per tenant and created by
the application, not here. The least-privilege IAM policy for org-brain is rendered to
[`infra/iam/org-brain-service.json`](infra/iam/org-brain-service.json) as a reviewable artefact; it
is not applied anywhere. Terraform runs in a container, shares the emulator's network namespace,
and keeps its state on a volume that `make stop` removes with the emulator.

## Synthetic data

Everything in [`data/synthetic/`](data/synthetic/) is fictitious, in Japanese, and generated with a fixed
seed (`make gen-data`; generators live in each domain pack). `tenant.json` is the shared scope tree,
people (role labels only) and consumers. Per workflow:

| File | What |
|---|---|
| `cases.jsonl` | input cases: 40 invoices (custom invoices) or 40 bank payments (cash application), the brief's formats, JPY as integers |
| `invoices.json` | cash application only: the open invoice each payment is matched against |
| `customers.json` | fictitious customers (cash application: registered bank names in half-width kana, garnishment flag) |
| `rules.json` | initial rules with their CEL condition, scope, entities and enforcement |
| `corrections.jsonl` | reviewer corrections and the rule each should become |
| `timeline.json` | the ordered steps a harness replays (process, correct, approve, retire) |
| `expected.json` | golden set: the exact rules each case's pack must hold, overridden local rules, verify outcomes; cash application adds the expected payer resolution (entity, confidence band, evidence) and route (auto/review) |

Covered: a happy path, a correction that becomes an approved rule and then applies, unrelated cases
where it must not appear, and acceptance scenarios 2, 3 and 4. The unit tests fail if the committed
data drifts from the generator.

## Layout

Mirrors the arbitr monorepo so the code can move there later:

```
services/org-brain-service/   FastAPI service (app/, tests/, Pipfile, Dockerfile)
data/synthetic/               generated synthetic data + golden sets (see above)
policies/                     Rego policies (OPA bundle) + tests
libraries/                    shared libraries (arbitr-test-utils, copied from the monorepo)
docs/                         architecture, discovery, tasks, ADRs, PR descriptions (docs/prs/)
```

## Secrets

Nothing secret is committed. `.env` is git-ignored; only `.env.example` is versioned.
