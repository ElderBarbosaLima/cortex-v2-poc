# PR 002 — Postgres 16 image with pgvector + pg_bigm, roles and the test stack

Branch: `poc/002-postgres` → `main` · Issue: [docs/tasks.md §2](../tasks.md#2-imagem-postgres-16-com-pgvector--pg_bigm-papéis-e-stack-de-teste) · Depends on: 001 (merged)

## What
- `infra/postgres/Dockerfile`: `pgvector/pgvector:0.8.7-pg16-trixie` pinned by digest (`sha256:c28c0b70…`, multi-arch). A build stage compiles **pg_bigm `v1.2-20250903`** with PGXS (tarball pinned by sha256) and copies only the installed files into the final image. Tag: `cortex-poc/postgres:16-pgvector0.8.7-bigm1.2.20250903`.
- `infra/postgres/initdb/01-org-brain.sh`: database `org_brain`, roles `org_brain_owner` (owns DB + `public`) and `org_brain_app` (`NOSUPERUSER NOBYPASSRLS NOINHERIT`, `USAGE` only), `CREATE EXTENSION vector, pg_bigm`. Passwords passed as psql variables, never interpolated into SQL.
- `docker-compose.yml`: `postgres` service (named volume, removed by `make stop`; port on `127.0.0.1`); `org-brain-service` now waits for it to be healthy and gets the DB settings.
- `docker-compose.test.yml` + `make test-integration`: own project `cortex-poc-test`, data on tmpfs, no host ports, `down -v` after every run (pass or fail).
- `OrgBrainConfig`: Postgres settings (`SecretStr` passwords). `psycopg2-binary` added (same driver as the monorepo).
- `make init-env` now adds keys that `.env.example` gained without overwriting existing values; `make build-multiarch` also builds the Postgres image.

## Decisions worth a look
- **Healthcheck over TCP** (`pg_isready -h 127.0.0.1`). During first-boot init the official entrypoint runs a temporary server on the unix socket only; a socket check would go healthy before the roles and extensions exist.
- **pg_bigm `1.2-20250903`, not the newer `1.2-20261118`**: it is the version Amazon RDS ships for PG 16.12+, so the AWS path matches. The base has 16.16 headers, satisfying pg_bigm's "16.12 or later" note.
- **trixie (Debian 13)**, the same base family as the Python image. `pg16` without suffix points to bookworm; I pinned the explicit trixie tag + digest.
- Extensions are created by the init script (superuser), so migrations never need a superuser.
- Dev passwords in `.env.example` are placeholders for a local, ephemeral, 127.0.0.1-only stack (monorepo pattern). The test stack's passwords live only inside `docker-compose.test.yml`'s throwaway project.

## Acceptance criteria — output
Clean clone at `~/cortex-v2/.clean-check-002`.

**1. Extensions at pinned versions**
```
$ make dev
 Container cortex-poc-org-brain-service-1 Healthy
$ docker compose exec -T postgres psql -U postgres -d org_brain -c "SELECT extname, extversion FROM pg_extension ORDER BY 1"
 extname | extversion
---------+------------
 pg_bigm | 1.2
 plpgsql | 1.0
 vector  | 0.8.7
```

**2. `show_bigm` on Japanese + a `gin_bigm_ops` index**
```
$ ... -c "SELECT show_bigm('架空電機株式会社')"
 {" 架",会社,式会,架空,株式,機株,"社 ",空電,電機}
```
The integration test also builds a `gin (body gin_bigm_ops)` index and finds `注文番号を備考に記載` with `LIKE likequery('備考')`.

**3. `org_brain_app` is not superuser and cannot bypass RLS**
```
     rolname     | rolsuper | rolbypassrls
-----------------+----------+--------------
 org_brain_app   | f        | f
 org_brain_owner | f        | f
```
Integration tests also assert: no `CREATEROLE`/`CREATEDB`, `org_brain_owner` owns the DB and `public`, `CREATE TABLE` as `org_brain_app` raises `InsufficientPrivilege`.

**4. Image builds for `linux/arm64` and `linux/amd64`**
```
$ make build-multiarch; echo "exit=$?"
#11 [linux/arm64 bigm-build 4/4] RUN curl ... && make USE_PGXS=1 && make USE_PGXS=1 install DESTDIR=/out
#15 [linux/amd64 bigm-build 4/4] RUN curl ... && make USE_PGXS=1 && make USE_PGXS=1 install DESTDIR=/out
exit=0
```
(`pg_bigm.tar.gz: OK` printed for both platforms.)

**5. `make test-integration` brings up the test stack, runs, and removes it**
```
$ make test-integration
 Container cortex-poc-test-postgres-1 Healthy
.......
7 passed, 4 deselected in 0.05s
$ docker volume ls --filter name=cortex-poc-test -q | wc -l   →  0
$ docker ps -a  --filter name=cortex-poc-test -q | wc -l      →  0
```

**Definition of done**
```
$ make stop
 Volume cortex-poc_postgres-data Removed
$ make test        → 4 passed, 7 deselected
$ make lint        → All checks passed! / 17 files already formatted
$ make typecheck   → 0 errors, 0 warnings, 0 informations
$ curl -s 127.0.0.1:5008/health → {"status":"ok","service":"org-brain-service","version":"0.1.0"}
$ git status --short → (empty)
```

## Found while testing
- First build failed: `pg_locale.h` includes `unicode/ucol.h`, so the build stage needs `libicu-dev`. Added; the runtime image is unchanged (it already has ICU).

## Not in this PR
Tables, RLS policies and the append-only trigger (issue 8). No `/ready` yet: nothing in the service reads the database until issue 8.
