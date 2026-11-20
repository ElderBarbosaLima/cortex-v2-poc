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
CREATE DATABASE org_brain OWNER org_brain_owner;
REVOKE ALL ON DATABASE org_brain FROM PUBLIC;
GRANT CONNECT ON DATABASE org_brain TO org_brain_owner, org_brain_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname org_brain <<'SQL'
-- Extensions need a superuser; create them here so migrations never need one.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_bigm;
ALTER SCHEMA public OWNER TO org_brain_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO org_brain_app;
SQL
