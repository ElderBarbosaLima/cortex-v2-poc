# Cortex v2 PoC — Descoberta (Fase 1)

Data: 20 nov 2026 · Autor: Elder (com Claude) · Status: **respostas propostas** (seção 9); itens da 9.1 dependem de outras pessoas.

Este documento não contém código. Ele registra o que a PoC vai seguir, o que é decisão nova (sem precedente na arbitr) e o que ainda precisa de resposta.

**Fontes lidas**
- Arquitetura (O QUE construir): `docs/architecture/03` (Rev. B), `04` (Rev. C), `06` (escopo do piloto), `12` (proposta de alto nível; transcrita do artifact porque a cópia em texto do Project "Cortex V2" não está acessível desta máquina).
- Código (COMO deve parecer): snapshots read-only em `~/cortex-v2/repos-arbitr/` (ao lado do repo, fora do git da PoC; movidos de `~/Downloads` em 20 nov, Q2). Prefixos usados abaixo:
  - `PROD:` = `product-y-mono-PROD-main/` (cloud-product-y-mono `main` @ 68b0b4d)
  - `UAT:` = `product-y-mono-UAT/` (`uat` @ 00940b2)
  - `AIV:` = `arbitr-ai-visibility-main/` (@ 5de5f5a)
  - docConverter (`master` @ 32d339c, `uat` @ 7da1116): não foi relevante para a PoC (extração de arquivos de referência fica fora do escopo).
- Externas: docs do AWS Emulator (github.com/aws/aws-emulator, docs.aws.amazon.com/emulator), PyPI/GitHub das 3 libs CEL, releases do OPA, Regal, pg_bigm, pgvector, Terraform AWS provider, tabela de extensões do RDS e da IBM Cloud Databases. Tudo consultado em 20 nov 2026.

---

## 1. Domínio relevante: governança v1 → envelope `memory_record`

### 1.1 O que existe hoje (v1, `PROD:services/org-brain-service`)

| Conceito v1 | Onde | Resumo |
|---|---|---|
| Sidecar de governança | `app/models/governance.py`, ADR `PROD:docs/adr/0001-governance-as-a-sidecar-table.md` | Tabela `knowledge_governance`, chave `(org_id, memory_type, subject_obj_id)`. Não altera as 4 tabelas de memória (facts, terms, term_bases, translation_memories). **Ausência de linha = "não governado", não "inválido"**; o read path aplica default por tipo (`DEFAULT_AUTHORITY`). TM é governada por container, nunca por segmento. |
| Estados | `GovState` | `candidate`, `active`, `rejected`, `retired`, `superseded`. |
| Authority ladder | `Authority(IntEnum)` | `0 observation`, `10 preference`, `20 house_rule`, `30 mandatory_policy`, `40 pinned`; gaps de 10 para inserir degraus sem migração. |
| Teto do modelo | `MODEL_ACTOR_CEILING = Authority.PREFERENCE` (10) | Um ator modelo nunca escreve acima de 10, em qualquer confiança (`ModelCeilingError` em `knowledge_commands.propose`). |
| Origin tiers | `OriginTier` | `authored`, `reviewed_edit`, `connector_sync`, `imported_legacy`, `model_proposed`. |
| Tipo de aprovador | `ApproverType` | `user`, `org_attestation`, `policy`, `none`. `approver_type=user` exige `approved_by` não vazio; sem aprovador não há `approved_at`. |
| Journal | `app/models/journal.py`, migração `alembic/versions/014_journal_hardening.py`, guarda ORM `app/models/immutable.py` | `knowledge_events` append-only, gravado **na mesma transação** da mudança de estado. Trigger `BEFORE UPDATE OR DELETE` (porque o app conecta como dono da tabela, e REVOKE não vale para o dono) + listener ORM que bloqueia unit-of-work e bulk update/delete (para o teste em SQLite). |
| Comandos | `app/services/knowledge_commands.py` | `propose`, `adopt`, `promote`, `approve`, `reject`, `retire`, `supersede`, `reverse`. Tabela de transições `_TRANSITIONS[(command, state)] → state`; estados terminais derivados da tabela; cada comando grava estado + 1 evento e faz `flush`, nunca `commit` (o chamador é dono da transação). |
| Promotion policy | `app/services/promotion_policy.py` | Configuração versionada (`POLICY_VERSION = "v1"`), recorrência primeiro, confiança depois (`min_recurrence=12`, confiança só com recorrência ≥ 4); a promoção automática é uma decisão governada e journalada (`actor_type=policy`). |
| Permissões | `UAT:services/client-service/alembic/versions/20261008_095_seed_v1_role_catalog.py` | `memory.read`, `memory.write`, `memory.approve`, `memory.share`, `memory.contributions.read`. `memory.approve` só no papel `final_validator` (e `org_manager`/`tenant_admin`). |

### 1.2 Mapeamento v1 → envelope de 19 campos (Rev. B)

| Campo Rev. B | Origem v1 | Decisão na PoC |
|---|---|---|
| `record_id` (ULID) | `obj_id` UUID4 por sujeito | **Novo padrão** (ver 6.1): ULID gerado pela app, armazenado como `uuid` do Postgres. Na API sai como string UUID, respeitando a regra "UUID na fronteira". PK numérica interna `id` nunca sai do serviço. |
| `tenant_id` (RLS) | `org_id` (PROD); `tenant_id` = obj_id do nó tenant (só UAT, migração `UAT:.../alembic/versions/tenant_ownership.py`) | Segue o UAT: `tenant_id` = UUID do nó tenant. RLS por tenant (novo, ver 6.3). |
| `kind` | `memory_type` (fact/term/term_base/translation_memory) | `fact | rule | decision | content`. Regras de Q2C são `rule`; correções do revisor são `decision`; casos de entrada não entram no ledger (são estado operacional / dados do pack). |
| `schema_version` | — | Inteiro por kind + tipo de corpo do domain pack (ex.: `custom_invoices.rule/1`). |
| `body` | as próprias tabelas de memória | JSONB validado por Pydantic do domain pack. Para `rule`: texto, `target` (conflict key), `condition` (CEL) e efeito. Cifrado por tenant (ver 6.6). |
| `entities[]` | só par de idiomas | Lista de chaves canônicas, ex.: `customer:CUST-0042`. Preenchida pelo resolvedor de entidade do domain pack. |
| `scope` | TMs têm `scope_type/scope_id`; resto não | UUID de um nó da árvore do tenant (padrão `org_nodes` do UAT). Leitura resolve para cima (`path_up`), nunca para os lados ou para baixo. |
| `source` | parcialmente `origin_tier` | `{system, document, location}` + `origin_tier` v1 mantido como subcampo. |
| `asserted_by` | `knowledge_events.actor_id/actor_type` | `{type: user|model|policy|system|import, id}` (mesmo `ActorType` do v1). |
| `approved_by` | `approver_type` + `approved_by` + `approved_at` | `{approver_type, user_id, decision_id}` ou vazio; `decision_id` aponta para a decisão de aprovação da fila de revisão. |
| `status` | `GovState` | `candidate→proposed`, `active→approved`, `retired→deprecated`, `superseded→superseded`; `stale` (expirado) e `erased` são novos. **`rejected` não existe na Rev. B** → pergunta Q5. |
| `enforcement` | implícito em `authority ≥ 30` e em termos `required` | `mandatory | prohibition | default` explícito. |
| `authority` | ladder 0–40 | **Reusa a ladder v1** (0/10/20/30/40) e o teto do modelo em 10: um registro com `asserted_by.type=model` nunca tem authority > 10 até um humano aprovar. A Rev. B fala em "legal > brand > team > inferred"; mapeamos para a ladder em vez de criar uma segunda escala. |
| `assurance_level` | o mais próximo é recorrência/confiança da promotion policy | Enum pequeno (`unverified | reviewed | attested`). Na PoC só alimenta a política de admissão (regra sempre exige aprovação humana — regra 6). |
| `valid_from/valid_to` | ADR cita "effective dates", mas não há coluna | Novo (business time). |
| `recorded_at` | `created_at` / `knowledge_events.created_at` | System time, `timestamptz` UTC (o v1 grava UTC naive; ver nota em `journal.py`: na PoC usamos `timestamptz` para evitar a ambiguidade). |
| `supersedes` | `supersedes_obj_id` | Igual. |
| `derived_from[]` | evidência dos facts (`fact_edits`) | Lista de `record_id`; usado por erasure e por rebuild de projeções. |
| `sensitivity` | — | `{class: public|internal|confidential|restricted, personal_data: bool, retention_days}`. |

