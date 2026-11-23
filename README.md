# Cortex v2 PoC

Proof of concept of the Cortex v2 core (governed organisational memory, arbitr), stage 1 of the
Memory Architecture Rev. B: *an approved reviewer correction improves the next applicable case,
stays out of unrelated work and remains attributable.* Pilot slice: IBM Japan, Phase 1 = a small
subset of **Payment Reconciliation** (Lee, 2026-11-22; not AskQ2C), proved on the `cash_application`
pack, with `custom_invoices` as the second pack (D18). Synthetic Japanese data only.

- What to build: [`docs/architecture/`](docs/architecture/) (Rev. B, Rev. C, pilot scope, high-level proposal).
- Decisions and findings: [`docs/discovery.md`](docs/discovery.md). Plan: [`docs/tasks.md`](docs/tasks.md).
  Each issue's PR description, with its acceptance output: [`docs/prs/`](docs/prs/).
- Backend name is `org-brain` (service, routes, symbols); the product and UI name is **Cortex**.

> Status: issues 0–30 of the plan are done.

## Quick start

**Prerequisites:** only Docker. On a Mac with Apple Silicon the team uses Colima (open source; see
`docs/tasks.md`, issue 0):

```bash
brew install colima docker docker-compose docker-buildx
colima start --vm-type vz --arch aarch64 --cpu 4 --memory 8 --disk 60 --mount-type virtiofs
```

Add `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` to `~/.docker/config.json`
so `docker compose` and `docker buildx` are found. Keep the clone under your home directory: Colima
only shares `$HOME` with its VM, so a clone in `/tmp` builds but the containers see empty bind mounts.
Everything also builds on `linux/amd64` (`make build-multiarch`).

**Run:**

```bash
make dev     # creates .env from .env.example, builds, starts, provisions AWS (Terraform), waits until healthy
make seed    # loads both domain packs' initial rules into the ledger (idempotent)
make e2e     # the whole demo, both workflows, through the public API (needs a fresh stack)
make stop    # stops everything and deletes the volumes: the stack is ephemeral
```

- API: <http://127.0.0.1:5008/health>
- Cortex UI: <http://localhost:5001/cortex>; the Decision Desk: <http://localhost:5001/cortex/decision-desk>

`make help` lists every target. Never call `docker compose`, `pipenv` or `pytest` directly.

## The demo

`make e2e` runs this story for each workflow and writes a report per workflow (`.e2e/report-*.md`).
You can follow the same steps by hand: the API with `curl`, the decision in the Decision Desk.

1. **A happy case.** The agent asks for a context pack and gets exactly the rules that apply. Verify
   of a correct output passes.
2. **Scenario 2.** A local instruction ("omit the registration number under ¥30,000") loses to the
   global prohibition: it is in the pack as `overridden_by` the prohibition.
3. **Scenario 3.** A contract clause applies to a case that shares no words with it: rules come from
   governed metadata and CEL, never from search.
4. **A correction.** A reviewer corrects the agent's output. Cortex records the decision and
   proposes a rule. The named owner approves it in the Decision Desk; the corrector cannot.
5. **The next applicable case** carries the learned rule; **an unrelated case** does not. The
   applied rule names its correction, corrector, approver and the policy revision (attributable).
6. **Evidence.** The pack is exported to an Object Lock bucket, encrypted under the tenant's key.
7. **Criterion (a).** `make reindex` rebuilds the search projection; the same cases give the same packs.
8. **Scenario 4.** The owner retires a customer rule. Verify holds the earlier pack
   (`rule_no_longer_approved`), and later packs no longer carry it.
9. **Erasure.** `make erase-record` crypto-shreds the learned rule. The earlier pack stays whole
   but that body is gone; the record is `erased`; new packs drop it; verify no longer passes.

## How it works

**The ledger is the only system of record.** `memory_record` holds one immutable row per version,
with `memory_record_current` as the current view. A trigger refuses UPDATE, DELETE and TRUNCATE even
for the owner, and row-level security (ENABLEd and FORCEd) keeps every tenant's rows invisible to the
others. `app/services/ledger.py` is the only way to write it: a status change is the next version,
and each write adds an outbox row in the same transaction.

Record bodies are encrypted (`aes-gcm/1`). Each tenant has a KMS key (`alias/cortex/tenant/<id>`),
and each record its own data key, wrapped by KMS and kept in `record_key`. Governance metadata
(status, scope, validity, entities) stays in clear for queries. `entities[]` holds only opaque
canonical keys (`customer:CUST-0042`), never a person's name.

