# PR 009 — Ledger commands and reads "as of T"

Branch: `poc/009-ledger-commands` → `main` · Issue: [docs/tasks.md §9](../tasks.md#9-ledger-comandos-e-leitura-as-of-t) · Depends on: 008 (merged)

## What
- `app/services/ledger.py`, in the shape of the monorepo's `knowledge_commands`:
  - A status table (`_TRANSITIONS`). Terminal statuses are derived from it, not hand-listed.
  - Named errors: `IllegalTransitionError`, `ModelCeilingError`, `RecordNotFoundError`.
  - Every command **flushes and never commits**, and writes **one outbox row per ledger row** (`record.appended`) in the same transaction.

  | Command | What it does |
  |---|---|
  | `append_new` | version 1, `proposed` or `approved` (an approved record needs `approved_by`) |
  | `transition` | appends the next version with the new status; every other field is carried over. Only an approval may change `authority` or `enforcement` (the approver decides, P5) |
  | `supersede` | old record → `superseded`; a new `approved` record with `supersedes = old` |
  | `latest`, `as_of`, `is_valid_at`, `open_body` | reads |

- Transitions:

  | from | to |
  |---|---|
  | proposed | approved · deprecated (rejected, Q5) · superseded · erased |
  | approved | deprecated (retired) · stale (expired) · superseded · erased |
  | stale | approved (recertified) · deprecated · superseded · erased |
  | deprecated, superseded | erased |
  | erased | — |

- **Model ceiling:** a record asserted by `type: "model"` cannot hold authority > 10 unless a **person** (`approver_type: "user"`) approves it. A `policy` approval does not lift it.
- **Bitemporal reads:**
  - `as_of(record_id, system_time=…)` returns the highest version with `recorded_at <= system_time`, or `None` before the record existed.
  - With `business_time=…`, it also requires `valid_from <= t < valid_to` (`valid_to` exclusive).
- `app/services/body_codec.py`: a `BodyCodec` protocol, `PlainJsonCodec` (`plain-json/1`), and a canonical-JSON `body_hash` that is the same for every codec. Opening a row with a different codec than the one it names is refused.
- `tests/conftest.py`: shared `sqlite_session` fixture (fixtures live in conftest, monorepo rule).

## Acceptance criteria — output

**1. Unit: illegal transition → `IllegalTransitionError`; model with authority 20 → `ModelCeilingError`** (`tests/services/test_ledger_commands.py`, 14 tests)
- `test_illegal_transitions_are_refused[…]`: proposed → stale; deprecated → approved; erased → deprecated.
- `test_approval_requires_an_approver`, `test_only_an_approval_changes_authority`.
- `test_a_model_cannot_assert_above_the_ceiling`: nothing written.
- `test_a_person_approving_lifts_a_model_proposal_above_the_ceiling`: a `policy` approval does not lift it.

**2. Integration: supersede → new `record_id` with `supersedes`, old `superseded`, 2 outbox rows; a rollback leaves neither ledger nor outbox rows** (`tests/integration/test_ledger_commands_db.py`)
- `test_supersede_creates_a_linked_record_and_two_outbox_rows`: old record at version 2 `superseded`; the outbox grows by exactly 2.
- `test_a_failed_command_leaves_neither_ledger_nor_outbox_rows`: one successful `append_new`, then a ceiling violation, in one `tenant_session`. Ledger and outbox counts are unchanged afterwards.
- Also `test_another_tenant_cannot_transition_a_record`: under RLS, tenant B gets `RecordNotFoundError` for A's record.

**3. `as_of(t)` before a transition returns the old version (system time) and respects `valid_from`/`valid_to` (business time)**
- `test_as_of_system_time_returns_the_version_held_then`: v1 `approved` at a time between the two transactions; v2 `deprecated` now; `None` before the record existed.
- `test_as_of_business_time_respects_the_validity_window`: inside the window → the record; the day before `valid_from` → `None`; exactly `valid_to` → `None`.

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/009`)
```
$ make test              → 61 passed, 22 deselected
$ make test-integration  → 22 passed, 61 deselected
$ make test-all          → … PASS: 4/4 … All test tiers passed
$ make lint              → exit 0
$ make typecheck         → 0 errors, 0 warnings, 0 informations (strict on app/)
$ make dev               → Stack up
$ git status --short     → (empty)
```

## Notes
- **Within one transaction, `now()` is the same for every statement**, so two versions written in one transaction share `recorded_at`. `as_of` breaks the tie by version, and the integration tests write in separate transactions on purpose, reading the DB clock between them.
- Size: 316 lines in `app/` and 366 in tests. The code is under the target; the test suite is larger because criteria 2 and 3 need real Postgres.
- Not here: the review queue (issue 18) and who may approve (OPA, issues 12–13). The commands take `approved_by` as given; authorisation happens before they are called.