**Como os comandos v1 viram fluxo v2:** `propose` → admission gate grava `proposed` + abre caso na fila; `approve`/`reject` → decisão do dono nomeado na fila; `retire` → nova versão `deprecated`; `supersede` → novo registro com `supersedes` + versão `superseded` do antigo; `reverse` fica fora (stage 1 não pede). O journal v1 (`knowledge_events`) e o ledger v2 se fundem: no v2 **cada transição é uma nova versão imutável do registro** (ver 6.2), então o ledger já é o journal. Registros `kind=decision` são memória episódica (o que o revisor decidiu sobre um caso), não log de governança.

**Ponte v1 → v2 (migração de dados v1):** fora do escopo da PoC (brief). O mapeamento acima é o contrato que uma migração futura usaria.

---

## 2. Padrões que a PoC vai seguir (backend e frontend)

### 2.1 Repo e operação
- Layout espelhado: `services/org-brain-service/`, `libraries/` (é `libraries/`, não `libs/`, no monorepo — o artifact 12 diz `libs/`, o código diz `libraries/`), `frontend/arbitr/`, `Makefile`, `docker-compose.yml`.
- `make` para tudo (`PROD:CLAUDE.md`, `PROD:Makefile`): `make dev`, `make stop`, `make logs`, `make test`, `make test-service SVC=`, `make lint`, `make format`, `make typecheck`, `make migrate`, `make shell SVC=`, `make help`. **Diferença:** no monorepo `SVC_RUN` roda `pipenv run` no host; na PoC cada alvo chama `docker compose run` (o host só tem Docker).
- Compose: healthchecks (`pg_isready`, como `PROD:docker-compose.yml`), `depends_on: condition: service_healthy`, porta do host sobrescrevível por env (`${ARBITR_POSTGRES_PORT:-5432}`), uma rede.
- `.env.example` versionado, `.env` fora do git (`make init-env` no monorepo).

### 2.2 Backend (`PROD:services/org-brain-service`)
- Python 3.12, FastAPI + Uvicorn, SQLAlchemy 2 + Alembic, Pydantic v2 + pydantic-settings, httpx, structlog, Pipenv (`Pipfile` + `Pipfile.lock`), Ruff, Pyright, pytest + pytest-asyncio + respx.
- Estrutura `app/{config.py, main.py, dependencies.py, logging_config.py, models/, routers/, schemas/, services/, clients/, utils/, cli/}`; `create_app()` em `main.py`; middleware de request id (`app/utils/middleware.py`).
- Config: `OrgBrainConfig(BaseSettings)` com `SecretStr` para segredos (`app/config.py`).
- `Dockerfile`: `python:3.12-slim`, libs de `libraries/` copiadas para `/libraries/<nome>` e referenciadas por path editável no Pipfile, `pipenv install --deploy`.
- Imports sempre no topo (`PROD:CLAUDE.md`).
- Modelos: `Base(DeclarativeBase)`, `BigInt = BigInteger().with_variant(Integer, "sqlite")`, `TimestampMixin` (`app/models/base.py`); `_JsonCol = JSONB().with_variant(JSON(), "sqlite")`.
- Comandos de domínio: tabela de transições + exceções nomeadas (`IllegalTransitionError`, `ModelCeilingError`), funções que fazem `flush` e não `commit` (`knowledge_commands.py`).
- Append-only: trigger no Postgres + guarda ORM (`014_journal_hardening.py`, `immutable.py`).
- Outbox: modelo `Outbox` de `PROD:services/project-orchestrator/app/models.py:272` (`event_type`, `payload`, `status pending|dispatched|skipped|failed`, `attempt_count`, `claimed_at`, `dispatched_at`, `last_error`) + registro de handlers `app/pipeline/outbox_registry.py` (`@register_outbox_publisher`, `OutboxOutcome`). A PoC reusa a forma para o projection worker. `status=failed` faz o papel do `:failed` dos streams.
- Alembic: ids ≤ 32 caracteres, uma única head; checado por teste (`make lint-migration` existe no monorepo).
- API: UUID `obj_id` na fronteira; resolvers rejeitam numéricos (`PROD:CLAUDE.md`, seção REST).
- Identidade de consumidor: forma do `AuthedCaller` (`UAT:services/integrations-service/app/utils/auth.py:113`) e guarda `require_scope(...)` (`:241`); escopos `cortex:read`/`cortex:write` seguem o padrão `verify:read`, `audit:read`, `visibility:read`.
- Testes: `pyproject.toml` com `addopts = "-m 'not integration'"`, marker `integration`; `conftest.py` aponta env para porta fechada (`127.0.0.1:1`) e instala `install_socket_guard()` (`PROD:libraries/arbitr-test-utils`). Ver conflito em 6.8.
- Streams Redis: a PoC **não** usa Redis (brief: projeções e jobs no outbox do Postgres). A regra `arbitr:<service>:<action>:{input|output}` não se aplica; fica registrado.
- Idiomas: `arbitr-languages` (`PROD:libraries/arbitr-languages`). Sem client-service na PoC, o catálogo vem de um fixture JSON carregado no `LanguageCatalogueCache` (desvio documentado; só `ja-jp` e `en-us` são usados).

### 2.3 Frontend (`PROD:frontend/arbitr`)
- Nuxt 4.4 (`ssr: false`), Nuxt UI 4.6, Tailwind 4.2, Pinia 3, `@nuxtjs/i18n` 10 (locales `i18n/locales/en` e `ja`), Zod 4, TypeScript 6, Vitest 4, Playwright 1.59, ESLint 10 com `@nuxt/eslint` + `eslint-plugin-vuejs-accessibility` (`eslint.config.mjs`), `vue-tsc`.
- Scripts: `lint = eslint . && node scripts/check-v2-styles.mjs` (guard de brand v2: sem hex cru, sem paleta default do Tailwind).
- Telas Cortex em `app/pages/cortex/*.vue`, componentes `app/components/cortex/Cortex*.vue`, cliente de API em `app/utils/api/cortex.ts` (tipos `Cortex...`, `apiGet` de `~/utils/api/client`), stores em `app/stores/`.
- Nova tela: `app/pages/cortex/decision-desk.vue`, componentes `CortexDecisionDesk*.vue`, test ids `cortex-decision-desk-<element>-<control-type>`, texto "Cortex" na UI.
- Brand: skills `frontend/.claude/skills/brand-visual-v2` e `brand-voice` (sentence case, verbo primeiro). A PoC copia só o necessário (tokens/`app.config.ts` e o guard), não o app inteiro.
- Padrão de "hold" para o verify: Cortex Assurance no UAT (`UAT:services/project-orchestrator/app/services/assurance.py`, spec `UAT:docs/superpowers/specs/2026-10-22-cortex-assurance-score-design.md`) e util de decisão `UAT:frontend/arbitr/app/utils/assurance-decision.ts`. Só existe no UAT → usado como referência de forma, não como código copiado.

---

## 3. Mapa componente → AWS (ou container local) → suporte no AWS Emulator

**Resumo honesto:** a documentação do AWS Emulator **não publica suporte por operação** para S3, KMS e Secrets Manager. A fidelity score é publicada por release (README), mas os assets de score não foram legíveis, e o `manifest.jsonl` da evaluation suite (18 MB) não pôde ser lido. A única evidência por operação encontrada é negativa: **KMS `TagResource` responde `NotImplemented` (501)** (`issues/things-that-do-not-work/20261022T081518Z-kms-implement-kms-tagresource-...md`). Sem Docker nesta máquina (ver Q1) não deu para sondar. Por isso a issue 1 começa com uma **sonda executável** (boto3 + `terraform plan/apply`) que registra 200/501 de cada operação abaixo, e o plano só avança se ela passar.

