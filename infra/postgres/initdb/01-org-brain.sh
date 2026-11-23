#!/usr/bin/env bash
# First-boot setup for the org_brain database (runs once, as the postgres superuser).
#
# Two roles, because RLS does not bind a table's owner (docs/discovery.md 6.3):
#   org_brain_owner  owns the database and schema; runs migrations.
#   org_brain_app    what the service connects as; owns nothing, cannot bypass RLS.
# Table-level grants for org_brain_app come with the migrations (issue 8).
set -euo pipefail

: "${ORG_BRAIN_OWNER_PASSWORD:?ORG_BRAIN_OWNER_PASSWORD must be set}"
: "${ORG_BRAIN_APP_PASSWORD:?ORG_BRAIN_APP_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v owner_pw="$ORG_BRAIN_OWNER_PASSWORD" -v app_pw="$ORG_BRAIN_APP_PASSWORD" <<'SQL'
CREATE ROLE org_brain_owner LOGIN PASSWORD :'owner_pw'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE org_brain_app LOGIN PASSWORD :'app_pw'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
SQL

# org_brain is this deployment's ledger (Japan). org_brain_hq stands in for a second
# region's ledger: the regions prototype (ADR 0002, issue 32) replicates its
# published Global records into org_brain. Same schema, same roles, same RLS.
for db in org_brain org_brain_hq; do
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v db="$db" <<'SQL'
CREATE DATABASE :"db" OWNER org_brain_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO org_brain_owner, org_brain_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<'SQL'
-- Extensions need a superuser; create them here so migrations never need one.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_bigm;
ALTER SCHEMA public OWNER TO org_brain_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO org_brain_app;
SQL
done
