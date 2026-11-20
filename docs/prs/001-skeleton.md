# PR 001 — Repo skeleton, org-brain `/health`, Makefile and container checks

Branch: `poc/001-skeleton` → `main` · Issue: [docs/tasks.md §1](../tasks.md#1-esqueleto-do-repo-org-brain-com-health-makefile-e-checagens-em-container)

## What
- `services/org-brain-service/`: FastAPI `create_app()`, `OrgBrainConfig` (pydantic-settings), structlog JSON logs (copied from the monorepo's `logging_config.py`), request-id middleware, `GET /health`.
- `Dockerfile` in the monorepo's shape (shared libs at `/libraries/<name>`, Pipenv `--deploy`), two targets: `runtime` (non-root user) and `dev`. Base `python:3.12.16-slim-trixie`, `pipenv==2026.10.6`.
- `libraries/arbitr-test-utils/`: copied unchanged from `main` (socket guard + pytest plugin).
- `docker-compose.yml`: `org-brain-service` (port bound to `127.0.0.1`, healthcheck) and two tool services under the `tools` profile: `org-brain-dev` (`network_mode: none`, used by test/lint/typecheck) and `pipenv-lock`.
- `Makefile`: `help init-env dev stop logs ps shell build build-tools build-multiarch lock test lint format typecheck`. `make stop` removes volumes (ephemeral stack, Q4).
- `docs/adr/0001-test-tiers.md` (Q3), README, `.env.example`, `.gitignore`.

## Decisions worth a look
- Unit tests run in a container with **no network**, on top of the socket guard.
- `pyright[nodejs]` bundles Node in a wheel, so typecheck also runs offline.
- Starlette's new warning pointing TestClient at `httpx2` is filtered; the monorepo still uses httpx.
- Ruff/Pyright settings follow `dubbing-service`/`integrations-service` in the monorepo (select `E F I B UP SIM RUF`, ignore `B008`, Pyright strict on `app/`).

## Acceptance criteria — output
Run from a clean clone at `~/cortex-v2/.clean-check` (under `$HOME`, see note below).

**1. Clean clone + `make dev` → healthy; `/health` answers**
```
$ git clone -b poc/001-skeleton ~/cortex-v2/cortex-v2-poc ~/cortex-v2/.clean-check && cd ~/cortex-v2/.clean-check
$ cp .env.example .env && make dev
 Container cortex-poc-org-brain-service-1 Healthy
Stack up — org-brain-service: http://127.0.0.1:5008/health
$ curl -s 127.0.0.1:5008/health
{"status":"ok","service":"org-brain-service","version":"0.1.0"}
$ make ps
cortex-poc-org-brain-service-1   cortex-poc/org-brain-service:dev   ...   Up 5 seconds (healthy)   127.0.0.1:5008->5008/tcp
```
(The issue text said `{"status":"ok"}`; the body also carries `service` and `version`, as the monorepo's `HealthResponse` does.)

**2. `make test` passes with the stack stopped and fails if a test opens a socket**
```
$ make stop
 Network cortex-poc_cortex Removed
$ make test
4 passed in 0.01s
```
`tests/test_hermetic_guard.py` asserts that a TCP connect to `10.255.255.1` and a DNS lookup of `postgres` raise `HermeticTestViolationError`; the container has no network as a second layer.

**3. `make lint` and `make typecheck`**
```
$ make lint
All checks passed!
15 files already formatted
$ make typecheck
0 errors, 0 warnings, 0 informations
```

**4. Image builds for `linux/arm64` and `linux/amd64`**
```
$ make build-multiarch; echo "exit=$?"
#16 [linux/arm64 runtime 2/2] COPY services/org-brain-service/ /app/
#20 [linux/amd64 runtime 2/2] COPY services/org-brain-service/ /app/
exit=0
```

**Clean tree after the run:** `git status --short` → empty.

## Found while testing
- A first clean-clone run from `/tmp` reported `no tests ran`: Colima only shares `$HOME` with its VM, so the bind mount was empty. Not a repo bug; documented in the README.
- The editable install wrote `arbitr_test_utils.egg-info/` into the mounted library and it slipped into the first commit; removed and added `*.egg-info/` to `.gitignore`.

## Not in this PR
Postgres (issue 2), AWS Emulator (3), OPA (4). No secrets anywhere; `.env` is git-ignored.