| Componente (arquitetura) | Rev. C (IBM) | PoC | Operações usadas | Suporte no emulador | Alternativa se falhar |
|---|---|---|---|---|---|
| Ledger, RLS, outbox, review cases, served packs | IBM Cloud Databases for PostgreSQL 16/17 | Container Postgres 16 próprio (pgvector + pg_bigm) — não passa pelo emulador | — | n/a | — |
| Busca (projeções) | pgvector + FTS + pg_trgm | Mesmo Postgres: FTS `simple` + pg_bigm (japonês) + pgvector com vetores fixos de teste | — | n/a. pg_bigm existe no **RDS** (PG16: `1.2_20250903` nas minors 16.12+ segundo a tabela do RDS; confirmar a linha PG16 no momento da issue). **Não aparece** na lista de extensões da IBM Cloud Databases (amostra PG 16.6, página atualizada em 2 nov 2026) | Risco WS6 (seção 8) |
| Originais e arquivo de evidências | Cloud Object Storage com retenção imutável | S3 | `CreateBucket` (com `ObjectLockEnabledForBucket`), `PutBucketVersioning`, `PutObjectLockConfiguration`, `PutObject` (com retenção), `GetObject`, `HeadObject`, `GetObjectRetention` | **Não verificado.** Docs: só path-style; usar `127.0.0.1` (não `localhost`); data plane com fidelidade menor que control plane; release notes recentes corrigem `GetObject`. Object Lock: nenhuma menção | Se Object Lock der 501: bucket versionado + export append-only no app + gap registrado (seção 8, R3). Não contornar em silêncio |
| Chaves por tenant / crypto-shredding | Key Protect | KMS | `CreateKey` (sem tags), `CreateAlias`, `DescribeKey`, `GenerateDataKey`, `Decrypt`, `ScheduleKeyDeletion` (+ checar que `Decrypt` falha em `PendingDeletion`) | **Não verificado**, exceto **`TagResource` = 501**. Emulador não avalia key policies | Sem tags em chave KMS. Se `ScheduleKeyDeletion` não bloquear `Decrypt`: usar `DisableKey` e registrar |
| Segredos | Secrets Manager | Secrets Manager | `CreateSecret`, `PutSecretValue`, `GetSecretValue`, `DescribeSecret` | **Não verificado** | Script AWS CLI (Terraform) / nenhum para a app |
| Autorização | OPA no OpenShift | Container `openpolicyagent/opa` | REST `/v1/data`, Data API, `/health?bundles` | n/a | — |
| Workload identity / IAM | IAM trusted profiles | Políticas mínimas como **artefato** (`aws_iam_policy_document`, só renderiza JSON, sem chamada de API) | nenhuma | Emulador **nunca nega** request assinado com a própria chave; não avalia bucket/key policies → "funcionou" não prova permissão | — |
| Provisionamento | Terraform IBM Cloud module | Terraform em container, provider AWS via env | Ver 3.1 | **Não verificado** ("suporte a IaC varia por serviço") | Parar e propor script AWS CLI (brief) |
| Jobs e projeções | Postgres outbox | Postgres outbox | — | n/a | — |
| Modelos / embeddings | watsonx.ai via gateway | Fora do escopo (determinístico) | — | n/a | — |
| Observabilidade | Instana + Cloud Logs | Fora do escopo (structlog JSON no stdout) | — | n/a | — |

### 3.1 Chamadas que o Terraform faz por baixo (precisam entrar na sonda)
- Provider: `skip_credentials_validation`, `skip_requesting_account_id`, `skip_metadata_api_check` (evita STS/IMDS), `s3_use_path_style = true`; endpoints por `AWS_ENDPOINT_URL`; CA por `AWS_CA_BUNDLE` (provider usa o SDK Go v2, que a doc do emulador diz respeitar `AWS_CA_BUNDLE`; confirmar na sonda).
- `aws_s3_bucket` (+ recursos `_versioning`, `_object_lock_configuration`): no refresh o provider lê a família `GetBucket*` (ACL, CORS, website, versioning, accelerate, request payment, logging, lifecycle, replication, encryption, object lock, policy, tagging, ownership controls, public access block). Qualquer 501 aí quebra o `apply`/`plan` — é o risco mais provável.
- `aws_secretsmanager_secret(_version)`: `CreateSecret`, `DescribeSecret`, `GetResourcePolicy`, `PutSecretValue`, `GetSecretValue`.
- **KMS fica fora do Terraform:** chaves são por tenant e nascem em runtime (onboarding do tenant pela app). Isso também evita `TagResource`/`ListResourceTags` que o recurso `aws_kms_key` chama.
- `aws_iam_policy_document`: data source local, sem API.
- Estado: o emulador guarda tudo **em memória** e zera no stop → o state do Terraform não pode sobreviver ao emulador (ver R2).

### 3.2 AWS Emulator no compose (como os outros containers o acessam)
- **Imagem:** não há imagem publicada. Dockerfile versionado no repo, base `public.ecr.aws/amazonlinux/amazonlinux:2023-minimal` (multi-arch), instala o RPM fixo `aws-emulator-<versão>-1-<aarch64|x86_64>.rpm` de `downloads.aws.com/.../LTS/` escolhido por `TARGETARCH`, com verificação da assinatura `.sig` (chave `current.asc`, fingerprint `98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624`). `ENTRYPOINT ["aws-emulator", "-b", "0.0.0.0", "--telemetry", "off"]`, `HOME=/home/emulator`, `--memory=2g`. Versões: `versions.json` lista 88 versões (1.0.191.1 → 1.0.545.1), várias por dia → Q12.
- **Bind:** `-b 0.0.0.0` dentro do container; porta publicada no host só em `127.0.0.1:9297`.
- **Certificado:** autoassinado, regenerado a cada start, em `~/.aws/emulator/9297/ca.pem`. **Os SANs não estão documentados** (nem no código lido). Além disso a doc de S3 pede `127.0.0.1` no endpoint. Decisão proposta (independe do SAN): os clientes AWS (org-brain, terraform, sonda) usam `network_mode: "service:aws-emulator"` e `AWS_ENDPOINT_URL=https://127.0.0.1:9297`. Assim o hostname bate com o que a doc garante e a verificação TLS fica ligada. Consequência: as portas desses clientes (ex.: 5008) são publicadas no serviço `aws-emulator`, e eles alcançam Postgres/OPA pelo nome do serviço normalmente (o namespace de rede compartilhado continua na rede do compose). A sonda da issue 1 também roda `openssl x509 -noout -ext subjectAltName`; se o cert cobrir o hostname do serviço, dá para trocar por alias depois sem mudar a app.
- **Confiança e credenciais:** volume nomeado `aws-emulator-home` montado em `/home/emulator/.aws/emulator` (rw no emulador, **ro** nos clientes, mesmo path, porque o `config` gerado aponta para esse `ca_bundle`). Clientes: `AWS_CA_BUNDLE=/home/emulator/.aws/emulator/9297/ca.pem`, `AWS_SHARED_CREDENTIALS_FILE=.../emulator_credentials`, `AWS_CONFIG_FILE=.../config`, `AWS_EC2_METADATA_DISABLED=true`, `AWS_DISABLE_HOST_PREFIX_INJECTION=true`, `AWS_REGION=us-east-1`. Nenhuma chave em `.env` ou no código. Como as chaves mudam a cada start, os clientes leem os arquivos no boot e o compose reinicia os clientes quando o emulador reinicia (`depends_on: restart: true`).
- **Healthcheck:** `curl --cacert .../ca.pem https://127.0.0.1:9297/__admin/health` (200, corpo `ok`) — o curl entra na imagem (amazonlinux minimal não traz). `depends_on: condition: service_healthy` nos clientes.
- **Telemetria:** `--telemetry off` + `EMULATOR_TELEMETRY=off`.
- Aplicação usa boto3 normal configurado só por env; nenhum `if emulator` no código.

