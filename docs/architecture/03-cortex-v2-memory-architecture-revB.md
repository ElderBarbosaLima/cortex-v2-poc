# Cortex v2 Memory Architecture (Rev. B)

Source: Artifact claude.ai/artifact/PbNuU6DeA1ZdWnNdhHSGHU · 28 Sep 2026 · Proposal · For the Cortex v2 build team. Diagram flattened to text.

A target design for Cortex as governed organisational memory. Every record in one ledger carries its own provenance. Two gates control what gets in and who can read it, mandatory rules can't be outranked, and nothing is released on an obsolete approval.

## Architecture (left to right)
- **Sources (ranked by trust):** review decisions (Arbitr assess→decide), customer assets (TMs, glossaries, guides), connected systems (Drupal, Slack, DMS, CRM), agent and chat exhaust.
- **Admission gate (write-time governance):** tag & classify (tenant, scope, sensitivity, PII) → extract typed records (schema-validated) → resolve entities (canonical IDs; unsure → review) → admission policy (source trust × assurance level × conflicts) → review queue (grouped cases; owners approve).
- **Governed core:** memory ledger (system of record: append-only, versioned, bitemporal, RLS per tenant; kinds fact/rule/decision/content) + rebuildable projections: entity graph (edge tables), vector index (pgvector), lexical index (exact terms), projection builder (outbox).
- **Access gate (read-time governance):** authorise (caller, purpose, policy → query filters) → plan & retrieve (entity-scoped hybrid search, as of time T) → add constraints (every applicable rule by entity and scope) → resolve (constraints never lose; then status › scope) → adjudicate residue (optional rules only; labelled; raises a case) → context pack (records, citations, pack ID, stored).
- **Consumers (read only through the gate):** Arbitr workflows (assess, decide, publish), agents (Agent Studio, Slack agent, Sage), Public API & MCP (customers' own AI tools); **release check** re-verifies versions before publishing.
- **Lifecycle:** consolidate (propose merges), supersede (close, never delete), recertify (owner review by date), evolve schema (propose→approve), erase (cascade by lineage), evaluate (adversarial eval sets).
- **Evidence:** served packs (exact pack stored per read with caller, purpose), replay (return stored pack, no model re-run), export (EU transparency, SOC 2, customer audits).
- **Foundation:** tenant isolation with RLS and per-tenant keys; encryption in transit/at rest; database audit logging; tracing and cost per query; versioned schemas, policies and models.
- **Writeback loop:** reviewed outputs return to Cortex as decision records.

## One envelope for every memory — `memory_record`, 19 fields
record_id (ULID) · tenant_id (RLS) · kind (fact | rule | decision | content) · schema_version · body · entities[] · scope (team/market/language/channel) · source (system/document/location) · asserted_by (person, agent, import) · approved_by (reviewer + decision ID, or empty) · status (proposed | approved | stale | deprecated | superseded | erased) · enforcement (mandatory | prohibition | default) · authority (precedence rank, e.g. legal > brand > team > inferred) · assurance_level · valid_from/valid_to (business time) · recorded_at (system time) · supersedes · derived_from[] (lineage) · sensitivity (class, personal-data flags, retention).

Four kinds: **fact** (semantic: what is true), **rule** (procedural; carries enforcement, e.g. prohibition global: never say "clinically proven"; default de-DE: address with Sie), **decision** (episodic: e.g. reviewer rejected "free trial" on French page because offer ended), **content** (source material, e.g. approved en-US→ja-JP TM segment). Operational state (connector settings, job status) stays out of Cortex.

What each field enables: enforcement+entities+scope → applicability path (mandatory rules without similarity search); status+authority+scope → deterministic resolution of optional rules; valid_from/to+status → release check and expiry; derived_from → erasure and index rebuilds; tenant_id+scope+sensitivity → authorisation filters; assurance_level → admission policy.

## Six rules
1. Governance is data (provenance, status, scope, validity on every record).
2. One system of record (ledger authoritative; graph/vector/lexical are rebuildable projections).
3. Supersede, don't delete (lawful erasure is the only delete, following lineage).
4. Constraints can't lose (mandatory rules and prohibitions found by applicability, not similarity; no ranking or model outranks them; a model only settles optional-rule conflicts).
5. Check again at release (each pack stored as served and bound to its output; versions re-checked before publishing).
6. Machines propose, people approve (admission, merges, schema changes go to owners, scaled by assurance level).

## What changed from the four-layer draft
System of record: one ledger vs vector DB + graph as peers · Governance: admission gate + access gate vs routing pre-filter · Provenance: on every record vs not modelled · Stale facts: superseded/labelled vs pruned · Conflicts: mandatory can't be outranked, then precedence, model only for optional vs model reasoning gates · Mandatory rules: separate applicability path vs similarity · Release: re-checked before publishing vs not covered · Schema changes: drafted by models, approved by owners vs rewritten automatically · Main input: Arbitr review decisions (already governed) vs unstructured ops data · Entity resolution: own admission step vs not addressed · Exact terms: lexical index vs similarity · Evidence: stored/replayable/exportable/erasable vs not covered.

## Build sequence (each stage has a proof before the next starts)
- **Stage 1 — Govern one real workflow:** ledger with explicit admission authority; tenant and scope controls; mandatory constraints and applicability path; exact served-pack capture; basic expiry; tested manual erasure incl. stored packs; v1 migration compatibility; small adversarial eval set. *Proof:* a reviewer's approved correction improves the next applicable job, stays out of unrelated work, remains attributable.
- **Stage 2 — Prove controlled use:** context bound to outputs and release decisions; release check and conflict stops; freshness and revocation; projection consistency under lag; incident tracing and evidence export. *Proof:* a withdrawn claim or changed output can't pass through controlled publishing on an obsolete approval.
- **Stage 3 — Expand consumption and sources:** agents, Public API and MCP with explicit permissions and freshness behaviour; selected connectors; graph where justified. *Proof:* new consumers can't bypass controls; source permission changes propagate within a tested commitment.
- **Stage 4 — Automate operations:** recertification, consolidation, broader schema evolution, automated erasure, optimising bottlenecks. *Proof:* review workload and cost stay sustainable while quality holds.
The evidence export tied to the 2 December EU transparency date depends on stages 1 and 2. Tenant and scope filtering and deterministic entity keys stay in stage 1.

## Acceptance tests for sign-off (on the stage 1 workflow; also seed the adversarial eval set)
1. Admin imports a TM containing an expired offer → import does not silently renew approval or make the offer publishable (Tag & classify, Customer assets).
2. A local instruction conflicts with a global prohibition → prohibition cannot lose via scope ranking or model adjudication (Resolve).
3. A mandatory restriction is missing from similarity results → applicability path still supplies and enforces it (Add constraints).
4. A claim is withdrawn while a job awaits publication and the projection worker is behind → release check detects or stops safely (Release check).
5. A context pack is requested after model or index changes → stored served pack is returned, without pretending a fresh run is identical (Served packs, Replay).
6. One conflict encountered by hundreds of jobs → one shared review case, affected work visible (Review queue).

## Start on one database
Stages 1–2 fit one Postgres deployment (IBM Cloud Databases for PostgreSQL, **15 or later**): ledger/edges/RLS; pgvector (from PG15); Postgres full-text; pgaudit (preloaded); originals in object storage; projections and jobs via outbox table + worker queue; policy as code versioned with the ledger; served packs in an append-only hash-chained table with per-tenant retention.

## Open questions for the build team
Which workflow is the stage 1 slice and who reviews it? What does Cortex v1 store today and how does it move into the ledger without breaking reads? Which entity types make ontology v1? Who owns review for each domain, and who is fallback? Which assurance levels allow auto-admit, for which kinds? What retention and erasure commitments do contracts already make?