**Writes: the admission gate.** `POST /v1/cortex/records` (`propose_record`) validates by the
record's domain pack:
- the schema version;
- canonical entity keys only;
- a CEL condition compiled at admission;
- fields and targets the pack knows.

Then OPA checks the read scope for the purpose, sensitivity ≤ the purpose's ceiling, and who may
propose. Every problem comes back at once (`422 invalid_proposal`), and nothing is written. An
accepted proposal is always `proposed`, never approved, and joins the one open review case for the
same conflict.

**Decisions: the review queue.** `/v1/cortex/review-cases` is where a person decides.
- **Who:** only the named owner or backup of the scope (in the seed), and only with `memory.approve`.
- **Separation of duties:** nobody who contributed to a proposal (asserted it, or corrected the case
  it came from) may decide on it.
- **Approval** sets the final authority and enforcement and records
  `approved_by {approver_type, user_id, decision_id}`.
- **Rejection** leaves the proposal `deprecated`, never approved, shown as `outcome: rejected`.
- **Retirement** (`POST /v1/cortex/records/{id}/retire`, same owners) withdraws an approved record:
  `outcome: retired`.

Every decision is also a `kind=decision` record. The case detail says ahead, from OPA, whether the
caller may decide and why not (`can_decide`).

**Reads: the access gate and context packs.** `POST /v1/cortex/context-packs`
(`request_context_pack`) builds a pack:
- **Rules** come from SQL on governed metadata, never from search:
  - approved rules of the case's domain;
  - at the requested scope or above it, never a sibling;
  - with no entities, or one shared with the case;
  - valid on the event's business date, never the clock.

  Then each rule's CEL condition runs on the case.
- **Resolution** is per target: a prohibition or mandatory rule always beats a default; between
  defaults, the higher authority wins, then the nearer scope.
- **Knowledge** (facts, content) comes from the search projection as candidates only. Each hit is
  revalidated against the ledger: tenant, approved now, valid on the date, readable scope,
  sensitivity.
- **Held packs:** a pack is held (`409`) rather than served without a constraint it cannot evaluate,
  or one above the ceiling.
- **Stored before returned:** every pack is saved with the policy revision and the
  `(record_id, version)` pairs, each body sealed under its record's own key. `GET …/{pack_id}`
  replays it.

**Before posting: verify.** `POST /v1/cortex/verify` (`verify_output`) checks the output against its
pack, on the ledger only. It answers `pass`, or `hold` with one of these reasons:
- `rule_no_longer_approved`;
- `new_mandatory_rule`;
- `constraint_unevaluable`;
- `output_check_failed`, which the domain pack decides, with the case.

Results are stored. Cortex never blocks the client's system itself (D3).

**Writeback.** `POST /v1/cortex/corrections` (`submit_correction`):
1. The correction is always recorded as a decision. Requests are idempotent by key.
2. The domain pack drafts a rule.
3. Once the D17 thresholds are met (`RULE_SUGGESTION=cases/correctors/days`, default `1/1/30`), the
   draft is proposed through the admission gate as `policy`, with authority ≤ 10 until a person
   approves. Its correctors are contributors, so they cannot approve it.

**Evidence.** `POST /v1/cortex/context-packs/{id}/export` archives the pack's manifest to the
`cortex-poc-evidence` bucket. The manifest holds:
- the pack as served;
- each record version with who asserted and approved it;
- the policy revision and the verifications.

It is canonical JSON, enveloped under the tenant's KMS key, with each body sealed under its record's
own key. The object key is deterministic, the retention is Object Lock per object
(`EVIDENCE_RETENTION_MODE`, `EVIDENCE_RETENTION_DAYS`), and the outbox worker does the upload.

**Erasure is crypto-shredding.**
- `make erase-record TENANT= RECORD= BY= REASON=` erases a record and every record derived from it:
  they get an `erased` version, and their data keys and search rows are deleted. Their copies in
  packs and evidence become unreadable, while the packs stay whole.
- `make erase-tenant TENANT= BY= REASON= CONFIRM=yes` disables and schedules deletion of the tenant's
  KMS key, and drops its data keys and projection.

Each erasure is logged with a report of ids and counts, never text.