### 3.3 Resultado da sonda (issue 3, 20 nov 2026, emulador 1.0.545)
Relatório completo: [`emulator-probe-1.0.545.md`](emulator-probe-1.0.545.md). **Gate: PASS.**
- **Todas as operações obrigatórias funcionam**: S3 (inclusive toda a família `GetBucket*` que o Terraform lê no refresh), KMS (`CreateKey`, `CreateAlias`, `GenerateDataKey` com `EncryptionContext`, `Decrypt`, `ScheduleKeyDeletion`) e Secrets Manager (`CreateSecret`, `PutSecretValue`, `GetSecretValue`, `GetResourcePolicy`).
- **Object Lock funciona**: bucket com Object Lock, retenção COMPLIANCE padrão e por objeto, `GetObjectRetention`, e o `DeleteObject(VersionId)` sob retenção é **recusado** (`AccessDenied`). O fallback da Q11 (xfail) não deve ser necessário; R3 fica reduzido a "fidelidade do emulador ≠ prova no S3/COS real".
- **Crypto-shredding funciona**: depois de `ScheduleKeyDeletion` a chave fica `PendingDeletion` e o `Decrypt` do DEK embrulhado falha com `KMSInvalidStateException`. `DisableKey` também bloqueia (`DisabledException`). `Decrypt` com outro `EncryptionContext` falha (`InvalidCiphertextException`), o que permite amarrar cada DEK ao tenant/registro.
- **KMS `TagResource` deixou de ser 501** nesta versão (a issue de 22 out está desatualizada). Mesmo assim, as chaves KMS ficam fora do Terraform (são por tenant, criadas em runtime).
- **Certificado (R4 resolvido):** SAN = `localhost`, `127.0.0.1`, `::1`; a verificação de hostname falha para `aws-emulator`. Confirma a decisão de `network_mode: service:aws-emulator` + `https://127.0.0.1:9297` com TLS verificado.
- Credenciais e CA mudam a cada start (confirmado pela impressão digital da access key no relatório); os clientes releem do volume.
- O emulador roda como uid 10001 (o mesmo do usuário de runtime do org-brain) porque grava os arquivos com modo `0600` e os clientes precisam lê-los do volume montado `ro`.

---

## 4. Divergências `main` × `uat` relevantes

Regra do brief: se divergirem, `main` vence. Aplicação na PoC: **padrões de código vêm do `main`**; do `uat` entram só modelos de dados que o `main` não tem e que o Cortex v2 precisa, usados como **desenho** (nada é importado).

| Só no `uat` | Onde | Uso na PoC |
|---|---|---|
| `libraries/arbitr-rbac` (árvore de nós, grants, barriers, `path_up`, `authorize`, `reachable_nodes`, `zone_set`, `readable_tenants`, `restrict_to_tenant`) | `UAT:libraries/arbitr-rbac/src/arbitr_rbac/{models,resolver,tenant_scope,payload}.py` | Vira dados + Rego no OPA (seção 7). **Não** é importada no org-brain (evita duplicar regra de autorização em Python). |
| `org_nodes`, `access_roles`, `access_role_permissions`, `access_grants`, `organizations.access_tree_version` | `UAT:services/client-service/alembic/versions/20261008_094_org_nodes_and_grants.py`, `095_seed_v1_role_catalog.py`, `096_backfill_root_grants.py`, `20261012_097_org_tenants.py`, `098_rbac_v2.py` | Formato dos dados de escopo/grants carregados no OPA (seed do tenant sintético). |
| Tenants e recursos tenant-owned (`tenant_ownership_catalog`, settings, billing, keys/webhooks) | client-service `099`–`117` | `tenant_id` = UUID do nó tenant; API key por tenant (`102_tenant_keys_webhooks`) inspira a identidade estática da PoC. |
| `tenant_id` + `shared_with_org` em TMs/term bases/facts; `TENANT_OWNERSHIP_STAGE off|enforced` | `UAT:services/org-brain-service/alembic/versions/tenant_ownership.py`, `app/services/tenant_scope.py` | Hoje o isolamento é um filtro ORM (`do_orm_execute` + `with_loader_criteria`). A PoC troca por **RLS** (defesa no banco), mantendo o mesmo significado de "conjunto vazio = não lê nada". |
| `access_zone` (barrier) em segmentos/fact_edits; `rezone_jobs` | `UAT:.../alembic/versions/access_zones_rbac.py`, `app/services/access_zones.py`, `rezone.py` | Barriers entram no Rego; rezone fora do escopo. |
| Cortex Assurance (score, gates R1–R7, hold de entrega, `assurance_events` na mesma transação, outbox) | `UAT:services/project-orchestrator/app/assurance/`, `services/assurance*.py`, migrações `214_assurance_events`, `223_merge_assurance_uat` | Forma do resultado `pass | hold + reason` do verify. |
| Frontend: `CortexShareToggle.vue`, `components/cortex/assurance/`, stores `orgAccess.ts`, `accessDenial.ts` | `UAT:frontend/arbitr/app/...` | Referência de UX para negação de acesso; não copiado. |
| `arbitr-redis/grant_use.py` | `UAT:libraries/arbitr-redis` | Não usado (sem Redis). |

Outras divergências observadas (não afetam a PoC, mas mostram risco de merge): os grafos Alembic de org-brain e client-service **divergem** entre branches (ex.: `015_term_base_domain` no `main` × `017_term_base_domain` no `uat`; `071`–`092` do client-service com números trocados). Isso confirma a regra "uma head" e o aviso do `PROD:CLAUDE.md` sobre forks de head no `uat`. O `main` tem features de automação (Playwright/BDD em `automations/`) que o `uat` não tem.

---

## 5. Biblioteca CEL

Critérios do brief: wheels linux/arm64 e amd64; data e inteiros grandes; manutenção. Mais um critério nosso: **validar na admissão** (rejeitar CEL inválido).

| | cel-expr-python 0.1.4 | cel-python 0.5.0 | common-expression-language 0.10.0 |
|---|---|---|---|
| Mantenedor | org `cel-expr` (equipe CEL/Google), publicação com provenance | cloud-custodian, 3 mantenedores | 1 mantenedor (hardbyte) |
| Último release | 6 nov 2026 (0.1.3: jun 2026) | 31 jan 2026 | 15 set 2026 |
| Implementação | wrapper do **cel-cpp** (pybind) | Python puro | Rust (`cel` 0.14.5, PyO3) |
| Wheel cp312 linux aarch64 / x86_64 | sim (`manylinux_2_28`) / sim | `py3-none-any` | sim (`manylinux2014`) / sim |
| Checagem estática com variáveis declaradas | **sim** (padrão no `compile`) | não | não (só introspecção `Program.variables()`) |
| `int` 64 bits / overflow | semântica cel-cpp (erro em overflow, pela spec; não documentado no README) | `EvalError` em overflow | `OverflowError` |
| timestamp/duration | spec CEL (não documentado no README) | não documentado | sim (`datetime`/`timedelta`; datetime naive = hora local do host) |
| dicts aninhados (`invoice.customer_id`) | **não documentado** | sim (`json_to_cel`) | sim, direto |
| Funções custom (`date()`) | `FunctionDecl` + `Overload` | formato não documentado | `Context.add_function` |
| Riscos | versão 0.x; wheel de 18 MB; sem sdist | sem type check; release parado há ~10 meses | bus factor 1; sem type checker; sem `fold` |

Nota: **CEL padrão não tem `date()`**. As condições do brief (`invoice.issue_date >= date("2023-10-01")`) exigem uma função custom `date(string) -> timestamp` registrada pelo `ConditionEvaluator` (meia-noite em `Asia/Tokyo`), e o domain pack converte `issue_date` do caso para o mesmo tipo. Valores JPY são `int` (cabe folgado em int64); o contexto rejeita `float` antes da avaliação.

**Escolha: `cel-expr-python`**, atrás da interface `ConditionEvaluator` (`compile(expr, declarations) -> CompiledCondition`, `evaluate(compiled, context) -> bool`). Motivos: é a implementação oficial (mesmo motor do cel-go/cel-cpp, conformance da spec), tem wheels multi-arch e é a única que rejeita na **compilação** variável/função desconhecida e erro de tipo — exatamente o que a admissão precisa. Os 3 pontos não documentados (dict aninhado, timestamp, overflow) viram o **primeiro critério de aceite** da issue do avaliador (teste de caracterização). Se algum falhar, troca-se para `common-expression-language` dentro da mesma interface, com validação de variáveis via `Program.variables()`. Ver Q8.

