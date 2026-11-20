# 1. Two test tiers: hermetic unit tests and an ephemeral integration stack

Date: 2026-11-20

## Status

Accepted for the PoC only. Do not carry it into the monorepo without discussing it with the Cortex squad.

## Context

The monorepo rule (`CLAUDE.md`, "Tests — NEVER touch a live Postgres, Redis, or
third-party service") makes every default test hermetic: SQLite in memory,
fakes, `httpx.MockTransport`, a fail-closed socket guard. A real backend is the
last resort, behind `@pytest.mark.integration`, deselected by default.

The PoC's acceptance criteria depend on things SQLite cannot emulate: Postgres
row-level security and a non-owner role, the append-only trigger, pgvector,
pg_bigm, a real OPA answering Rego, and the AWS Emulator (S3, KMS, Secrets
Manager). Emulating those would test the emulation, not the property.

## Decision

- `make test` runs **only** hermetic unit tests, exactly as the monorepo does:
  `addopts = "-m 'not integration'"`, the arbitr-test-utils socket guard, and
  the container itself has no network (`network_mode: none`).
- `make test-integration` runs `@pytest.mark.integration` tests against an
  **ephemeral** test stack: a separate compose project (`cortex-poc-test`),
  throwaway volumes, no host ports. It never touches the dev stack's database.
- `make test-all` runs both (plus `opa test` once policies exist). It is the
  PoC's definition of "tests pass".

## Consequences

- A developer still gets a fast, offline suite from `make test`.
- Integration tests are where most acceptance criteria live, so they are not a
  last resort here. That is the deliberate difference from the monorepo.
- The integration stack must stay cheap to create and destroy; seeds are
  deterministic (see issue 6).
