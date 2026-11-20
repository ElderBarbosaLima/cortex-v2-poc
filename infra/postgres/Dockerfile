# Cortex PoC Postgres: PostgreSQL 16 + pgvector + pg_bigm (Japanese bigram search).
# Not an AWS service and never goes through the emulator. On AWS the same
# extensions exist in RDS for PostgreSQL (pg_bigm 1.2_20250903 on PG 16.12+);
# on IBM Cloud Databases pg_bigm is not listed (docs/discovery.md, R5).
#
# Image tag: cortex-poc/postgres:16-pgvector0.8.7-bigm1.2.20250903

ARG BASE=pgvector/pgvector:0.8.7-pg16-trixie@sha256:c28c0b700f3f1b5eec14082b31ad6e7c8a566d14386323609c615315e7c0ffc8

FROM ${BASE} AS bigm-build
# pg_bigm needs server headers from 16.12 or later for its latest fix; the base
# ships 16.16 and PGDG's postgresql-server-dev-16 matches it.
ARG PG_BIGM_VERSION=1.2-20250903
ARG PG_BIGM_SHA256=4d4fb48161e3f9e99207851299379c83b3e5d1d3c1a07c35f6a3153d577b3142
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl libicu-dev postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o pg_bigm.tar.gz \
        "https://github.com/pgbigm/pg_bigm/archive/refs/tags/v${PG_BIGM_VERSION}.tar.gz" \
    && echo "${PG_BIGM_SHA256}  pg_bigm.tar.gz" | sha256sum -c - \
    && tar xzf pg_bigm.tar.gz --strip-components=1 \
    && make USE_PGXS=1 \
    && make USE_PGXS=1 install DESTDIR=/out

FROM ${BASE}
COPY --from=bigm-build /out/ /
COPY infra/postgres/initdb/ /docker-entrypoint-initdb.d/