---

## 6. Decisões sem precedente na arbitr (registradas antes de criar o padrão)

### 6.1 ULID como `record_id`
Sem precedente (a arbitr usa UUID4 em `obj_id`). Decisão: gerar ULID na app (`python-ulid`, puro Python) e gravar em coluna `uuid` do Postgres. Ordenação temporal útil para o ledger; continua sendo um UUID válido na API (regra da casa preservada).

### 6.2 Ledger append-only versionado
Tabela `memory_record` com PK `(record_id, version)` (+ `id` bigint interno); cada transição de status grava uma **nova versão** imutável; trigger `BEFORE UPDATE OR DELETE` como no `014_journal_hardening.py` + guarda ORM como `immutable.py`. "Versão corrente" = maior `version` por `record_id` (view). Consulta "as of T" usa `recorded_at <= T` (system time) e `valid_from <= T < valid_to` (business time). Supersede cria um novo `record_id` com `supersedes` e grava uma versão `superseded` do antigo. A **única** exceção ao append-only é erasure (6.6), que não apaga linhas: destrói a chave.

### 6.3 RLS por tenant
Sem precedente (hoje: filtro por router / `do_orm_execute` no UAT). Dois papéis: `org_brain_owner` (dono das tabelas, roda migrações) e `org_brain_app` (sem ownership, `NOBYPASSRLS`, usado pela app). `ENABLE` + `FORCE ROW LEVEL SECURITY`, política `tenant_id = current_setting('app.tenant_id')::uuid`, `SET LOCAL app.tenant_id` por transação a partir da identidade do consumidor. Sem `app.tenant_id` → zero linhas (fecha por padrão). O OPA não substitui o RLS. Teste obrigatório: leitura cruzada entre dois tenants devolve zero linhas mesmo com SQL direto.

### 6.4 Busca atrás de `SearchProjection` (D12)
Interface `SearchProjection` (`rebuild(tenant)`, `apply(outbox_event)`, `search(query, filters, as_of) -> candidate ids`). Implementação `PostgresSearchProjection`: tabela de projeção própria (não o ledger), FTS `simple` + índice `gin_bigm_ops` (pg_bigm) para japonês + coluna `vector` (pgvector, vetores fixos de teste). Alimentada só pelo outbox; `make reindex` apaga e reconstrói. A busca devolve **candidatos**; o access gate revalida cada um contra o ledger (status `approved`, vigência em T, tenant, escopo) antes do pack. Regras `mandatory`/`prohibition` **nunca** vêm da busca: vêm de uma consulta SQL de aplicabilidade (`kind=rule`, `enforcement in (...)`, `status=approved`, escopo ∈ `path_up(scope pedido)`, vigente em T) seguida da avaliação CEL.

### 6.5 Conflitos e resolução (cenário 2)
Cada regra declara um `target` (chave de conflito do domain pack, ex.: `invoice.registration_number`). Para o mesmo `target`: `prohibition`/`mandatory` vencem `default` sempre (regra 4); só então authority, depois escopo mais próximo. A regra local perdedora entra no pack rotulada `overridden_by: <record_id>` (atribuível), nunca aplicada.

### 6.6 Envelope encryption e crypto-shredding (KMS)
Uma chave KMS por tenant (alias `alias/cortex/tenant/<tenant_id>`), criada pela app no onboarding do tenant. DEK por tenant via `GenerateDataKey`, guardada cifrada no Postgres e cacheada em memória. Cifrado: `body` dos registros, snapshot dos packs e objetos exportados para S3. Em claro: metadados de governança (status, escopo, validade, enforcement, entities). Erasure de tenant = `ScheduleKeyDeletion` (+ descarte do cache de DEK) + apagar as linhas de projeção do tenant (projeções são texto em claro, reconstruíveis) + versão `erased` dos registros. Prova: depois do shred, `Decrypt` falha e o export não abre. Ver Q10.

### 6.7 OPA
Ver seção 7. Primeira vez de OPA e de Rego na arbitr. **Padrão adotado (issue 4, registrado antes do uso):**
- **Layout:** `policies/.manifest` (roots do bundle = `["cortex/policy"]`), `policies/cortex/policy/*.rego` (pacote `cortex.policy`), testes ao lado com sufixo `_test.rego` no pacote `cortex.policy_test`, `policies/.regal/config.yaml`. Rego v1 (padrão do OPA 1.x). Anotações `METADATA` no nível de regra, com `entrypoint: true` nas regras que o serviço consulta.
- **Bundle:** serviço one-shot `opa-bundle` (`infra/opa/build-bundle.sh`) roda `opa build --revision sha256:<16 hex>`, em que o hash cobre caminho + conteúdo de cada `.rego` (sem testes) e do `.manifest`. Mesma política → mesma revisão; qualquer mudança → nova. `make opa-reload` reconstrói e reinicia o OPA.
- **Revisão na decisão:** `data.cortex.policy.revision` lê `data.system.bundles[_].manifest.revision` do bundle que é dono de `cortex/policy`. As decisões reais (issue 12) devolvem essa revisão na mesma resposta.
- **Dados:** tudo fora de `cortex/policy` aceita a Data API (`PUT /v1/data/cortex/tenants/<id>` → 204); escrever dentro da root do bundle é recusado (400), então política e dados não se misturam.
- **Servidor:** imagem oficial distroless `openpolicyagent/opa:1.22.4` fixada por digest. O healthcheck usa o próprio binário (`opa eval --fail` com `http.send` contra `/health?bundles`), sem shell nem curl. O build do bundle usa a variante oficial `-debug` (tem `sh` e `sha256sum`), só no one-shot.
- **Qualidade:** `make lint` roda `opa check --strict` + Regal 0.43.0; `make test-policies` (e `make test-all`) roda `opa test`. Tudo em container sem rede.

### 6.8 Testes contra Postgres real (conflito com a regra hermética)
A regra da casa (`PROD:CLAUDE.md`, "Tests — NEVER touch a live Postgres") manda SQLite em memória por padrão e deixa `@pytest.mark.integration` como último recurso. Os critérios de aceite da PoC dependem de RLS, trigger, pgvector, pg_bigm e OPA, que o SQLite não emula. Proposta: dois níveis, os dois em container — (a) unit herméticos (SQLite/fakes/`httpx.MockTransport`, socket guard ligado, rodam sem a stack); (b) `integration` contra Postgres/OPA/emulador **efêmeros do próprio compose de teste** (projeto compose separado, volumes descartáveis), nunca contra o banco de dev. `make test` roda os dois. Ver Q3.

### 6.9 Terraform
Sem IaC na arbitr. **Padrão adotado (issue 5):** módulo `infra/terraform/aws/` (nome por nuvem, não por emulador: nada nele cita o emulador), Terraform 1.17.2 + provider `hashicorp/aws` 6.73.0 fixos, `.terraform.lock.hcl` com hashes `linux_arm64` e `linux_amd64` (`make tf-lock`). Roda no serviço `terraform` (compose, `network_mode: service:aws-emulator`), state local num volume que morre com o emulador, cache de providers num volume externo. Se o emulador reinicia, o refresh vê os recursos sumirem e o `apply` os recria (testado). Políticas IAM ficam como `aws_iam_policy_document` renderizado em `infra/iam/*.json` (revisável, nunca aplicado). Chaves KMS ficam fora: são por tenant e nascem na aplicação.

---

## 7. arbitr-rbac (UAT) → dados e políticas Rego

