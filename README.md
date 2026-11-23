# Cortex v2 PoC

Proof of concept of the Cortex v2 core (governed organisational memory, arbitr), stage 1 of the
Memory Architecture Rev. B: *an approved reviewer correction improves the next applicable case,
stays out of unrelated work and remains attributable.* Pilot slice: IBM Japan AskQ2C (custom
invoices and cash application), with synthetic Japanese data only.

- What to build: [`docs/architecture/`](docs/architecture/) (Rev. B, Rev. C, pilot scope, high-level proposal).
- Decisions and findings: [`docs/discovery.md`](docs/discovery.md). Plan: [`docs/tasks.md`](docs/tasks.md).
- Backend name is `org-brain` (service, routes, symbols); the product and UI name is **Cortex**.

> Status: issues 1–16 of 29 (skeleton, Postgres, AWS Emulator, OPA, Terraform, synthetic data, ledger, search projection, CEL conditions, Rego policies, identity and the OPA client, per-record keys, domain pack interface, API contract). Most of what the docs describe is not built yet.

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

### Calling the API

Consumers authenticate with a static API key per consumer (PoC stand-in for OIDC); the tenant always
comes from that identity, never from the request. Local keys are in `CORTEX_DEV_API_KEYS` (`.env`);
Secrets Manager holds only their sha256.

```bash
curl -s 127.0.0.1:5008/v1/cortex/whoami -H "Authorization: Bearer local-dev-askq2c-key"
curl -s "127.0.0.1:5008/v1/cortex/records/<record_id>?purpose=invoice_drafting" \
     -H "Authorization: Bearer local-dev-askq2c-key" -H "X-End-User-Id: <person uuid>"
```

The contract is generated from the code into [`libraries/cortex-contract/`](libraries/cortex-contract/)
(`openapi.json` + TypeScript types; `make contract`, checked by `make contract-check` and `make test`).
Errors always look like `{"error": "<code>", "detail": …}`. The three operations an agent needs
(`request_context_pack`, `verify_output`, `submit_correction`) are marked `x-mcp-tool`; until their
issues land they answer `501 not_implemented`.

