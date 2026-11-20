# Cortex v2 · high-level architecture (proposal)

Source: Artifact claude.ai/artifact/SHLq6qcyM7U6JykXaM7jj6 · Draft · Elder · 20 Nov 2026.
Baseline: uat 00940b2 · Rev. C · doc 06 · Execution Plan review notes (D1–D24).
This copy was transcribed from the published artifact (the "Cortex V2" Project text copy was not reachable from this machine). The diagram is flattened to text; nothing was added.

Proposal for the IBM Japan pilot (AskQ2C first, AskHR next), built through stage 3. Shows which repo each part lives in, what it runs on, and how each feature moves through the system.

Badges: **PROD** exists in production · **UAT** on UAT only, or partly built · **NEW** designed, not in code · **OPEN** decision or IBM answer pending · **PoC** IBM-side system.

## Diagram (boxes)

**IBM Japan · customer side**
- AskQ2C agent — watsonx Orchestrate + Q2C reviewers (PoC)
- AskHR — watsonx Orchestrate, next phase, 18k users (NEXT)
- IBM rule owner + backup approver — approves each rule
- IBM source data — SAP? contracts? email? systems: needs IBM answer (IBM?)
- watsonx.governance — receives evidence export; use for Q2C: open (IBM?)
- M365 Copilot — later, same MCP spec, not in any plan yet (LATER)

**cloud-product-y-mono**
- integrations-service :5007 — /v1/cortex REST + MCP; one spec → both (D15); consumer registry + OIDC (D1); tenant from identity, not URL; scopes cortex:read / :write (EXTEND)
- client-service :5003 — org tree · tenants · grants; arbitr-rbac resolver (UAT)
- project-orchestrator — Arbitr workflows (translation); Match / Review read Cortex (PROD)
- **services/org-brain-service :5008 · Cortex core**
  - Admission gate — classify · PII · sensitivity; extract typed records; resolve entities (payer, customer); policy: trust × assurance (CEL?) (NEW)
  - Review queue — grouped cases · owner + backup; D17: N cases, N approvers; corrector can't approve (NEW)
  - Ledger (memory_record) — append-only · supersede · as-of; v1 sidecar + journal bridged in (NEW)
  - Projection worker — outbox → rebuildable indexes; v1 outbox exists (PARTIAL)
  - Access gate — authorise: caller, purpose, scope; retrieve hybrid, as of time T; add mandatory by applicability; resolve: constraints never lose; pack: records, citations, trace ID (NEW)
  - Verify / release check — re-check pack vs ledger (D3); hold pattern from Assurance (UAT) (NEW)
  - Pack store + sealer — served packs, append-only; Merkle batch seals (D14) (NEW)