### 7.1 O modelo (arquivos)
- `UAT:libraries/arbitr-rbac/src/arbitr_rbac/models.py`: `Node(id, parent_id, name, type, is_barrier)`, `Grant(id, role, node_id, expires_at, revoked_at, principal_type, principal_id)`, `Role(code, permissions, tier)`, `Catalog.version` (hash sha256 das permissões), `Tree.path_up()`, `tenant_of()`. Famílias de conteúdo bloqueadas por barrier: `CONTENT_PERMISSION_FAMILIES` (inclui `memory`).
- `resolver.py`: `authorize(ctx, perm, node)` anda `path_up` do nó até a raiz; o grant ativo mais próximo decide (`nearest-allow` / `inherited-allow`); barrier encontrado no caminho bloqueia grants **acima** dele para permissões de conteúdo (grant no próprio barrier vale); expiração avaliada no instante da decisão (expira em `expires_at <= now`); veredictos `out-of-scope`, `no-permission`, `barrier`, `expired` com motivo. `reachable_nodes` faz o mesmo em uma passada.
- `tenant_scope.py`: `readable_tenants` = tenants que contêm um nó alcançável para a permissão; conjunto vazio = não lê nada; `restrict_to_tenant` confina uma API key ao seu tenant.
- `payload.py`: formato `GET /internal/rbac/access-context` (`nodes`, `grants`, `roles`, `tree_version`, `catalog_version`). Grants revogados não são enviados.
- Precedente de caminho materializado: `org_nodes.path` (094) "materialised obj_id chain used for ancestor checks".

### 7.2 Como vira dados no OPA (Data API, `PUT /v1/data/cortex/tenants/<tenant_id>`)
```json
{
  "tree_version": 3,
  "nodes": { "<node>": { "parent": "<node|null>", "type": "tenant|team|...", "is_barrier": false,
                         "path_up": ["<node>", "...", "<root>"] } },
  "roles": { "final_validator": ["memory.read", "memory.write", "memory.approve"] },
  "grants": [ { "id": "...", "principal_type": "user|consumer", "principal_id": "...",
                "role": "...", "node": "...", "expires_at": "2027-01-31T00:00:00Z" } ],
  "consumers": { "<consumer_id>": { "purposes": { "invoice_drafting": { "sensitivity_ceiling": "confidential" } } } }
}
```
`path_up` vai pré-computado (como `org_nodes.path`) para o Rego não precisar de recursão. Dados ficam fora das roots do bundle (o bundle só é dono de `data.cortex.policy`), senão o OPA recusa o PUT. O org-brain empurra os dados no start e a cada mudança do seed. Principais da PoC: o consumidor (API key estática do tenant, `principal_type=consumer` — o `PRINCIPAL_USER` do rbac já prevê outros tipos) e o usuário final (header).

### 7.3 Políticas (`policies/cortex/`)
- `authz.rego` — decisão de leitura. Input: `{consumer, end_user, purpose, tenant_id, scope_node, permission, now}` (`now` vem do chamador para teste determinístico e para "as of T"). Saída: `{allow, verdict, reason, readable_scopes: [...], sensitivity_ceiling, policy_revision}`. Tradução direta do `authorize`/`reachable_nodes` (nearest grant em `path_up`, barrier para `memory.*`, expiração). Consumidor e usuário final precisam os dois permitir (interseção) quando há usuário final.
- `approval.rego` — propor/aprovar/rejeitar: `memory.write` para propor, `memory.approve` no nó de escopo do registro para aprovar, **separação de funções** (`input.record.asserted_by != input.end_user`, D17 "corrector can't approve").
- `policy_revision` = `data.system.bundles["cortex"].manifest.revision`, devolvido **na mesma resposta** da decisão (sem corrida entre "qual versão" e "qual decisão"); revision = hash do conteúdo de `policies/` passado em `opa build -r`. Todo pack guarda esse valor.
- Falha fechada: timeout curto (httpx), qualquer erro/timeout/`result` ausente → 503 `access_gate_unavailable`, leitura negada, log estruturado; documentado como "modo degradado: sem leitura, sem aprovação".
- A decisão vira filtros SQL (`scope = ANY(:readable_scopes)`, `sensitivity <= :ceiling`); RLS continua garantindo o tenant.
- Testes: `policies/cortex/*_test.rego` cobrindo os veredictos do resolver (inclusive barrier e expiração exatamente em `now`), `opa test` + `regal lint` em container.

---

## 8. Riscos e premissas

**Riscos**
- **R1 — Docker não está instalado neste Mac** (`docker`, `gh`, `terraform`, `opa` ausentes; nenhum Docker Desktop/OrbStack/Colima em `/Applications`). A Fase 3 não começa sem isso. → Q1.
- **R2 — Estado do emulador é só memória e as credenciais/cert mudam a cada start.** Reiniciar o emulador apaga buckets, segredos e **as chaves KMS**; com Postgres persistente, os `body` cifrados ficariam ilegíveis (um crypto-shred acidental). Proposta: a stack de dev é efêmera e acoplada — `make dev` recria emulador, roda Terraform, recria o banco e semeia os dados sintéticos; `make stop` derruba tudo junto. → Q4.
- **R3 — Fidelidade por operação desconhecida**, em especial S3 Object Lock e a família `GetBucket*` lida pelo Terraform; KMS `TagResource` já é 501. Mitigação: sonda na issue 1/2; se falhar, parar e propor (brief).
- **R4 — SANs do certificado não documentados.** Mitigação: `network_mode: service:aws-emulator` + `127.0.0.1` (3.2).
- **R5 — pg_bigm não aparece na IBM Cloud Databases** (lista de extensões, amostra PG 16.6). Era "não confirmado"; agora é "não listado". Mantido como risco WS6, não bloqueio. Build: usar `v1.2-20250903` (a mesma que o RDS oferece), compilado com PGXS sobre `pgvector/pgvector:0.8.7-pg16-*`, contra headers PG16 ≥ 16.12. A tag pg16 visível do pgvector é **trixie** (Debian 13), não bookworm: confirmar `postgresql-server-dev-16` no build.
- **R6 — `cel-expr-python` 0.x** com comportamento não documentado (dicts, timestamp). Mitigação: teste de caracterização + troca atrás de `ConditionEvaluator`.
- **R7 — Imagens multi-arch não confirmadas:** `openpolicyagent/opa:1.22.4` (as tags `-static` são multi-arch, mas estão deprecadas na 1.22), `ghcr.io/open-policy-agent/regal:0.43.0`, `hashicorp/terraform:1.17.x` (arm64 não visível na página). Verificar com `docker manifest inspect` na issue 1; fallback: Dockerfile mínimo com o binário oficial `linux_arm64`/`amd64` + checksum.
- **R8 — Healthcheck do OPA:** a imagem padrão provavelmente é distroless (sem shell/curl/wget; não confirmado). Fallback: tag `-debug` (busybox) fixada por digest, ou healthcheck feito por um sidecar curl. Decidir na issue 1.
- **R9 — Escopo vs plano anterior:** o Work Plan de 5 out adia RLS, ledger versionado e tela de fila; a PoC (stage 1 + doc 06) os inclui. Não é problema da PoC, mas o resultado não deve ser lido como plano de produção.
- **R10 — Dados de piloto:** tudo sintético; telemetria do emulador desligada; nenhum dado real de cliente entra no repo.

**Premissas**
- P1 — Um tenant por cliente (IBM Japan = 1 tenant sintético), árvore: `org (raiz) → tenant IBM Japan → Finance → Q2C Invoicing / Q2C Cash Application`. Um segundo tenant sintético existe só para testes de isolamento.
- P2 — "Escopo: cliente CUST-0042" = registro com `scope` no nó do time Q2C + `entities: ["customer:CUST-0042"]` + condição CEL. Cliente é entidade, não nó da árvore. → Q6.
- P3 — Identidade: `Authorization: Bearer <API key estática por tenant>` (hash do valor guardado no Secrets Manager, provisionado pelo Terraform; valor em `.env` local de dev, nunca versionado) + `X-End-User-Id: <uuid>`; propósito no corpo. Documentado como substituto de OIDC (D1).
- P4 — Uma correção gera uma proposta de regra (limiar configurável, padrão 1 na PoC; D17 fala em N casos e N aprovadores). → Q9.
- P5 — Proposta de regra a partir da correção é **determinística**: o domain pack traduz uma correção estruturada (`target`, valor esperado, condição) em `rule` com `asserted_by.type=model`/`policy` e authority ≤ 10; o aprovador humano define authority/enforcement finais.
- P6 — Verify: além de checar que cada `mandatory`/`prohibition` do pack continua `approved` e vigente em T, refaz a consulta de aplicabilidade por SQL e dá `hold` se surgiu regra obrigatória aplicável que não estava no pack (barato e cobre o espírito do cenário 4).
- P7 — Decision Desk só em inglês; dados exibidos em japonês como vieram; chaves i18n só em `en` (o `ja` cai no fallback). Lacuna registrada.
- P8 — Packs ficam em tabela append-only `served_pack` (pack ID, trace ID, consumidor, propósito, `policy_revision`, IDs+versões dos registros, snapshot cifrado); selos Merkle fora do escopo (brief).
- P9 — Repo da PoC em `~/cortex-v2/cortex-v2-poc`, git local sem remoto, branch `main`; ainda sem commit.