**AI goes through LiteLLM, and only through it** (issue 30, `app/clients/llm.py`). Two uses, each
with its own switch in `.env`, and both off when no key is provisioned:
- **Rule suggestion** (`LLM_RULE_SUGGESTION=true`): once the D17 thresholds are met, the chat model
  (`anthropic/claude-sonnet-5-5`) drafts the rule from the correction, the case, the pack's baseline
  draft and the pack's vocabulary (CEL variables and fields, targets, entity fields). The draft goes
  through the admission gate like any other, as `asserted_by: model`, with authority ≤ 10. A person
  still approves it. A draft the gate refuses, or no answer, falls back to the pack's own draft, and
  the reasons come back in `proposal_problems` (`llm_…`). `suggested_by` says which one was used.
- **Embeddings** (`LLM_EMBEDDINGS=true`): the projector embeds each record
  (`azure/text-embedding-ada-002`, 1536 dimensions) and the context pack embeds its query, so
  knowledge that shares no word with the case can be a candidate. Every candidate is still revalidated
  against the ledger, and rules never come from search. After switching embeddings on, run
  `make reindex`.

No model sits on the applicability, resolution or verify path. The key is `LITELLM_API_KEY` in your
git-ignored `.env`. `make provision` stores it in Secrets Manager (`cortex-poc/litellm-api-key`), and
the service and projector read it from there. `make test-llm` calls the real LiteLLM; `make test`
never does.

**Authorisation is OPA's** (`policies/`, D22), and every call fails closed: OPA down means
`503 access_gate_unavailable`, never data.

| Decision | Path | Answers |
|---|---|---|
| read | `data.cortex.policy.authz.decision` | may this consumer (and end user) read at this scope for this purpose; readable scopes (the node and its ancestors); sensitivity ceiling |
| propose / approve / reject / retire | `data.cortex.policy.approval.decision` | `memory.write` to propose; `memory.approve` to decide, by a person, the named owner or backup, never a contributor |

Both translate the `arbitr-rbac` resolver: the nearest grant up the tree, information barriers, and
expiry at `now`. The bundle revision (a content hash) is recorded in every pack. `make seed` and the
service push the tenants' trees and grants into OPA.

**Rule conditions are CEL** (D22, `app/conditions/`, engine `cel-expr-python`). Amounts are `int`, and
dates reach CEL as day numbers, so `invoice.issue_date >= date("2023-10-01")` compares calendar
dates. A condition that does not compile, is not boolean, or names an unknown variable is never
admitted.

**Domain packs** (`services/org-brain-service/app/domain_packs/<name>/`) hold everything
workflow-specific. The core finds them by discovery and never names one (D18); a guard test enforces
that.
- **`custom_invoices`:**
  - `customer:` entities resolved by id, never by name, and `channel:` entities;
  - the CEL variable `invoice` and the invoice targets;
  - the correction → rule draft, and an output check.
- **`cash_application`:**
  - a bank payer-name normaliser: half- and full-width kana, `ｶ)`, `(ｶ` and `ｶﾌﾞｼｷｶﾞｲｼｬ` markers,
    truncation;
  - a resolver that states its confidence and evidence. Only `high` names a customer, and an
    ambiguous payer is never auto-matched;
  - garnishment (差押) always forces review, and the learned fee rule (≤ ¥880 → 支払手数料).

Each pack passes the shared contract (`tests/domain_packs/contract.py`) and its golden set
(`tests/integration/test_golden_*.py`) at 100%.

