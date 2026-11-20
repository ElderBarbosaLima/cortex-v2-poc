# Cortex v2 PoC

Proof of concept of the Cortex v2 core (governed organisational memory, arbitr), stage 1 of the
Memory Architecture Rev. B: *an approved reviewer correction improves the next applicable case,
stays out of unrelated work and remains attributable.* Pilot slice: IBM Japan AskQ2C (custom
invoices and cash application), with synthetic Japanese data only.

- What to build: [`docs/architecture/`](docs/architecture/) (Rev. B, Rev. C, pilot scope, high-level proposal).
- Decisions and findings: [`docs/discovery.md`](docs/discovery.md). Plan: [`docs/tasks.md`](docs/tasks.md).
- Backend name is `org-brain` (service, routes, symbols); the product and UI name is **Cortex**.

> Status: issues 1–2 of 29 (skeleton, Postgres). Most of what the docs describe is not built yet.

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
make dev                              # build + start, waits until healthy (creates .env from .env.example)
curl -s 127.0.0.1:5008/health         # {"status":"ok","service":"org-brain-service","version":"0.1.0"}
make stop                             # stops everything and deletes volumes: the stack is ephemeral
```

`make help` lists every target. Never call `docker compose`, `pipenv` or `pytest` directly.

## Checks (all in containers)

| Target | What it runs |
|---|---|
| `make test` | hermetic unit tests (no network, no stack) — see [ADR 0001](docs/adr/0001-test-tiers.md) |
| `make test-integration` | integration tests on an ephemeral stack (`-p cortex-poc-test`, no host ports, wiped after) |
| `make lint` / `make format` | Ruff check + format check / Ruff format + fix |
| `make typecheck` | Pyright (strict on `app/`) |
| `make build-multiarch` | builds the images for `linux/arm64` and `linux/amd64` |
| `make lock SVC=<service>` | regenerates `Pipfile.lock` with the pinned pipenv |

## Services

| Service | What | Host port |
|---|---|---|
| `postgres` | PostgreSQL 16.16 + pgvector 0.8.7 + pg_bigm 1.2 (`infra/postgres/`) | `127.0.0.1:5432` (`CORTEX_POSTGRES_PORT`) |
| `org-brain-service` | Cortex backend (FastAPI) | `127.0.0.1:5008` |

Postgres roles: `org_brain_owner` owns the database and runs migrations; `org_brain_app` is what the
service uses, owns nothing and cannot bypass row-level security. Their local passwords come from
`.env` (see `.env.example`); `make init-env` adds keys a new issue introduces without touching yours.

## Layout

Mirrors the arbitr monorepo so the code can move there later:

```
services/org-brain-service/   FastAPI service (app/, tests/, Pipfile, Dockerfile)
libraries/                    shared libraries (arbitr-test-utils, copied from the monorepo)
docs/                         architecture, discovery, tasks, ADRs, PR descriptions (docs/prs/)
```

## Secrets

Nothing secret is committed. `.env` is git-ignored; only `.env.example` is versioned.