---

## 9. Perguntas e respostas

Respostas propostas em 20 nov 2026, com base nos docs 03 (Rev. B), 04 (Rev. C), 06, 11 e 12 e no código lido. Valem **só para a PoC**; não são decisões do Execution Plan. Cada uma diz quem confirma.

1. **Docker no Mac:** não há runtime instalado. Qual você prefere — Docker Desktop (licença paga para empresa), OrbStack ou Colima? Instala você, ou quer que eu prepare os comandos? E o `gh`/remoto: fica sem remoto (PRs em `docs/prs/`)?

   **Resposta.** **Colima**, a não ser que a arbitr já pague Docker Desktop ou OrbStack. Os dois cobram por uso comercial: Docker Desktop exige assinatura paga em empresa com 250+ funcionários **ou** US$ 10M+ de receita; OrbStack exige licença para qualquer uso profissional (US$ 8/usuário/mês). Colima é open source e não tem essa cláusula.
   - Você instala (precisa de Homebrew e talvez de admin no Mac). Comandos, para conferir na issue 1:
     ```
     brew install colima docker docker-compose docker-buildx
     # siga o aviso do brew: "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] em ~/.docker/config.json
     colima start --vm-type vz --arch aarch64 --cpu 4 --memory 8 --disk 60 --mount-type virtiofs
     docker info && docker compose version
     ```
     8 GB porque a stack soma emulador (2 GB), Postgres, OPA e o Nuxt.
   - **Remoto:** fica sem remoto por enquanto; PRs em `docs/prs/`. Não publicar em GitHub pessoal: o repo descreve o piloto IBM e copia padrões do monorepo da arbitr. Quando o dono do acesso a repos responder (item aberto com Nake/Erika), criar um repo **privado na org da arbitr**.
   - *Quem confirma:* Elder (runtime); Nake/Erika (licença, se já existir, e onde o repo vai morar).

2. **Local do repo:** `~/cortex-v2/cortex-v2-poc` está bom? Os snapshots ficam em `~/Downloads/repos-arbitr` — posso continuar lendo de lá (ou você move para `~/cortex-v2/repos-arbitr`)?

   **Resposta.** O local do repo está bom. **Mover os snapshots para `~/cortex-v2/repos-arbitr`**, ao lado do repo e fora do git da PoC. Assim um único folder (`~/cortex-v2`) cobre os dois, não depende de `Downloads` (que costuma ser limpo) e não há risco de commitar código do monorepo. A PoC só cita os snapshots em documentação; nada no build ou nos testes lê de lá.
   - *Quem confirma:* Elder.

3. **Testes:** aceita o desvio da regra hermética — `make test` = unit herméticos + integração contra uma stack de teste efêmera em container (6.8)?

   **Resposta.** Aceito, com um ajuste para ficar mais perto da casa:
   - `make test` = **só unit herméticos**, como no monorepo (`addopts = "-m 'not integration'"`, socket guard ligado).
   - `make test-integration` = stack de teste efêmera (projeto compose separado, ex. `-p cortex-poc-test`, volumes descartáveis, sem portas publicadas no host). Nunca o banco de dev.
   - `make test-all` = os dois. É o que define "pronto" na PoC, porque os critérios de aceite (RLS, trigger, OPA, pg_bigm, emulador) estão no nível de integração.
   - Registrar o desvio num ADR curto, para ninguém levar o padrão ao monorepo sem conversar.
   - *Quem confirma:* Elder; avisar Juan Carlos se a PoC virar referência para o monorepo.

4. **Stack efêmera:** aceita que `make dev` sempre recrie emulador + banco + seed (R2), já que o emulador não persiste estado nem chaves?

   **Resposta.** Sim. Enquanto o emulador não persiste estado, é a única opção segura: com banco persistente, reiniciar o emulador destrói as chaves KMS e vira um crypto-shred acidental. Condições:
   - seed sintético, determinístico e rápido;
   - `make dev` idempotente e `make stop` derruba tudo junto;
   - nenhum dado de dev feito à mão que precise sobreviver (o que importa vira seed ou fixture).
   - Isso vale só para o ambiente local. Em KMS/Key Protect reais as chaves persistem.
   - *Quem confirma:* Elder.

5. **Status `rejected`:** a Rev. B não tem. Recomendo manter os 6 status e gravar a rejeição como versão `deprecated` + registro `decision` com o motivo. Ou prefere adicionar `rejected` ao enum (desvio da Rev. B)?

   **Resposta.** Seguir a Rev. B (6 status).
   - Proposta rejeitada = nova versão `deprecated` com `approved_by` vazio + registro `kind=decision` com o motivo, quem decidiu e `derived_from` apontando para a proposta.
   - "Rejeitada" se distingue de "aposentada" pelo histórico: nunca teve versão `approved`. A API e o Decision Desk expõem um campo derivado `outcome: rejected` para o usuário e o auditor não precisarem inferir.
   - Levar ao WS1 (envelope, D4/D6) como lacuna da Rev. B: um auditor vai querer ver "rejeitado" explícito. Se o WS1 decidir adicionar o status, a mudança na PoC é pequena.
   - *Quem confirma:* WS1 (Juan Carlos) para o envelope; Elder para a PoC.

6. **Regra de cliente:** confirma P2 (cliente em `entities[]` + CEL, escopo = nó do time)?

   **Resposta.** Confirmo P2. A Rev. B define escopo como time/mercado/idioma/canal e busca regras obrigatórias "por entidade e escopo"; cliente é entidade. Três detalhes:
   - **Uma só fonte de verdade para o cliente.** A consulta SQL de aplicabilidade filtra por `entities` (regra sem entidade = vale para todos os casos do escopo; regra com entidade = só casos que compartilham uma). O CEL fica para o resto (datas, valores). A admissão rejeita uma regra cuja CEL compare `customer_id` com algo diferente do que está em `entities`.
   - **Cash application:** a entidade do caso é o **pagador**, que pode não ser o cliente faturado. O resolvedor de entidade (WS3 + normalizador de nome bancário do WS5) mapeia nome bancário → `payer:` → `customer:` e devolve candidatos com evidência.
   - **Pergunta para a IBM (deep dive de 25 nov):** uma regra de cliente vale para todo o grupo econômico (matriz e filiais)? Se sim, precisamos de hierarquia de entidade, não de escopo. Hoje nada no design cobre isso.
   - *Quem confirma:* Elder (PoC); IBM (grupo econômico).

7. **API `/v1/cortex`:** na PoC não há integrations-service, então o org-brain serve `/v1/cortex/*` direto (o Decision Desk usa as mesmas rotas). Spec único no estilo da casa: code-first (FastAPI) → `libraries/cortex-contract/openapi.json` versionado + teste de drift + tipos TS gerados para o frontend (precedente: `arbitr-sdk/scripts/generate_models.py` + `docs-drift`). Ok, ou prefere spec-first (YAML → código)?

   **Resposta.** Code-first. É o precedente da casa e atende o D15 ("um spec que gera REST e MCP, poucas ferramentas amplas") desde que o `openapi.json` seja **o** spec:
   - marcar com uma extensão (ex. `x-mcp-tool`) as poucas operações amplas que virariam ferramenta MCP (pedir pack de contexto, verify, enviar correção), com um teste que mantém essa lista estável;
   - gerar o servidor MCP fica fora (stage 3), mas o contrato já nasce pronto para isso;
   - manter prefixo `/v1/cortex` e a forma de autenticação iguais às do integrations-service (doc 12), para que mover as rotas para lá seja só troca de host.
   - *Quem confirma:* Elder (PoC); D15 é do WS2.