**Frontend** (`frontend/arbitr/`, the monorepo's stack):
- **Stack:** Nuxt 4 (`ssr: false`), Nuxt UI 4.6, Tailwind 4.2, Pinia 3, i18n (`en`), Zod, ESLint with
  the accessibility rules, and the brand v2 tokens and guard.
- **The API key stays on the server.** The browser talks only to a same-origin proxy (`/api/cortex`),
  which adds the key from `CORTEX_DESK_API_KEY` at runtime. `make test` builds the bundle with a
  canary key and fails if it appears anywhere.
- **The Decision Desk** lists cases and shows the evidence. You approve with the final authority and
  enforcement, or reject with a reason. A dev user selector sets `X-End-User-Id`; in production that
  comes from the session.

## API

Consumers authenticate with a static API key per consumer, a PoC stand-in for OIDC. The tenant
always comes from that identity, never from the request. Local keys are in `CORTEX_DEV_API_KEYS`
(`.env`), and Secrets Manager holds only their sha256. The person acting goes in `X-End-User-Id`.

```bash
curl -s 127.0.0.1:5008/v1/cortex/whoami -H "Authorization: Bearer local-dev-askq2c-key"
```

The contract is generated from the code into [`libraries/cortex-contract/`](libraries/cortex-contract/)
(`openapi.json` and TypeScript types; `make contract`, checked by `make contract-check` and
`make test`). Errors always look like `{"error": "<code>", "detail": …}`. The three operations an
agent needs are marked `x-mcp-tool`: `request_context_pack`, `verify_output` and `submit_correction`.

| Operation | Route |
|---|---|
| `whoami` | `GET /v1/cortex/whoami` |
| `list_records` / `get_record` | `GET /v1/cortex/records`, `GET /v1/cortex/records/{id}` |
| `propose_record` | `POST /v1/cortex/records` |
| `retire_record` | `POST /v1/cortex/records/{id}/retire` |
| `list_review_cases` / `get_review_case` | `GET /v1/cortex/review-cases`, `GET …/{id}` |
| `approve_review_case` / `reject_review_case` | `POST …/{id}/approve`, `POST …/{id}/reject` |
| `request_context_pack` / `get_context_pack` | `POST /v1/cortex/context-packs`, `GET …/{id}` |
| `export_context_pack` / `get_context_pack_export` | `POST`/`GET /v1/cortex/context-packs/{id}/export` |
| `verify_output` | `POST /v1/cortex/verify` |
| `submit_correction` | `POST /v1/cortex/corrections` |

## Checks (all in containers)

| Target | What it runs |
|---|---|
| `make test` | hermetic unit tests (no network, no stack; [ADR 0001](docs/adr/0001-test-tiers.md)), frontend Vitest, the bundle secret check |
| `make test-integration` | integration tests on an ephemeral stack (`-p cortex-poc-test`, no host ports, wiped after), including both golden sets |
| `make scenarios` | the six Rev. B failure scenarios, one named test each (import with an expired offer · local vs global prohibition · mandatory rule missed by search · withdrawal while the projection lags · replay after a model or index change · one conflict, hundreds of jobs) |
| `make test-policies` | `opa test` on `policies/` |
| `make test-all` | `test` + `test-integration` + `test-policies`: the definition of "tests pass" |
| `make e2e` | the demo above, both workflows, public API, on a fresh running stack |
| `make test-llm` | the real LiteLLM, opt-in (needs `LITELLM_API_KEY`): a Sonnet 5.5 rule draft the gate accepts, 1536-dimension embeddings with meaning |
| `make test-e2e-ui` | Playwright (Decision Desk) in a container against the running stack (after `make seed`) |
| `make lint` / `make format` | Ruff, migrate-check, `opa check --strict` + Regal, `terraform fmt -check`, ESLint + the v2 style guard / Ruff and Terraform format |
| `make typecheck` | Pyright (strict on `app/`) and vue-tsc |
| `make build-multiarch` | builds the images for `linux/arm64` and `linux/amd64` |
| `make probe-emulator` | AWS Emulator fidelity probe → [`docs/emulator-probe-1.0.545.md`](docs/emulator-probe-1.0.545.md) |
| `make provision` / `make tf-plan` / `make tf-lock` | Terraform apply / plan against the emulator; refresh provider hashes for both architectures |
| `make seed` / `make reindex` | load tenants, OPA data and the packs' rules / rebuild the search projection from the ledger |
| `make erase-record` / `make erase-tenant` | crypto-shredding (see above) |
| `make gen-data` | regenerate `data/synthetic/` (deterministic: a rerun leaves `git diff` empty) |
| `make migrate` / `make migrate-check` | Alembic upgrade as the table owner / one head, ids ≤ 32 chars |
| `make contract` / `make contract-check` | regenerate / verify the API spec and its TS types |
| `make opa-reload` | rebuild the policy bundle and restart OPA after editing `policies/` |
| `make lock SVC=<service>` | regenerate `Pipfile.lock` with the pinned pipenv |

## Services

| Service | What | Host port |
|---|---|---|
| `aws-emulator` | AWS Emulator 1.0.545 (S3, KMS, Secrets Manager), built from the signed RPM (`infra/aws-emulator/`) | `127.0.0.1:9297` (HTTPS) |
| `opa` | Open Policy Agent 1.22.4 serving the `policies/` bundle (built by `opa-bundle`) | `127.0.0.1:8181` |
| `postgres` | PostgreSQL 16.16 + pgvector 0.8.7 + pg_bigm 1.2 (`infra/postgres/`) | `127.0.0.1:5432` |
| `org-brain-service` | Cortex backend (FastAPI); shares the emulator's network namespace, so its port is published by `aws-emulator` | `127.0.0.1:5008` |
| `org-brain-projector` | drains the outbox: search projection, evidence exports | — |
| `org-brain-migrate` | one-shot `alembic upgrade head` before the service starts | — |
| `frontend` | Nuxt dev server with the Cortex proxy | `127.0.0.1:5001` |

Postgres roles:
- `org_brain_owner` owns the tables and runs migrations;
- `org_brain_app` is what the service uses: it owns nothing and cannot bypass row-level security.

## IBM Cloud ↔ AWS (this PoC)

Rev. C: the target is IBM Cloud, and the PoC runs the AWS equivalents locally. Cloud differences live
only in `infra/terraform/` and behind the AWS SDK (configured by environment variables only).

| Capability | IBM Cloud (pilot) | This PoC |
|---|---|---|
| Ledger, review queue, served packs, verifications | IBM Cloud Databases for PostgreSQL 16 | Postgres 16.16 container (pgvector + pg_bigm) |
| Search projection | pgvector + FTS (+ pg_trgm) | same Postgres: `simple` FTS + pg_bigm (Japanese) + pgvector with fixed test vectors |
| Tenant keys, crypto-shredding | Key Protect | KMS (AWS Emulator) |
| Secrets (consumer key hashes) | Secrets Manager | Secrets Manager (AWS Emulator) |
| Evidence archive with immutable retention | Cloud Object Storage, immutable retention | S3 with Object Lock COMPLIANCE (AWS Emulator) |
| Provisioning | Terraform IBM Cloud module | Terraform AWS provider in a container |
| Authorisation | OPA (same) | OPA 1.22.4 container |
| Identity | OIDC (D1) | a static API key per consumer, plus `X-End-User-Id` |
| Models / embeddings | watsonx.ai via a gateway | LiteLLM (`devs-ai.straker.ai`, UAT): Sonnet 5.5 for rule drafts, ada-002 for embeddings, both opt-in |

## Emulator limits

- **What works:** the probe passes every required operation, including Object Lock: `DeleteObject`
  of a version under COMPLIANCE retention is refused, and `Decrypt` after `ScheduleKeyDeletion` is
  refused. That fidelity is the emulator's, not proof against real S3/COS or Key Protect (risk R3).
- **IAM is not enforced.** The least-privilege policy is rendered to
  [`infra/iam/org-brain-service.json`](infra/iam/org-brain-service.json) for review and applied
  nowhere. "Works locally" is not proof of a permission.
- **State is in memory.** The CA and credentials are regenerated on every start, so restarting the
  emulator loses every bucket, secret and key. That is why the whole stack is ephemeral and
  `make stop` deletes the volumes.
- **The certificate is valid for `localhost`/`127.0.0.1` only.** AWS clients join the emulator's
  network namespace and call `https://127.0.0.1:9297`, with TLS verification on.
- **KMS `TagResource` answered 501** in earlier releases, so the application never tags KMS keys.
- **pg_bigm is not on IBM Cloud Databases' extension list** (risk R5, for WS6).

## Deviations and decisions on record

| What | Why | Where |
|---|---|---|
| Six Rev. B statuses; a rejection is `deprecated` without approval, shown as `outcome: rejected` | Rev. B has no `rejected` (taken to WS1 as a gap) | Q5, issue 18 |
| A named owner and backup per scope decide; a decided case shows OPA's reason ahead (`can_decide`) | doc 06; the corrector must see why they cannot approve | issues 18, 28 |
| `review_case.title` dropped: read from the sealed body | it held body text in clear, outside crypto-shredding | issue 18 |
| A data key per record, not per lineage root | so a derived record can be erased alone, and erasing any parent shreds a child | Q10, issue 24 |
| Evidence manifests keep bodies sealed per record | an Object Lock archive cannot be deleted, but erased bodies must not stay readable | issue 24 |
| Learned rules have no lower validity bound | a correction teaches a requirement that already existed | your decision, issue 25 |
| A reissue is judged as of its reissue date | rules in force when it is issued, CEL on the old invoice date | your decision, issue 25 |
| `check_output` receives the case | verify let an auto-match of an ambiguous payer pass | your decision, issue 26 |
| Machine proposals stay in the ledger as `proposed`; the database refuses a `model` or `policy` record born approved, or approved by anyone but a person | Nake: model rules never skip review. Rev. B keeps `proposed` in the envelope, so rejections stay auditable | issue 34 |
| A model's rule draft is proposed as `model`, never approved, with a fallback to the pack's draft | rule 4: machines propose, people approve; the golden path stays deterministic | issue 30 |
| `search_doc.embedding` is a plain `vector` (no fixed dimension) | real embeddings (1536) and the fixed test vectors (8) coexist; search compares the same dimension only | issue 30 |
| Phase 1 = the `cash_application` pack, IBM calls as `ibm-pr-agent` (pack-only, purpose `payment_reconciliation`); reviewers stay OWN-001/OWN-002 | the subset, the real consumer and the reviewers come from the December workshop (Lee, Erika). `askq2c-agent` remains the PoC's full-loop agent; the architecture docs keep IBM's original naming | issue 35 |
| Regions: a deployment per region; approved, non-personal records in the published Global scope (the tenant root) are replicated, read-only, with a stable `record_id` (`make replicate`) | Nake's question; APPI. A proposal until WS6, Legal and IBM decide. The prototype uses two databases in one Postgres | [ADR 0002](docs/adr/0002-regions.md), issue 32 |
| Example sets: Japanese (tenant `jp`) and English (tenant `en`), switched in the Decision Desk; rule text stays in `text_ja` in both | a way to test by hand in either language without mixing data; renaming the field would touch every pack and contract | issue 39, [`data/examples/`](data/examples/) |
| Owners retire through `POST /records/{id}/retire` | scenario 4 through the public API | issue 29 |
| Erasure is an admin CLI, not a route | no admin identity model in the PoC | issue 24 |
| The Decision Desk is English only; Japanese data is shown as it came | UI in Japanese is out of scope | P7 |
| The frontend image pins npm 11.6.2 | npm 10.9.0 (bundled with node 22.12.0) crashes on this dependency tree | issue 27 |
| `make test` is hermetic only; integration runs on its own ephemeral stack | the monorepo's test rule; RLS, OPA and the emulator need real services | Q3, ADR 0001 |

## Synthetic data

Everything in [`data/synthetic/`](data/synthetic/) is fictitious, in Japanese, and generated with a
fixed seed (`make gen-data`; the generators live in each domain pack). `tenant.json` is the shared
scope tree, people (role labels only), named owners and consumers. Per workflow:

| File | What |
|---|---|
| `cases.jsonl` | 40 invoices (custom invoices) or 40 bank payments (cash application), JPY as integers |
| `invoices.json` | cash application only: the open invoice each payment is matched against |
| `customers.json` | fictitious customers (cash application: registered bank names in half-width kana, garnishment flag) |
| `rules.json` | initial rules with their CEL condition, scope, entities and enforcement |
| `corrections.jsonl` | reviewer corrections and the rule each should become |
| `timeline.json` | the ordered steps a harness replays (process, correct, approve, retire) |
| `expected.json` | golden set: the exact rules per case, overridden local rules, verify outcomes; cash application adds payer resolution and route |

## Layout

Mirrors the arbitr monorepo so the code can move there later:

```
services/org-brain-service/   FastAPI service (app/, tests/, alembic/, Pipfile, Dockerfile)
frontend/arbitr/              Nuxt app (Cortex pages, proxy, Vitest; e2e/ = Playwright)
libraries/                    cortex-contract (OpenAPI + TS types), arbitr-test-utils
policies/                     Rego policies (OPA bundle) + tests
infra/                        Postgres and emulator images, Terraform, rendered IAM policy
data/synthetic/               generated synthetic data + golden sets
tests/e2e/                    the public-API end-to-end harness (make e2e)
docs/                         architecture, discovery, tasks, ADRs, PR descriptions (docs/prs/)
```

## Secrets

Nothing secret is committed. `.env` is git-ignored, and only `.env.example` is versioned.
`make init-env` adds keys that a new issue introduces, without touching yours. Keys never reach the
browser bundle, images or logs. `LITELLM_API_KEY` belongs in `.env` too. `make provision` stores it in
Secrets Manager, and the service reads it from there, never from code or the environment. The Terraform
state that carries it lives on a volume that `make stop` deletes.
