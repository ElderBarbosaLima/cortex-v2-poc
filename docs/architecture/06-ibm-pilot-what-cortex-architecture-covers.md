# IBM pilot: what the Cortex architecture covers (requirements vs Cortex v2)

Source: Artifact claude.ai/artifact/T16im8AJKH8szH2Q6CjLyy · "IBM Japan pilot · requirements vs Cortex v2" · Working draft, 16 Nov 2026 (authors JC and E)
Based on the IBM AskHR / AskQ2C kickoff notes of 11 Nov 2026. Requirements still under review: every row can change after the **25 Nov deep dive** and the **design workshop**. The page showed "14 open comments" — not in this copy. Status calls are the author's reading of the design, not engineering sign-off. IBM figures as reported by IBM or at the kickoff.

**This is the newest statement of the IBM pilot scope. It replaces Workflow A (Japan launch) and Workflow B (AP exceptions) from doc 05 with AskHR and AskQ2C.**

## How to read the status
- **Designed**: in the Cortex v2 design, not built. Only Cortex v1 is built today. **For the pilot, v2 is built through stage 3.**
- **Partly**: design covers some; the rest sits elsewhere or is undecided.
- **Not covered**: nothing in the design does this (zero rows in either workflow).
- **Needs IBM answer**: not an architecture question.

## The four build stages (Memory Architecture Rev. B)
1. Govern one real workflow: ledger, admission authority, tenant/scope controls, mandatory rules, stored context packs, basic expiry, manual erasure.
2. Prove controlled use: context bound to outputs, release check, freshness and revocation, evidence export.
3. Expand consumption and sources: agents, Public API and MCP, selected connectors, explicit permissions.
4. Automate operations: recertification, consolidation, schema changes, automated erasure.

---

## Workflow 1: AskHR (next phase; global approvals running in parallel)
IBM's employee HR assistant on watsonx Orchestrate. arbitr's pitch: reduce human intervention in Japan, not only answer correctness.

### A. The workflow today
1. Employee asks: ~18,000 IBM Japan users type a question into AskHR.
2. Routed: an LLM classifies the question to an HR domain (Benefits, Payroll...).
3. Answer or task: runs a task or answers from ~7,000 policy pages in OpenText (role-based). Correctness isn't measured, only where the chat ended.
4. "Complex case": an employee who gives up types it to reach a person. Escalation is chosen by the user, not rules.
5. Human callback: HR calls back within 24h. Japan a little under 90% automated vs 94% globally.

**Where arbitr steps in:** hold hand-off rules, required policy rules and the currently valid version of each policy (every language) as approved records. AskHR reads them as context. Each exception goes to a named owner with context attached; exact context used is stored as evidence.

### B. IBM needs vs architecture (6 designed, 3 partly, 0 not covered, 2 needs IBM answer)
| What IBM needs | Status | How Cortex v2 handles it | Stage |
|---|---|---|---|
| Reduce human intervention in Japan: replace the "complex case" bailout with explicit hand-off rules | Partly | Cortex holds hand-off rules as approved records and attaches context. Routing happens in AskHR/Orchestrate, not Cortex. | 1 records; agent reading is 3 |
| Required policy rules never missed (AskHR answers from ~7,000 pages by search rank) | Designed | Found by applicability, not search; no ranking or model can drop them. | 1 |
| Never serve an outdated policy (errors caught after the fact by monitoring/thumbs-down) | Designed | Validity dates; withdrawn versions superseded and filtered at read time. | 1–2 |
| Japan policy stays in sync with global policy and translations (AskHR runs in 31–52 languages) | Partly | Translated versions are records linked to the approved source, scoped by market and language. How Japan-specific policy is authored/synced isn't designed. | 1–2 |
| Respect role-based access to HR data (OpenText roles) | Designed | Access gate filters by caller and purpose; source permissions inherited, never widened. Linking to IBM's identity system isn't designed. | 1, identity link open |
| Handle highly sensitive HR cases (misconduct, low performance → HR leader) | Partly | Sensitivity classes, restricted scopes, erasure by lineage exist in design; nothing specific to HR's most sensitive cases. | 1 |
| One governed context across agents (planned AskHR + OpenText Aviator + SAP Joule, AskIBM on top) | Designed | One ledger every agent reads through the same access gate. | 3 |
| Not a second document repository (OpenText stores/retrieves HR docs) | Designed | Records point to originals; arbitr governs which version is approved/valid, doesn't store documents. | 1 |
| A record of what the agent used (no public decision tracing for AskHR) | Designed | Exact context pack stored per read; replay and export. | 1–2 |
| Global security and approvals (AskHR is global; IBM Japan opening talks) | Needs IBM answer | Process, not architecture. | — |
| A Japan baseline (escalation share and reason for <90% vs 94% unknown) | Needs IBM answer | Needed before targets can be set. | — |

---

## Workflow 2: AskQ2C (PoC starts here; shadow mode early Dec, live Jan)
Full quote-to-cash scope at IBM Japan: contracting, invoicing, AR collection. First area: customised invoices, the 1% still done by hand.