- Arbitr app — Decision Desk (Nuxt 4); Japanese UI: gap (NEW UI)
- api-gateway :5002 — JWT · /api/org-brain/* (PROD)
- libs/ — arbitr-rbac (UAT), cortex-contract (new), arbitr-redis, arbitr-languages (MIXED)
- evals (WS7) — 6 acceptance scenarios, golden set, synthetic JP data (NEW)

**Other repos (each its own Git repo)**
- arbitr-agents-mono: Agent Studio · Slack agent · APE — read through the access gate; stage 3, after the pilot path (PROD)
- cloud-tm-import-mono: tm-import-service — today: SQL straight into org_brain; v2: submit through admission (WS3) (CHANGE)
- product-y-docconverter-m48: docConverter (Java · Okapi) — reference-file extraction; reused by classify / extract (D7) (PROD)
- arbitr-ai-visibility: AI Visibility — own claim model · pgvector precedent; reuse vs absorb: undecided (OPEN)

**Red Hat OpenShift on IBM Cloud · infra: arbitr-cortex-infra (proposed)**
- PostgreSQL 16/17 — IBM Cloud Databases · org_brain DB; memory_record ledger · RLS (D2); served packs · outbox · review cases; pgvector + full-text · JP: pg_bigm? (EXTEND)
- Elasticsearch 8.15 — TM segments · glossary terms; kept by D12; clashes with PG-only design (PROD)
- Redis 8 Streams — v1 review / sign-off; events → admission; existing bus (PROD)
- Object Storage — IBM COS · originals + evidence; immutable retention (new); File Hub already on COS (EXTEND)
- Key Protect — per-tenant envelope keys; erasure = crypto-shred; + Secrets Manager (NEW)
- Model gateway — LiteLLM → watsonx.ai; extract · resolve · embed; D21: hosting per tenant (PROD)
- Footer: OpenTelemetry → Instana + Cloud Logs · IAM trusted profiles · Japan region for APPI: unconfirmed

## Edges by flow
- Flow 1 (admit & approve): IBM source data → admission gate (shadow-mode import); tm-import-service → admission gate (TMX import → gate); admission gate → review queue; IBM rule owner → Arbitr app (approves in Decision Desk) → api-gateway → review queue; admission gate → ledger (admit); review queue → ledger (approved); ledger → PostgreSQL (append); ledger → projection worker → Elasticsearch (rebuild indexes).
- Flow 2 (serve context): AskQ2C and AskHR → integrations-service (read) → access gate; project-orchestrator → access gate; access gate → PostgreSQL (applicability); access gate → Elasticsearch (TM / terms); access gate → pack store.
- Flow 3 (verify before posting): AskQ2C → integrations-service (verify) → verify (output + pack ID); verify → pack store (load pack).
- Flow 4 (learn from a correction): AskQ2C → integrations-service (writeback) → admission gate → review queue → owner approves in Decision Desk → ledger.
- Flow 5 (evidence & export): pack store → PostgreSQL (served packs); pack store → Object Storage (archive); Key Protect → watsonx.governance (export, D8).

## How each feature works
**Flow 1 · Write path — Admit & approve a rule** (Stage 1 · WS1, WS3)
1. Data arrives: shadow-mode import of IBM data, TMX imports, v1 review decisions on Redis Streams.
2. The admission gate tags tenant, scope, sensitivity and PII, extracts typed records and resolves entities.
3. Policy admits it, or opens one grouped case in the review queue.
4. A named IBM owner approves it in Decision Desk (Arbitr app → api-gateway).
5. The ledger appends a memory_record plus an outbox row in the same transaction. Nothing is updated in place.
6. The projection worker rebuilds pgvector / full-text in Postgres and the ES indexes for TMs and terms.
Built today: governance sidecar, journal, approve/supersede commands (PROD). New: ledger, queue, Decision Desk screens.

**Flow 2 · Read path · access gate — Serve context to an agent** (Stage 1 core, stage 3 for MCP · WS2)
1. AskQ2C (or AskHR later) calls a Cortex MCP tool or /v1/cortex with caller identity and purpose.
2. integrations-service resolves the consumer and end user. The tenant comes from identity, never from the URL.
3. Authorise turns caller, purpose, scope and sensitivity into filters; Postgres RLS enforces the tenant.
4. Retrieve runs hybrid search as of time T, then adds every mandatory rule by applicability, not similarity.
5. Resolve: constraints never lose; then authority and nearer scope. A model only settles optional leftovers.
6. The pack (records, citations, trace ID) is stored and its pack ID returned. Arbitr workflows use the same gate.
Built today: /v1 + MCP pattern, unused cortex:read scope, rbac resolver (UAT). New: every gate step and the routes.

**Flow 3 · Release check — Verify before posting** (Before IBM launch · WS2 · D3, D16)
1. Before posting an invoice or a payment match, AskQ2C sends its output and the pack ID.
2. Verify loads the stored pack and re-checks that each mandatory and prohibition record is still approved and current.
3. It answers pass, or hold with the reason. The hold reuses the Cortex Assurance pattern on UAT.
D3 decides whether IBM must call it. Without the call, mandatory rules are advisory only.

**Flow 4 · Writeback loop — Learn from a correction** (Pilot (writeback), launch (rule suggestion) · WS3, WS5 · D17)
1. A reviewer fixes a case. AskQ2C posts a writeback: AI proposal vs final, pack ID, idempotency key.
2. It enters as a decision record through the admission gate.
3. Repeated corrections propose a rule once there are enough distinct cases and approvers. The person who corrected can't approve.
4. The owner approves in Decision Desk. From then on the access gate adds the rule to every matching case.
This is the AskQ2C loop in doc 06. D18 (cash application vs custom invoices) changes only the WS5 domain pack.

**Flow 5 · Packs → audit — Evidence & export** (Stage 2 · WS4 · D8, D14)
1. Every served pack is kept append-only with caller, purpose and trace ID.
2. Packs are sealed in Merkle batches, so reads don't queue behind one hash chain per tenant.
3. Archives go to Object Storage with immutable retention. Replay returns the stored pack; nothing is re-run.
4. An adapter exports evidence to watsonx.governance or auditors. Erasure destroys the tenant's key (crypto-shredding).
The 2 Dec EU transparency date is still unconfirmed by legal.

## Repository split
Proposal: keep the Cortex core inside `cloud-product-y-mono` as `org-brain-service`. The house rules (make, one DB per service, no cross-service SQL), the UAT bridge branches and the RBAC/tenant promotion chain already live there, and a separate repo would add a second release train before the pilot. New repos only where the code is truly separate: infrastructure.

| Repo | Path / part | Role in v2 | Change | Workstream · decision |
|---|---|---|---|---|
| cloud-product-y-mono | services/org-brain-service | Cortex core: admission, ledger, access gate, verify, packs, projections | Most of the new code. v1 tables bridge into the ledger. | WS1–WS4 |
| cloud-product-y-mono | services/org-brain-service/domain_packs/q2c | Q2C domain pack: payer resolution, Japanese bank-name normaliser | New folder that plugs into the entity-resolution interface | WS5 · D18 |
| cloud-product-y-mono | services/integrations-service | /v1/cortex routes and Cortex MCP tools | Extend; generated from the shared spec | WS2 · D1, D15 |
| cloud-product-y-mono | libs/cortex-contract | The one spec: REST routes, MCP tools, SDK types | New lib | WS2 · D15 |
| cloud-product-y-mono | client-service + libs/arbitr-rbac | Org tree, tenants, grants | On UAT only. Must reach PROD first (promotion steps 1–3) | WS2 · WS0 |
| cloud-product-y-mono | Arbitr app (Nuxt) | Decision Desk and review queue screens | New screens; Japanese UI not designed | WS3 |
| cloud-tm-import-mono | tm-import-service | TMX import | Stop writing org_brain tables; submit through admission | WS3 |
| arbitr-agents-mono | Agent Studio, Slack agent, APE… | Consumers of context | Read through the access gate | Stage 3 |
| product-y-docconverter-m48 | docConverter | Reference-file extraction | Reused by classify / extract | WS3 · D7 |
| arbitr-cortex-infra (proposed) | Terraform IBM Cloud module + Argo CD | Clusters, databases, COS, keys, secrets | New repo; first check where today's Jenkins / K8s config lives | WS6 |
| arbitr-ai-visibility | — | Own claim model, pgvector precedent | No change until reuse vs absorb is decided | Open |

## Open items that would change this picture
- **D18** First workflow: cash application or custom invoices. Only the WS5 domain pack changes.
- **D12** Elasticsearch stays for TMs and terms, against the Postgres-only design.
- **D22** Policy engine (Rev. C says OPA; the Inventory points to CEL for rule conditions).
- **D3** Must IBM call verify before posting? Without it, mandatory rules are advisory.
- **D1** Consumer identity: Orchestrate service accounts and end users mapped to the access gate.
- **D21** Which models run where per tenant (embeddings, resolver, suggestions).
- **IBM** Japan region for APPI · which systems hold Q2C requirements · baseline numbers (D19).
- **PROD** PROD has no arbitr-rbac, tenants or Assurance yet; the pilot depends on that promotion.

Sources: Cortex v2 Inventory (18 Nov, UAT baseline), Cortex v2 on IBM Cloud Rev. C, Memory Architecture Rev. B, IBM pilot requirements (doc 06, 16 Nov), review notes on the Execution Plan (20 Nov). The Execution Plan itself was not read; D18–D24 are proposed numbers. Status calls are a reading of the docs, not engineering sign-off.

> **PoC note:** for this PoC, D18, D12 and D22 are decided by the PoC brief (both workflows; Postgres-only search behind a projection interface; CEL for conditions + OPA for authorization). See `docs/discovery.md`.
