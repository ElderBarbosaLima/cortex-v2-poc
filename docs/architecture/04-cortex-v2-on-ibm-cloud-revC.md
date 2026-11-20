# Cortex v2 on IBM Cloud (Rev. C)

Source: Artifact claude.ai/artifact/UXBotfnqUcC5gNGp4WDcNN · 28 Oct 2026 · Proposal · Platform: IBM Cloud · For the Cortex v2 build team.

**Note on this copy:** Rev. C reuses the Rev. B design unchanged — same architecture, same 19-field `memory_record` envelope, same six rules, same four stages, same six acceptance scenarios, same open questions. Read `03-cortex-v2-memory-architecture-revB.md` for those. This file keeps only what Rev. C adds or changes.

## Differences from Rev. B
- Each layer is annotated with an IBM Cloud service: Sources → connectors on OpenShift; Admission gate → OPA on OpenShift; Governed core → IBM Cloud Databases for PostgreSQL; Access gate → OPA + model gateway to watsonx.ai; Consumers → watsonx Orchestrate, API, MCP; Lifecycle → jobs on OpenShift + Cloud Object Storage; Evidence → Postgres + COS + watsonx.governance export.
- Foundation line adds: Red Hat OpenShift on IBM Cloud · Key Protect per-tenant keys · Secrets Manager · IAM trusted profiles · Cloud Object Storage · Instana and Cloud Logs · Terraform.
- Consumers now list "watsonx Orchestrate, Agent Studio, Slack agent — all read through the access gate".
- **PostgreSQL version: 16 or 17** (Rev. B said 15 or later; pgvector from PG15; pgaudit preloaded).
- "Arbitr" capitalised; "Review decisions from Arbitr".

## The stack on IBM Cloud
Cortex is built portable: the same container images, manifests and Postgres schema run on any supported cloud. Stages 1–2 fit a single Postgres deployment; split out a dedicated graph or vector engine only when measured latency/scale requires it.

| Layer | Service | Note |
|---|---|---|
| Platform | Red Hat OpenShift on IBM Cloud | Every Cortex service runs here as a container, same images and manifests |
| Ledger, edges, RLS | IBM Cloud Databases for PostgreSQL, 16 or 17 | One engine for the system of record |
| Vector index | pgvector in the same service | pgvector from PG15; pgaudit preloaded |
| Lexical index | Postgres full-text search and pg_trgm | Same database |
| Originals and evidence archives | IBM Cloud Object Storage with immutable retention | S3 API, one storage client |
| Jobs and projections | Postgres outbox and job queue | IBM Event Streams only if volume requires |
| Keys | IBM Key Protect, per-tenant envelope keys | Behind one key interface in the app |
| Secrets | IBM Cloud Secrets Manager via External Secrets Operator | |
| Workload identity | IAM trusted profiles | |
| Authorisation | Open Policy Agent | Policy as code, versioned with the ledger |
| Models | Model gateway to watsonx.ai and Model Factory models on vLLM | Changing provider is configuration |
| Embeddings | One pinned open embedding model, self-hosted | Keeps vectors comparable and portable |
| Governance export | Adapter to watsonx.governance | Evidence stays in Cortex; export is an adapter |
| Agents | watsonx Orchestrate, Agent Studio, Slack agent | All read through the access gate |
| Edge | IBM Cloud Internet Services | |
| Observability | OpenTelemetry to IBM Instana and IBM Cloud Logs | Instrumented once in code |
| Delivery | Terraform IBM Cloud module, OpenShift GitOps (Argo CD) | Cloud differences live only in this module |

Keep the Postgres extension set to what each supported cloud offers, and confirm versions in the regions you deploy to before building. (Source: IBM Cloud docs, managing PostgreSQL extensions.)