Every write enters through the **admission gate**, `POST /v1/cortex/records` (`propose_record`):
validation by the record's domain pack (schema version, canonical entity keys only, a CEL condition
compiled at admission, fields and targets known to the pack), then OPA (read scope for the purpose,
sensitivity ≤ the purpose's ceiling, who may propose). Every problem comes back at once in a
`422 invalid_proposal` and nothing is written. An accepted proposal is always `proposed`, never
approved, and joins the one open review case for the same conflict (same targets + entities).

The **review queue** (`/v1/cortex/review-cases`) is where a person decides. Each team scope has a
named owner and backup in the seed. Only they may approve or reject, and only with `memory.approve`.
Nobody who contributed to a proposal (asserted it or corrected the case it came from) may decide on
it (OPA, separation of duties). An approval sets the final authority and enforcement and records
`approved_by {approver_type, user_id, decision_id}`. A rejection leaves the proposal `deprecated`,
never approved, shown as `outcome: rejected`. Every decision is also a `kind=decision` record and an
append-only `review_decision` row. With OPA down, a decision is a 503 and nothing is written.

Which rules apply to a case is the **access gate's** job (`app/services/access_gate/`), and search
never decides it. A SQL query on governed metadata finds the candidates: approved rules of the case's
domain, at the requested scope or above it (never a sibling), with no entities or one shared with the
case, and valid on the event's business date (never the clock). Then each rule's CEL condition runs on
the case. A condition that cannot be evaluated is reported, never silently dropped. Resolution is per
target: a prohibition or mandatory rule always beats a default; between defaults, higher authority
wins, then the nearer scope. The loser is marked `overridden_by` the winner.

An agent gets a **context pack** with `POST /v1/cortex/context-packs` (`request_context_pack`):
- **Rules** come from the access gate above, never from search.
- **Knowledge** (facts, content) comes from the search projection, but only as candidates. Each hit is
  checked again against the ledger: tenant, approved now, valid on the event's date, readable scope,
  sensitivity. A record retired in the ledger but not yet re-indexed never gets in.
- **Held packs:** the answer is `409` instead of a pack when a mandatory rule or prohibition cannot be
  evaluated for the case, or is above the purpose's sensitivity ceiling.
- **Stored before returned:** every pack is saved (append-only `served_pack`) with the policy revision
  and the `(record_id, version)` pairs, and each body is sealed under the record's own key.
  `GET /v1/cortex/context-packs/{pack_id}` returns exactly what was served, without re-running
  anything. Another tenant's key gets a 404.

Before posting, the agent calls **`POST /v1/cortex/verify`** (`verify_output`) with the pack id, the
same case (checked by hash; Cortex keeps only the hash) and its output. The answer is `pass`, or
`hold` with reasons. Every check reads the ledger, never the search index:
- `rule_no_longer_approved`: a constraint in the pack has been retired since;
- `new_mandatory_rule`: a constraint now applies that was not in the pack;
- `constraint_unevaluable`: a constraint cannot be evaluated for the case;
- `output_check_failed`: the domain pack found a problem in the output.

Every result is stored, append-only, linked to the pack. Cortex does not block the client's system
itself; a `hold` is the client's signal to stop.

When a reviewer corrects an output, the client sends it to **`POST /v1/cortex/corrections`**
(`submit_correction`). The request carries the pack id, the case (checked by hash), the reviewer as
`X-End-User-Id` and an idempotency key; a retry with the same key writes nothing. What happens:
- The correction is always recorded as a `kind=decision` record.
- The domain pack turns it into a rule draft.
- The draft becomes a proposal only once the **D17 thresholds** are met: at least N distinct work cases
  and M distinct correctors on the same thing, within D days. Set them with `RULE_SUGGESTION`
  (`cases/correctors/days`, default `1/1/30`).
- The proposal goes through the admission gate as `asserted_by: policy`, with authority ≤ 10 until a
  person approves.
- The correctors are listed as contributors, so none of them may approve the rule.

Once the named owner approves it, the rule appears in the next applicable case's pack.

For audit, **`POST /v1/cortex/context-packs/{id}/export`** archives a pack's evidence manifest to
the Object Lock bucket `cortex-poc-evidence`. The manifest holds the pack as served, each record
version with who asserted and approved it, the policy revision and every verification. It is
canonical JSON, encrypted under the tenant's KMS key, with a deterministic object key. The outbox
worker does the upload and asking again returns the same export. Retention is per object
(`EVIDENCE_RETENTION_MODE`, `EVIDENCE_RETENTION_DAYS`; default COMPLIANCE, 1 day locally).
`GET …/export` shows the status, object key, version and manifest sha256.

**Erasure is crypto-shredding.** The ledger is append-only, so nothing is deleted from it; the keys
that open the bodies are destroyed instead. Every record has its own data key.
- `make erase-record TENANT=… RECORD=… BY=… REASON=…` erases a record and every record derived from
  it (through `derived_from`, any position). They get an `erased` version, their data keys and search
  rows are deleted, and their copies in served packs and evidence manifests become unreadable. The
  packs stay whole: ids, versions and hashes remain, and the other bodies stay readable.
- `make erase-tenant TENANT=… BY=… REASON=… CONFIRM=yes` disables the tenant's KMS key and schedules
  its deletion, and drops its data keys and projection. No body, pack or export of that tenant opens
  any more.

Each erasure is logged with a report (ids and counts, never text), and `make reindex` afterwards
does not bring anything back.

**Domain packs** live in `services/org-brain-service/app/domain_packs/<name>/`, and the service finds
them by discovery: the core never names one (D18). The first real pack is `custom_invoices`. It
provides:
- entities `customer:<id>`, resolved by customer id and never by name, and `channel:<name>`;
- the CEL variable `invoice` and the governed targets (registration number, tax breakdown, remarks,
  attachments);
- how a correction becomes a rule draft, and an output check for verify.

The second pack is `cash_application`. Its case is a bank payment plus the open invoice it is
matched with. It normalises bank payer names: half- and full-width kana, `ｶ)`, `(ｶ` and
`ｶﾌﾞｼｷｶﾞｲｼｬ` markers, and truncated names. It resolves them to `customer:` keys and states its
confidence and evidence. Only a `high` match names a customer; an ambiguous payer is routed to a
person, never auto-matched. Garnishment (差押) always forces review, and a learned rule books a
deducted transfer fee (≤ ¥880) as 支払手数料.

`make seed` loads each pack's `rules.json` into the ledger (idempotent). The golden sets
(`tests/integration/test_golden_*.py`, one per pack) replay the whole timeline
through the API: 40 packs, corrections, approval, retirement, verify.

Every read asks OPA first and **fails closed**: if OPA is down, the answer is
`503 {"error": "access_gate_unavailable"}`, never data. `make seed` (re)loads the tenants' scope trees
and grants into OPA; the service also does it at startup, and again by itself if OPA restarts and
forgets them.

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
| `make seed` | register the seed's tenants and load their scope tree, grants and consumers into OPA |
| `make contract` / `make contract-check` | regenerate / verify the API spec and its TS types |
| `make reindex` | delete and rebuild every tenant's search projection from the ledger |
| `make lock SVC=<service>` | regenerates `Pipfile.lock` with the pinned pipenv |

## Services

| Service | What | Host port |
|---|---|---|
| `aws-emulator` | AWS Emulator 1.0.545 (S3, KMS, Secrets Manager), built from the signed RPM (`infra/aws-emulator/`) | `127.0.0.1:9297` (HTTPS) |
| `opa` | Open Policy Agent 1.22.4, serving the `policies/` bundle (built by the one-shot `opa-bundle`) | `127.0.0.1:8181` (`CORTEX_OPA_PORT`) |
| `postgres` | PostgreSQL 16.16 + pgvector 0.8.7 + pg_bigm 1.2 (`infra/postgres/`) | `127.0.0.1:5432` (`CORTEX_POSTGRES_PORT`) |
| `org-brain-service` | Cortex backend (FastAPI); shares the emulator's network namespace (AWS client), so its port is published by `aws-emulator` | `127.0.0.1:5008` |
| `org-brain-projector` | drains the outbox into the search projection (`search_doc`: pg_bigm, `simple` FTS, pgvector for test vectors) | — |
| `org-brain-migrate` | one-shot `alembic upgrade head` before the service and projector start | — |

The ledger (`memory_record`, one immutable row per version; `memory_record_current` view; `outbox`) is
append-only: a trigger refuses UPDATE, DELETE and TRUNCATE even for the owner, and row-level security
(ENABLEd and FORCEd) keeps every tenant's rows invisible to the others. `app/services/ledger.py` is the
only way to write it: `append_new`, `transition` (a status change is the next version), `supersede`,
and `as_of` reads by system time and business time. Each write adds an outbox row in the same
transaction; commands flush and never commit, so the caller's transaction decides.

Record bodies are encrypted (`aes-gcm/1`): each tenant has a KMS key (`alias/cortex/tenant/<id>`) and
each record lineage its own data key, wrapped by KMS and kept in `record_key`. Deleting that row
shreds the record and everything derived from it; scheduling the tenant key's deletion shreds the
tenant. Governance metadata (status, scope, validity, entities) stays in clear for queries.

Postgres roles: `org_brain_owner` owns the database and runs migrations; `org_brain_app` is what the
service uses, owns nothing and cannot bypass row-level security. Their local passwords come from
`.env` (see `.env.example`); `make init-env` adds keys a new issue introduces without touching yours.

### Rule conditions (CEL)

"To which cases does this rule apply" is a CEL expression stored in the rule (D22), compiled at
admission and evaluated on the applicability path through `ConditionEvaluator`
(`app/conditions/`, engine: `cel-expr-python`). Amounts are `int`; dates reach CEL as day numbers,
so `invoice.issue_date >= date("2023-10-01")` compares calendar dates. A condition that does not
compile, is not boolean, or names an unknown variable or function is never admitted.

### OPA

Authorisation decisions (who may read, propose or approve what) live in Rego under `policies/`, never
in Python (D22). `opa-bundle` builds them into a bundle whose revision is a content hash; the policy
exposes it as `data.cortex.policy.revision` so every context pack can record which policies it was
authorised under. Tenant trees and grants are pushed through OPA's Data API under `cortex/tenants`.

| Decision | Path | Answers |
|---|---|---|
| read | `data.cortex.policy.authz.decision` | may this consumer (and end user) read at this scope for this purpose; readable scopes (the node and its ancestors); sensitivity ceiling |
| propose / approve / reject | `data.cortex.policy.approval.decision` | `memory.write` to propose, `memory.approve` to decide; only a person decides, and never on a record they contributed to |

Both translate the `arbitr-rbac` resolver (nearest grant up the tree, information barriers, expiry at `now`).

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

## Domain packs

The core never knows which workflow is running (D18). Everything workflow-specific sits in a domain
pack behind `DomainPack` (`app/domain_packs/base.py`): entity types and resolver, what conditions may
read (variables, fields, which fields name an entity), rule targets, rule and correction schemas,
correction → proposed rule, search text, and an optional output check for verify. The core is tested
with a made-up pack (`tests/fakes/fake_pack.py`); real packs must pass the same contract
(`tests/domain_packs/contract.py`), and a guard test fails if core code imports a real pack or
branches on a workflow name.

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