8. **CEL:** ok com `cel-expr-python` + fallback `common-expression-language` (seção 5)?

   **Resposta.** Ok. Duas regras de negócio entram junto com a escolha:
   - **Valores:** o WS1 pede valores decimais e tolerâncias por moeda. Guardar sempre em unidade menor (`int`) + `currency`. Tolerâncias de cash application (ex. diferença por tarifa bancária descontada pelo pagador) também em `int` na mesma unidade. Nunca `float`.
   - **Data de referência:** "regras valem na data do evento de negócio" (WS1). Datas na avaliação CEL = data do evento (emissão da fatura, data do pagamento), não o relógio do servidor. Isso também alimenta a consulta "as of T".
   - *Quem confirma:* Elder.

9. **Limiar de correções:** ok 1 correção → 1 proposta na PoC (D17 configurável)?

   **Resposta.** Ok para a demo, mas com os três controles do D17 já parametrizados:
   - `min_distinct_cases`, `min_distinct_correctors` e `window_days`, mais separação de funções sempre ligada (quem corrigiu não aprova a regra resultante);
   - default da demo `1 / 1 / 30 dias`, fiel ao loop do doc 06 (uma correção vira proposta); o golden set também roda com algo como `3 / 2 / 30` para provar que o limiar funciona.
   - O valor real é decisão de produto com a IBM e depende de volume: custom invoices são ~1% não automatizado, então um limiar alto talvez nunca dispare. O doc 11 também propõe que, no piloto, só o writeback entre; a sugestão de regra só no launch.
   - *Quem confirma:* WS0 (Nake/Erika) com a IBM.

10. **O que cifrar por tenant:** `body` dos registros + snapshots de pack + exports S3; metadados de governança e projeções em claro (projeções apagadas no erasure). Ok?

    **Resposta.** O que cifrar está ok. **A granularidade da chave precisa mudar.** Uma chave por tenant só apaga o tenant inteiro, mas o stage 1 pede "erasure manual testada, inclusive dos packs armazenados" e o doc 06 promete "erasure por linhagem" (APPI). Ou seja: apagar um registro e o que deriva dele, sem apagar o tenant. Proposta:
    - chave KMS por tenant como **KEK**; **DEK por registro** (ou por raiz de linhagem), guardada cifrada numa tabela `record_key` fora do ledger. Essa tabela é mutável: é a única coisa que se apaga;
    - erasure de registro = apagar as DEKs da linhagem (`derived_from`) + versão `erased` + apagar as linhas de projeção;
    - o snapshot do pack guarda o `body` de cada registro cifrado com a DEK **do próprio registro**. Depois do erasure, o pack continua íntegro mas ilegível naquele registro, sem reescrever o pack;
    - erasure de tenant = `ScheduleKeyDeletion` da KEK, como já está em 6.6;
    - metadados em claro: ok, mas `entities[]` só pode ter chaves canônicas opacas (`customer:CUST-0042`, nunca nome de pessoa ou de pagador). A admissão checa isso; no AskHR vai importar mais.
    - *Quem confirma:* Elder (PoC); Legal (D20/APPI) define o que a erasure precisa cobrir em produção.

11. **Object Lock:** se o emulador der 501, aceita o fallback "bucket versionado + export append-only + gap documentado", ou isso é bloqueio?

    **Resposta.** Não é bloqueio para a PoC, mas não pode aparecer como verde. A retenção imutável faz parte da promessa de evidência (packs, export do D8, SOC 2, a data de 2 dez ainda sem confirmação do jurídico). Mas o objetivo da PoC é testar portabilidade, e um 501 é limite do emulador, não do S3 real. Fallback aceito com três condições:
    - o teste de retenção fica marcado `xfail(reason="emulator: Object Lock 501")`, não é apagado;
    - o gap entra na seção 8 (R3);
    - item para o WS6: confirmar se o IBM COS aceita a mesma chamada S3 de Object Lock ou só a política de retenção própria dele. Se não aceitar, o "um cliente de storage" da Rev. C precisa de um adaptador.
    - *Quem confirma:* Elder (PoC); WS6 (COS).

12. **Versão do emulador:** as versões saem várias vezes por dia (1.0.519 → 1.0.545 em ~36 h). Fixo a mais recente que passar na sonda, ou exige uma com pelo menos 2 semanas (a lista `versions.json` não tem datas; eu mapearia pelos releases do GitHub)?

    **Resposta.** Fixar a mais recente que passar na sonda. A regra de 2 semanas não reduz risco real aqui (não é produção) e custa caro sem datas na lista.
    - Gravar no repo a versão, o sha256 do RPM e o resultado da sonda (ex. `docs/emulator-probe-<versão>.md`).
    - Atualizar só de propósito, rodando a sonda de novo.
    - *Quem confirma:* Elder.

### 9.1 O que depende de outra pessoa
| Item | Quem | Pergunta |
|---|---|---|
| Q1 | Nake/Erika | A arbitr já tem licença Docker Desktop/OrbStack? Onde o repo da PoC vai morar? |
| Q5 | WS1 (Juan Carlos) | O envelope ganha o status `rejected`? |
| Q6 | IBM (deep dive 25 nov) | Regra de cliente vale para o grupo econômico? |
| Q9 | WS0 + IBM | Limiar real do D17 e se a sugestão de regra entra no piloto ou só no launch. |
| Q10 | Legal (D20/APPI) | Escopo da erasure: por registro/linhagem, por titular de dado, por tenant? |
| Q11 | WS6 | IBM COS aceita a API S3 de Object Lock? |

### 9.2 O que as respostas mudam no plano (registrado antes da Fase 2)
- **Q1/Q2:** runtime = Colima (issue 0, executada por você). Snapshots movidos para `~/cortex-v2/repos-arbitr`. Sem remoto; PRs em `docs/prs/`.
- **Q3:** `make test` = só unit herméticos; `make test-integration` = stack efêmera (`-p cortex-poc-test`, sem portas no host); `make test-all` = os dois, e é o que define "pronto". ADR `docs/adr/0001-test-tiers.md` na issue 1. Substitui a proposta de 6.8.
- **Q4:** `make dev` recria emulador + Terraform + banco + seed; `make stop` derruba tudo.
- **Q5:** 6 status da Rev. B; rejeição = versão `deprecated` sem aprovação + `decision`; campo derivado `outcome: rejected` na API e no Decision Desk.
- **Q6:** aplicabilidade filtra `entities` em SQL (sem entidade = vale para todo o escopo); CEL só para o resto; a admissão rejeita CEL que compare `customer_id` com valor fora de `entities`. Em cash application a entidade do caso é `payer:` → `customer:` pelo resolvedor.
- **Q7:** code-first; `libraries/cortex-contract/openapi.json` é o spec; extensão `x-mcp-tool` nas operações amplas (pedir pack, verify, writeback) com teste que fixa a lista; auth e prefixo iguais ao integrations-service.
- **Q8:** valores em unidade menor `int` + `currency`; datas da avaliação = data do evento de negócio (`as_of`), nunca o relógio do servidor.
- **Q9:** `min_distinct_cases`, `min_distinct_correctors`, `window_days` + separação de funções sempre ligada; demo `1/1/30`, golden set também em `3/2/30`.
- **Q10 (substitui 6.6):** KMS por tenant = **KEK**; **DEK por registro** (por raiz de linhagem quando houver `derived_from`) em `record_key`, tabela mutável fora do ledger. Erasure de registro = apagar DEKs da linhagem + versão `erased` + linhas de projeção; o pack guarda cada `body` cifrado com a DEK do próprio registro, então fica íntegro e ilegível só naquele registro. Erasure de tenant = `ScheduleKeyDeletion` da KEK. `entities[]` só com chaves canônicas opacas (checado na admissão).
- **Q11:** se Object Lock der 501: teste `xfail(reason="emulator: Object Lock 501")`, gap em R3, item para o WS6 (IBM COS).
- **Q12:** fixar a versão mais recente que passar na sonda; registrar versão + sha256 do RPM + resultado em `docs/emulator-probe-<versão>.md`.
- As respostas citam o **doc 11**, que não está em `docs/architecture/` nem nos arquivos de handoff desta máquina. Se ele tiver requisitos de WS1 (valores decimais, data do evento) além dos citados aqui, preciso de uma cópia.
