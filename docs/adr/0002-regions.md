# 2. Regions: a deployment per region, with a published Global scope

Date: 2026-11-23

## Status

**Proposed. This is the PoC's working assumption, not a decision.** It needs WS6, Legal and IBM (Nake's review, 2026-11-22; issue 32). The PoC prototypes option (b) so the decision can be made on running code.

## Context

Nake asked: "If each region is its own deployment, how do Global records reach Japan?" Today the PoC is one deployment, and doc 12 leaves "Japan region for APPI" open.

Some records are company-wide: group policy, a prohibition that holds everywhere, a contract clause shared by every subsidiary. These must reach every region. Others must not leave their region: personal data under APPI, and anything a region's law or customer contract keeps local.

Four properties must hold in every option:
- mandatory rules and prohibitions come from the ledger, not from search (D12);
- a pack can be replayed exactly (stored packs);
- erasure shreds every copy;
- lineage (`supersedes`, `derived_from`) stays intact.

## Options

| | (a) One deployment, in Japan | (b) A deployment per region, plus a published Global scope | (c) Federation at read time |
|---|---|---|---|
| **How Global reaches Japan** | It is already there: one ledger | A job copies approved, non-personal Global records into each region, with a stable `record_id` and provenance | Japan's access gate asks the home region for each pack |
| **Residency (APPI)** | Japanese data stays in Japan. Other regions' personal data would have to come to Japan, which blocks them | Personal records never leave their region. Only approved, non-personal records move, and only out of the Global scope | Personal data stays home, but every pack is assembled across borders, and the gate must filter what it receives |
| **Latency** | Fine in Japan; slow elsewhere | Local everywhere. Packs read only the local ledger | Every pack waits on another region, and an outage there means no pack (fail closed) or an incomplete pack |
| **Erasure** | One place | Erasure in the home region propagates. The replica is crypto-shredded, with everything derived from it locally | One place, but stored packs in other regions hold a body that is not theirs to shred |
| **Lineage and replay** | Simple | `record_id` is stable, so packs and verify name the same record everywhere. A retirement reaches the other regions on the next run, so verify there lags by up to one interval | Replay depends on the other region's history being reachable |
| **Cost** | Lowest | One more job, plus one rule: a replica is read-only | Highest: cross-region availability on the critical path |

## Decision (proposed)

**(b).** Each region runs its own Cortex. The Global scope is published, and the other regions receive copies of it:
- **What moves:** records in the published scope that are approved and not personal, meaning `sensitivity.personal_data` is false and the class is `public` or `internal`. Nothing else ever leaves the home region: not proposals, rejections, drafts, review cases or packs.
- **The replica:**
  - It keeps the `record_id` and the content, re-sealed under the destination's own data key.
  - `asserted_by = {type: replication, id: region:<home>, origin: …}`.
  - `source.replica_of` names the home record, its version and its body hash.
- **A replica is read-only.** It changes only through the job. Retiring one locally answers `409 replica_read_only`, and the owner retires it in the home region.
- **Changes propagate:** stale, deprecated (retire), superseded and erased.
  - Erasure in the home region crypto-shreds the replica, and everything derived from it, in the destination.
  - A record that stops being publishable (for example, reclassified as personal) is erased from the other regions.
- **Idempotent:** a second run with nothing new changes nothing.
- **The lag is the cost.** Between two runs, verify in a destination does not yet see a home-region retirement. The interval must be short and monitored, and an alert on lag belongs to WS6.

## What the PoC prototype does and does not show

**It shows** (`app/services/replication.py`, `make replicate`, `tests/integration/test_regions.py`, 6 tests):
- a Global rule approved in HQ appears in a Japan pack as a mandatory constraint;
- retired in HQ, verify holds the Japan pack once replicated, and not before (the lag);
- personal, proposed and team-scope records never leave HQ;
- supersede and erasure carry across, and the replica's data key is shredded;
- a second run is a no-op;
- the replica is read-only.

**Simplifications, to resolve in WS6:**
- **One Postgres, two databases.** `org_brain_hq` stands in for the other region, and `org_brain` is Japan. In production these are two deployments, so the job needs a cross-region channel (pull from a read-only endpoint, or a signed export to object storage). It should never be a database link.
- **One KMS emulator and the same tenant id in both.** In production each region has its own KMS. The replica is already re-sealed under a destination key, never copied as ciphertext.
- **The published scope is the tenant root.** A real deployment would add an explicit `global` node, or a `published` flag owned by a named Global owner.
- **The same seed, scope tree and people in both regions.** Approvers in the home region are named in `approved_by`. Whether the destination must recognise them, or only trust the home region's attestation, is open.
- **The job runs on demand** (`make replicate`). In production it would be a scheduled worker with a lag metric.

## Consequences

- The ledger stays the only system of record in each region (D12), and replicas are ledger rows, so applicability, precedence and verify need no change.
- The tighten-only check (issue 31) also protects Global rules in Japan: a Japanese local rule cannot loosen a replicated prohibition when it comes in.
- Legal must confirm the `personal_data` and class rule as the residency boundary, and IBM must confirm which region its Phase 1 calls hit (Japan).