### A. The workflow today
1. Terms set: contract or PO sets the customer's invoicing terms.
2. Invoice generated: ~99% of IBM Japan invoices fully automated.
3. Falls out: 1% become custom invoices; trigger unknown; monthly count unknown.
4. Done by hand: staff apply customer's format and supporting documents; each requirement re-learned by hand — "takes a lot of time".
5. Submitted: via customer portals such as Ariba and Coupa; rejections, disputes, reissues follow.

**Where arbitr steps in:** a reviewer's correction ("customer X needs this field") becomes a proposed rule; once a named IBM owner approves it, it's added to every invoice for that customer, every time. In **shadow mode (early December)** arbitr runs in a sandbox so both sides see IBM's data, changing nothing in IBM systems.

### B. IBM needs vs architecture (6 designed, 3 partly, 0 not covered, 2 needs IBM answer)
| What IBM needs | Status | How Cortex v2 handles it | Stage |
|---|---|---|---|
| An approved customer requirement applies every time (core of customised-invoice case) | Designed | Writeback loop: correction → proposed rule → owner approves → access gate adds it to every matching invoice. | 1 |
| No required rule is ever missed (the only pass/fail): contract clauses, IBM finance policy, Japan's Qualified Invoice System | Designed | Mandatory rules can't be outranked; missing ones added by applicability. Rule inventory must come from the workshop. | 1 |
| Shadow mode: sandbox to see the data (early Dec; no changes to IBM systems) | Partly | Isolated tenant per customer designed. Getting IBM data in relies on import/manual entry (no connector designed for PoC). IBM Cloud Japan region unconfirmed. | 1 |
| Measure arbitr's contribution (one of six scorecard dimensions vs IBM baseline) | Needs IBM answer | Baseline from IBM data in the sandbox. Outputs stored with context used, so comparable. | — |
| Personal data under Japan's privacy law (names, addresses, contacts) | Partly | PII screening, per-tenant isolation, retention, erasure designed. Japan-region hosting unconfirmed. | 1 |
| Reachable from AskQ2C / watsonx Orchestrate (live in Jan if shadow passes) | Designed | MCP and API access are stage 3, being built for the pilot. | 3, built for pilot |
| Evidence to watsonx.governance | Designed | Evidence export + watsonx.governance adapter in IBM Cloud design. Whether IBM uses it for Q2C is open. | 2 |
| Read requirements from where they live (unconfirmed: SAP fields, contracts, spreadsheets, email, staff knowledge) | Needs IBM answer | MCP/API ready for pilot; IBM must confirm which systems hold requirements. | 3, built for pilot |
| Rules and evidence readable by Japanese approvers (IBM Japan owners approve each rule; Japanese volume unknown) | Partly | Owner reads proposed rule + evidence in **Decision Desk**, then approves. Japanese-language screen not designed. Records tagged by language, so a rule can have a Japanese version linked to the approved original. Who translates an English correction undecided. Nobody at arbitr assigned to review Japanese output in the sandbox. | Open |
| Approvers with limited weekly time (IBM owner + backup approve rules) | Designed | One review queue with shared cases; review volume measured from stage 1. Capacity is IBM's to commit. | 1 |
| IBM keeps ownership of its rules (commercial term) | Designed | Customer-owned, tenant-isolated memory with erasure by lineage. | 1 |

---

## Across both workflows: one gap to raise with the developers now
**Which IBM systems to connect.** MCP and API will be ready. We don't know where Q2C requirements live. SAP is likely (public IBM job posting), plus contracts and email. IBM must confirm before integrations are planned.

## Open questions
### AskHR
- Why is Japan a little under 90% when global is 94%: definition or real shortfall? (IBM Japan)
- What share of escalations come from the "complex case" bailout vs routing? (IBM Japan)
- Which global teams own approval, and can AskHR localization contacts shorten the path? (IBM Japan, pilot owner)
- How are Japan-specific policies authored and kept in sync with global policy and translations? (IBM Japan)
- Do hand-off rules live in Cortex and get executed by AskHR, or does arbitr route the case? (Nake, our developers)
- How does AskHR's identity/role system map to Cortex's access gate? (Engineering, IBM)
### AskQ2C
- Which systems hold customer invoicing requirements: SAP, contracts, spreadsheets, email? (IBM Japan)
- How many custom invoices a month, how many staff hours? (IBM Japan)
- What makes an invoice leave automation? (IBM Japan)
- Is AskQ2C on watsonx Orchestrate, and does the PoC agent extend it or run beside it? (IBM Japan)
- Can arbitr be reached from Orchestrate over MCP or API? Network, identity or approval constraints? (IBM Japan, our developers)
- Is watsonx.governance used for Q2C agents today? (IBM Japan)
- Is IBM Cloud's Japan region acceptable, and what retention is required? (IBM Japan security)
### Architecture
- Was the 28 Oct "architecture overview" sent to IBM Japan the Cortex v2 on IBM Cloud artifact? (Pilot owner)

## Sources cited by the page
IBM AskHR and AskQ2C: Public Intelligence and Gaps (kickoff notes, 11 Nov 2026; not in this project) · Cortex v2 Memory Architecture Rev. B (doc 03) · Cortex v2 on IBM Cloud Rev. C (doc 04) · Agent Studio v1 Handoff (gateway and MCP, 3 Nov 2026; not in this project).
