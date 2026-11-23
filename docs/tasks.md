# Cortex v2 PoC — Plano em issues (Fase 2)

Data: 20 nov 2026 · Status: **aguardando aprovação**.
Base: `docs/discovery.md` (inclusive 9 e 9.2) e `docs/architecture/`.

## Como ler
- Uma issue = uma branch `poc/NNN-<slug>` = uma descrição de PR em `docs/prs/NNN-<slug>.md` com a saída dos comandos de aceite colada.
- Tamanho máximo: ~1 dia focado ou ~400 linhas de diff, **sem contar** dados gerados e lockfiles.
- **Definition of done (vale para toda issue, não repito abaixo):** `docker compose up` sobe a partir de um clone limpo; `make test-all` passa em container; `make lint` e `make typecheck` passam (Ruff + Pyright; ESLint + Vitest no frontend; Regal + `opa test` nas políticas); nenhum segredo em código ou `.env` versionado (só `.env.example`); README atualizado; IDs Alembic ≤ 32 caracteres e uma única head; nenhum `if workflow == ...` no núcleo (se aparecer, paro e reporto).
- O núcleo é testado com um **domain pack falso** que só existe em `tests/` (issue 15). Os dois packs reais só chegam nas issues 25 e 26. Isso prova que o núcleo não sabe qual workflow está rodando.

### Alvos `make` (criados ao longo das issues; todos chamam o compose)
| Alvo | O que faz | Issue |
|---|---|---|
| `make help` | lista os alvos | 1 |
| `make dev` / `make stop` / `make logs` / `make ps` / `make shell SVC=` | sobe a stack efêmera completa / derruba tudo | 1 (cresce até 28) |
| `make build` | builda as imagens | 1 |
| `make test` | unit herméticos (sem stack, socket guard ligado) | 1 |
| `make test-integration` | stack de teste efêmera `-p cortex-poc-test`, sem portas no host | 2 |
| `make test-all` | `test` + `test-integration` + `opa test` | 4 |
| `make lint` / `make format` / `make typecheck` | Ruff, Regal, ESLint / Ruff format / Pyright, vue-tsc | 1, 4, 27 |
| `make migrate` / `make migrate-check` | Alembic upgrade / uma head + ids ≤ 32 | 8 |
| `make probe-emulator` | sonda de fidelidade do AWS Emulator | 3 |
| `make provision` | Terraform apply no emulador (idempotente) | 5 |
| `make gen-data` | gera `data/synthetic/` (determinístico) | 6 |
| `make seed` | tenant sintético, grants, regras iniciais | 13 (cresce até 26) |
| `make reindex` | apaga e reconstrói as projeções de busca | 10 |
| `make contract` / `make contract-check` | gera `openapi.json` + tipos TS / falha se houver drift | 16 |
| `make test-e2e-ui` | Playwright do Decision Desk contra `make dev` | 28 |
| `make e2e` | harness end-to-end nos dois workflows | 29 |

### Grafo de dependências
```
0 ─ 1 ─┬─ 2 ─ 8 ─ 9 ─ 10 ───────────────┐
       ├─ 3 ─ 5 ─┬─ 13 ─────────────────┤
       │         └─ 14 ─────────────────┤
       ├─ 4 ─ 12 ─ 13                   │
       ├─ 6 ─ 7                         │
       └─ 11 ─ 15 ─ 16 ─ 17 ─ 18 ─ 19 ─ 20 ─ 21 ─ 22 ─┬─ 23 ─ 24
                                                     ├─ 25 ─ 26
       1 ─ 27 ─ 28 (também depende de 18, 20)        └─ 29 (depende de 24, 26, 28)
```
(Dependências exatas em cada issue.)

---

## 0. Ambiente local: Colima + Docker CLI (executada por você)
**Contexto.** O host não tem runtime de containers (discovery R1, Q1). Tudo depende disso.
**Inclui.** Instalar Colima, Docker CLI, Compose e Buildx via Homebrew; VM `vz` aarch64, 4 CPU, 8 GB, 60 GB, `virtiofs`.
**Não inclui.** Docker Desktop/OrbStack; remoto git.
**Critérios de aceite.**
- `docker info` mostra `Architecture: aarch64`; `docker compose version` ≥ v2.
- `docker run --rm --platform linux/amd64 alpine uname -m` imprime `x86_64` (emulação amd64 disponível para a checagem multi-arch).
**Validação.**
```bash
brew install colima docker docker-compose docker-buildx
colima start --vm-type vz --arch aarch64 --cpu 4 --memory 8 --disk 60 --mount-type virtiofs --vz-rosetta
docker info --format '{{.Architecture}}' && docker compose version
docker run --rm --platform linux/amd64 alpine uname -m
```
**Dependências.** Nenhuma. **Tamanho.** 30 min.

---

## 1. Esqueleto do repo, org-brain com `/health`, Makefile e checagens em container
**Contexto.** Fixar o layout do monorepo e o "`make` para tudo" antes de qualquer código de domínio.
**Inclui.**
- `services/org-brain-service/` (FastAPI `create_app()`, `config.py` com pydantic-settings, `logging_config.py` structlog JSON, middleware de request id, `GET /health`), `Pipfile`/`Pipfile.lock`, `pyproject.toml` (pytest com `-m 'not integration'`, Ruff, Pyright), `Dockerfile` `python:3.12-slim` no formato do monorepo.
- `libraries/arbitr-test-utils/` com socket guard (copiado do `PROD:libraries/arbitr-test-utils`, só o necessário).
- `docker-compose.yml` (rede, `org-brain-service`), `.env.example`, `.gitignore`, `Makefile` com `help dev stop logs ps shell build test lint format typecheck`.
- `docs/adr/0001-test-tiers.md` (desvio Q3).
- README inicial (pré-requisitos = issue 0).
**Não inclui.** Postgres, emulador, OPA, frontend.
**Critérios de aceite.**
1. Clone limpo + `make dev` → `org-brain-service` healthy; `curl -s localhost:5008/health` devolve `{"status":"ok"}`.
2. `make test` passa com a stack parada e falha se um teste abrir socket.
3. `make lint` e `make typecheck` passam.
4. Imagem builda para `linux/arm64` e `linux/amd64`.
**Validação.**
```bash
git clone . /tmp/cortex-clean && cd /tmp/cortex-clean && cp .env.example .env
make dev && curl -s localhost:5008/health
make stop && make test && make lint && make typecheck
docker buildx build --platform linux/arm64,linux/amd64 -f services/org-brain-service/Dockerfile .
```
**Dependências.** 0. **Tamanho.** ~300 linhas.

## 2. Imagem Postgres 16 com pgvector + pg_bigm, papéis e stack de teste
**Contexto.** D12: busca só em Postgres; RLS exige papel que não é dono (discovery 6.3).
**Inclui.**
- `infra/postgres/Dockerfile`: `FROM pgvector/pgvector:0.8.7-pg16-<debian>` fixado por digest; compila pg_bigm `v1.2-20250903` com PGXS contra headers PG16 ≥ 16.12; tag própria `cortex-poc/postgres:16-pgvector0.8.7-bigm1.2.20250903`.
- `initdb` script: banco `org_brain`, papéis `org_brain_owner` (dono, migrações) e `org_brain_app` (`NOSUPERUSER NOBYPASSRLS`), `CREATE EXTENSION vector, pg_bigm`.
- Serviço `postgres` com healthcheck `pg_isready`, volume descartável; `make test-integration` com projeto `cortex-poc-test`.
**Não inclui.** Tabelas (issue 8).
**Critérios de aceite.**
1. `SELECT extname, extversion FROM pg_extension` lista `vector 0.8.7` e `pg_bigm 1.2`.
2. `show_bigm('架空電機株式会社')` devolve bigramas; um índice `gin (... gin_bigm_ops)` é criado.
3. `org_brain_app` não é superusuário e não tem `BYPASSRLS`.
4. Imagem builda em `linux/arm64` e `linux/amd64`.
5. `make test-integration` sobe a stack de teste, roda 1 teste `integration` e derruba os volumes.
**Validação.**
```bash
make dev && make shell SVC=postgres   # psql -U postgres -d org_brain -c "SELECT extname, extversion FROM pg_extension"
make test-integration
docker buildx build --platform linux/arm64,linux/amd64 infra/postgres
```
**Dependências.** 1. **Tamanho.** ~200 linhas.

## 3. AWS Emulator no compose + sonda de fidelidade (gate)
**Contexto.** Não existe imagem publicada; fidelidade por operação desconhecida (discovery 3, R2–R4). Nada que use AWS começa antes desta sonda passar.
**Inclui.**
- `infra/aws-emulator/Dockerfile`: `amazonlinux:2023-minimal` fixado por digest, RPM `aws-emulator-<versão>-1-<aarch64|x86_64>.rpm` por `TARGETARCH`, verificação da assinatura `.sig` + sha256, `curl` para o healthcheck, `ENTRYPOINT aws-emulator -b 0.0.0.0 --telemetry off`, `HOME=/home/emulator`, `EMULATOR_TELEMETRY=off`, `mem_limit: 2g`.
- Volume `aws-emulator-home` (rw no emulador, ro nos clientes, mesmo path); healthcheck `/__admin/health` com `--cacert`; porta publicada só em `127.0.0.1:9297`.
- Serviço one-shot `aws-probe` (`network_mode: service:aws-emulator`, `depends_on: service_healthy`), env `AWS_ENDPOINT_URL=https://127.0.0.1:9297`, `AWS_CA_BUNDLE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `AWS_EC2_METADATA_DISABLED`, `AWS_DISABLE_HOST_PREFIX_INJECTION`, `AWS_REGION`.
- `scripts/probe/aws_probe.py` (boto3): S3 `CreateBucket` (Object Lock), `PutBucketVersioning`, `PutObjectLockConfiguration`, `PutObject` com retenção, `GetObject`, `HeadObject`, `GetObjectRetention`, tentativa de `DeleteObjectVersion` sob retenção, toda a família `GetBucket*` que o Terraform lê; KMS `CreateKey`, `CreateAlias`, `DescribeKey`, `GenerateDataKey`, `Encrypt`, `Decrypt`, `ScheduleKeyDeletion` → `Decrypt` deve falhar, `DisableKey`, `TagResource` (esperado 501); Secrets Manager `CreateSecret`, `PutSecretValue`, `GetSecretValue`, `DescribeSecret`, `GetResourcePolicy`; `openssl x509 -ext subjectAltName` no `ca.pem`/cert servido.
- Saída: `docs/emulator-probe-<versão>.md` (versão, sha256 do RPM, tabela operação → código HTTP → ok/501/divergente, SANs).
**Não inclui.** Terraform (issue 5).
**Critérios de aceite.**
1. `make dev` → `aws-emulator` healthy; nenhuma chave em `.env`, código ou imagem.
2. `make probe-emulator` gera o relatório; toda operação marcada "obrigatória" (todas acima, menos `TagResource` e Object Lock) dá ok. Se não der: **paro, documento e proponho alternativa** antes da issue 5.
3. Reiniciar o emulador gera novas credenciais e a sonda volta a passar (clientes releem o volume).
4. Imagem builda em `linux/arm64` e `linux/amd64`.
**Validação.**
```bash
make dev && make ps
make probe-emulator && cat docs/emulator-probe-*.md
docker compose restart aws-emulator && make probe-emulator
docker buildx build --platform linux/arm64,linux/amd64 infra/aws-emulator
```
**Dependências.** 1. **Tamanho.** ~350 linhas.

## 4. OPA no compose + bundle versionado + Regal e `opa test` em container
**Contexto.** D22: OPA para autorização; política como código versionada (discovery 6.7, 7.3).
**Inclui.**
- `policies/cortex/` com política placeholder (`allow := false`) e 1 teste; `.regal/config.yaml`.
- Serviço one-shot `opa-bundle`: `opa build -b policies -r <sha256 do conteúdo de policies/>` → volume; serviço `opa` (`openpolicyagent/opa:1.22.4`, multi-arch confirmado com `docker manifest inspect`; senão binário oficial + checksum) com `run --server --bundle`, roots do bundle = `cortex/policy`.
- Healthcheck `GET /health?bundles` (técnica escolhida conforme a imagem: distroless → tag `-debug` fixada por digest ou sidecar; decisão registrada no PR).
- `make lint` inclui `regal lint policies`; `make test-all` inclui `opa test policies -v`; ambos em container (`ghcr.io/open-policy-agent/regal:0.43.0`).
- Registrar no `docs/discovery.md` o padrão OPA (pedido do brief).
**Não inclui.** Políticas reais (12), cliente (13).
**Critérios de aceite.**
1. `make dev` → `opa` healthy só depois do bundle ativado.
2. `curl -s -X POST localhost:8181/v1/data/cortex/policy/revision` devolve o mesmo hash que `opa-bundle` imprimiu; mudar um `.rego` muda o hash.
3. `PUT /v1/data/cortex/tenants/x` aceito (fora das roots do bundle).
4. `make lint` e `make test-all` rodam Regal e `opa test` sem nada instalado no host.
**Validação.**
```bash
make dev && make ps
curl -s -X POST localhost:8181/v1/data/cortex/policy/revision -d '{}'
make lint && make test-all
```
**Dependências.** 1. **Tamanho.** ~200 linhas.

## 5. Provisionamento idempotente no emulador com Terraform em container
**Contexto.** Rev. C: "diferenças de nuvem só neste módulo". Sem IaC na arbitr (discovery 6.9).
**Inclui.**
- `infra/terraform/aws/` (provider `hashicorp/aws` 6.73.x fixo, `.terraform.lock.hcl` com hashes arm64 e amd64): bucket `cortex-poc-evidence` (versionado, Object Lock habilitado se a sonda passou; senão só versionado e o gap fica em R3), bucket `cortex-poc-originals`, segredo `cortex/poc/tenant-api-keys` (hash das API keys, valor de entrada via variável lida de `.env` local), `aws_iam_policy_document` → `infra/iam/*.json` (política mínima do org-brain: S3 put/get nos dois buckets, KMS create/generate/decrypt/schedule-deletion restrito ao prefixo de alias, Secrets get de um ARN).
- Provider: `skip_credentials_validation`, `skip_requesting_account_id`, `skip_metadata_api_check`, `s3_use_path_style`; endpoint/CA/credenciais só por env.
- Serviço one-shot `terraform` (`network_mode: service:aws-emulator`), state num volume que vive e morre com o emulador; `make dev` roda `make provision`.
**Não inclui.** Chaves KMS (são por tenant, runtime — issue 14).
**Critérios de aceite.**
1. `make provision` duas vezes seguidas: o segundo `plan` mostra `No changes.`
2. `aws s3api get-bucket-versioning` = `Enabled`; Object Lock conforme sonda.
3. `infra/iam/*.json` versionados e revisáveis; nenhuma chamada IAM ao emulador.
4. Se o provider chamar algo que dê 501: **paro e proponho** script AWS CLI (brief).
**Validação.**
```bash
make dev && make provision && make provision 2>&1 | grep "No changes"
make shell SVC=aws-probe   # aws s3api get-bucket-versioning --bucket cortex-poc-evidence
```
**Dependências.** 3. **Tamanho.** ~250 linhas.

## 6. Gerador de dados sintéticos: base + `custom_invoices`
**Contexto.** Os testes dos gates já nascem com dados (brief). Tudo fictício, japonês, seed fixa.
**Inclui.**
- `scripts/synthetic/` (Python em container, `random.Random(20261120)`), `make gen-data`.
- `data/synthetic/custom_invoices/`: `cases.jsonl` (~40 faturas no formato do brief; JPY `int`; empresas claramente fictícias), `rules.json` (regras iniciais com chave simbólica, ex. `CI-R-QIS-REGNO`, enforcement, escopo, `entities`, `target`, CEL, validade), `corrections.jsonl` (correções de revisor: caso, campo, antes/depois, revisor), `expected.json` (golden set: por caso, chaves de regra que **devem** e **não podem** estar no pack, resultado esperado do verify, `outcome` esperado).
- Cobertura: caso feliz; correção de CUST-0042 (注文番号 no 備考 + 検収書 no Coupa) → regra aprovada → aplica no próximo caso; casos não relacionados (outro cliente/canal) em que a regra não pode aparecer; cenário 2 (instrução local "< ¥30.000 omitir número de registro" × proibição global); cenário 3 (regra mandatory QIS que a busca textual da query do caso não encontra); cenário 4 (regra retirada com projeção atrasada → fora do pack, verify `hold`).
- Teste de esquema dos arquivos gerados.
**Não inclui.** Cash application (7).
**Critérios de aceite.**
1. `make gen-data` duas vezes → `git diff --exit-code data/synthetic` limpo.
2. Nenhum `float` nos JSON; todos os totais batem (`tax_total`, `total`).
3. Nenhum nome de empresa real (lista de bloqueio no teste); todos os nomes têm marcador fictício (架空/サンプル/テスト).
4. `expected.json` cobre os 6 grupos acima (teste conta por grupo).
**Validação.**
```bash
make gen-data && git diff --exit-code data/synthetic && make test
```
**Dependências.** 1. **Tamanho.** ~350 linhas de código (dados gerados não contam).

## 7. Gerador de dados sintéticos: `cash_application`
**Contexto.** D18: os dois workflows sobre o mesmo núcleo.
**Inclui.** `data/synthetic/cash_application/` no mesmo formato: pagamentos (`payment_id`, `value_date`, `amount` int, `payer_name_raw` em kana de meia largura, banco, `open_invoices`), faturas em aberto, cadastro de clientes com nomes bancários; casos: `ｶ)ｶｸｳﾃﾞﾝｷ`, `ｶｸｳﾃﾞﾝｷ(ｶ`, truncado `ｶ)ｶｸｳﾃﾞﾝ` → CUST-0042; pagador ambíguo (dois clientes) → revisão; tarifa ≤ ¥880 (ex. ¥660) e > ¥880 (¥881, não casa); conta com 差押 → nunca auto-match; correção que vira a regra da tarifa; não relacionados; cenários 2, 3, 4.
**Não inclui.** Normalizador (issue 26).
**Critérios de aceite.** Iguais aos 1–4 da issue 6, para este workflow; mais: cada caso de resolução traz `expected_entity` e `expected_confidence_band` (`high|low|ambiguous`).
**Validação.** `make gen-data && git diff --exit-code data/synthetic && make test`
**Dependências.** 6. **Tamanho.** ~300 linhas.

## 8. Ledger: schema, migrações, RLS e append-only
**Contexto.** Regras 1–3 da Rev. B; discovery 6.1–6.3.
**Inclui.**
- Alembic (`org_brain_owner`), modelos SQLAlchemy: `memory_record` (PK interna `id`, `(record_id uuid, version)` único, os 19 campos, `tenant_id`, `kind`, `status`, `enforcement`, `authority smallint`, `valid_from/valid_to timestamptz`, `recorded_at`, `supersedes`, `derived_from uuid[]`, `entities text[]`, `scope uuid`, `body_ciphertext bytea`, `body_hash`), view `memory_record_current`; `outbox` (forma do project-orchestrator).
- Trigger `BEFORE UPDATE OR DELETE` em `memory_record` + guarda ORM (`immutable.py`).
- RLS `ENABLE` + `FORCE` em `memory_record` e `outbox`, política por `current_setting('app.tenant_id', true)::uuid`; grants mínimos ao `org_brain_app`.
- Gerador ULID → uuid; `make migrate`, `make migrate-check`.
**Não inclui.** Comandos de domínio (9), cifra (14).
**Critérios de aceite** (integration salvo indicação).
1. `UPDATE`/`DELETE` em `memory_record` falham com a mensagem do trigger; unit: guarda ORM bloqueia unit-of-work e bulk.
2. Como `org_brain_app`, sem `app.tenant_id` → 0 linhas; com tenant A → só A, mesmo via SQL cru.
3. `make migrate-check` falha com duas heads ou id > 32 (teste com migração falsa).
4. `record_id` gerados são UUIDs válidos e crescem com o tempo.
**Validação.** `make migrate && make migrate-check && make test-all`
**Dependências.** 2. **Tamanho.** ~380 linhas.

## 9. Ledger: comandos e leitura "as of T"
**Contexto.** Supersede em vez de update; outbox na mesma transação (doc 12, fluxo 1 passo 5).
**Inclui.** `app/services/ledger.py`: `append_new`, `transition(record_id, to_status, actor)`, `supersede(old, new_body, actor)`, `as_of(record_id, t)`, `current(...)`; tabela de transições no estilo `knowledge_commands._TRANSITIONS` (`proposed→approved|deprecated`, `approved→deprecated|superseded|stale`, `*→erased`); teto do modelo (`asserted_by.type=model` → authority ≤ 10, `ModelCeilingError`); cada escrita grava 1 linha no outbox na mesma transação; `flush` sem `commit`.
**Não inclui.** Fila de revisão (18).
**Critérios de aceite.**
1. Unit: transição ilegal → `IllegalTransitionError`; modelo com authority 20 → `ModelCeilingError`.
2. Integration: supersede gera novo `record_id` com `supersedes`, versão `superseded` do antigo, e 2 linhas de outbox; rollback não deixa nem registro nem outbox.
3. `as_of(t)` antes da transição devolve a versão antiga (system time) e respeita `valid_from/valid_to` (business time).
**Validação.** `make test-all`
**Dependências.** 8. **Tamanho.** ~350 linhas.

## 10. Projeção de busca (`SearchProjection`) + worker do outbox + `make reindex`
**Contexto.** D12: índice é projeção reconstruível; critério (a) do brief.
**Inclui.** Interface `SearchProjection` (`apply(event)`, `rebuild(tenant)`, `search(query, filters, as_of) -> [candidate]`); `PostgresSearchProjection` (tabela `search_doc` própria com RLS, FTS `simple`, `gin_bigm_ops` para japonês, coluna `vector(8)` com vetores fixos de teste); worker do outbox (claim com `FOR UPDATE SKIP LOCKED`, `claimed_at/dispatched_at`, `status failed` + `last_error`), registry de handlers; o worker roda como serviço separado e os testes drenam explicitamente (lag controlado sem código de teste na app); `make reindex`.
**Não inclui.** Revalidação no access gate (20).
**Critérios de aceite.**
1. Integration: busca `備考` e `振込手数料` encontra os registros certos via pg_bigm.
2. **(a)** Apagar `search_doc` + `make reindex` → mesmas respostas (ids e ordem) para um conjunto fixo de 20 queries.
3. Handler que lança exceção → `status=failed`, `attempt_count` incrementa, os outros eventos seguem.
4. Ledger e gates não importam `PostgresSearchProjection` (teste de import).
**Validação.** `make test-all && make dev && make reindex`
**Dependências.** 9. **Tamanho.** ~380 linhas.

## 11. `ConditionEvaluator` (CEL)
**Contexto.** D22: CEL para condições; validar na admissão (discovery 5, Q8).
**Inclui.** Interface `ConditionEvaluator` + `CelExprEvaluator` (`cel-expr-python` fixo); declaração de variáveis por domain pack (ex. `invoice`, `payment` como mapas); função custom `date("YYYY-MM-DD")` → timestamp 00:00 `Asia/Tokyo`; conversão do contexto (datas do evento → timestamp; `float` rejeitado); `compile()` devolve erro estruturado; introspecção de identificadores usados (para a regra Q6 sobre `customer_id`).
**Não inclui.** Uso na admissão (17).
**Critérios de aceite** (unit, primeiro os de caracterização; se algum falhar, troco para `common-expression-language` na mesma interface e registro no PR).
1. Dict aninhado: `invoice.customer_id == "CUST-0042" && invoice.channel == "coupa"` avalia certo.
2. `invoice.issue_date >= date("2023-10-01")` com datas antes/depois/no dia.
3. `invoice.total - payment.amount <= 880` com 990000/989340 (true) e 990000/989119 (false); overflow int64 → erro, nunca wrap.
4. Variável ou função desconhecida e sintaxe inválida falham no `compile`, não na avaliação.
5. Contexto com `float` → erro antes de avaliar.
6. Wheel instala em `linux/arm64` e `linux/amd64` (build multi-arch da imagem).
**Validação.** `make test && docker buildx build --platform linux/arm64,linux/amd64 -f services/org-brain-service/Dockerfile .`
**Dependências.** 1. **Tamanho.** ~250 linhas.

## 12. Políticas Rego: leitura e aprovação + testes
**Contexto.** Tradução do `arbitr-rbac` (UAT) para dados + Rego (discovery 7).
**Inclui.** `policies/cortex/authz.rego` (input `consumer, end_user, purpose, tenant_id, scope_node, permission, now`; nearest grant em `path_up`; barrier para `memory.*`; expiração com `expires_at <= now` = expirado; interseção consumidor ∩ usuário final; propósito → teto de sensibilidade; saída `allow, verdict, reason, readable_scopes, sensitivity_ceiling, policy_revision`); `approval.rego` (`memory.write` propõe, `memory.approve` no escopo do registro aprova, separação de funções); `revision.rego`; fixtures de dados no formato de 7.2.
**Não inclui.** Cliente HTTP (13).
**Critérios de aceite.**
1. `opa test` cobre os veredictos do resolver: `nearest-allow`, `inherited-allow`, `out-of-scope`, `no-permission`, `barrier` (grant acima bloqueado, grant no barrier vale), `expired` (inclusive no instante exato).
2. Corretor do caso não consegue aprovar a regra derivada (`deny` com motivo).
3. Propósito não registrado para o consumidor → `deny`.
4. `regal lint` sem violações.
**Validação.** `make lint && make test-all`
**Dependências.** 4. **Tamanho.** ~350 linhas (Rego + testes).

## 13. Cliente OPA, carga dos dados do tenant e identidade do consumidor
**Contexto.** Falha fechada; tenant vem da identidade, nunca da URL (doc 12).
**Inclui.** `app/clients/opa.py` (httpx, timeout curto, erro/timeout/`result` ausente → `AccessGateUnavailable` → 503 `access_gate_unavailable`); loader que faz `PUT /v1/data/cortex/tenants/<id>` a partir de `data/seed/tenants.json` no start; dependência `get_caller()` no formato `AuthedCaller` (API key Bearer → hash → consumidor/tenant, lido do Secrets Manager via boto3; header `X-End-User-Id`); `SET LOCAL app.tenant_id` por request; `make seed` (tenants, árvore, grants, consumidores, propósitos).
**Não inclui.** Rotas de negócio.
**Critérios de aceite.**
1. Unit (`httpx.MockTransport`): OPA 500, timeout e resposta sem `result` → 503 explícito; nunca leitura.
2. Integration: com o OPA parado (`docker compose stop opa`), qualquer leitura → 503; ao voltar, funciona.
3. API key de um tenant nunca lê o outro, mesmo passando outro tenant em query/body (RLS + identidade).
4. A decisão devolvida traz `policy_revision` igual ao do bundle ativo.
**Validação.** `make test-all && make dev && make seed`
**Dependências.** 5, 8, 12. **Tamanho.** ~350 linhas.

## 14. Chaves: KEK por tenant (KMS) + DEK por registro
**Contexto.** Q10: erasure por linhagem e por tenant.
**Inclui.** Interface `KeyService` (Rev. C: "atrás de uma interface de chaves"); `KmsKeyService` (boto3): onboarding do tenant cria KEK (`CreateKey` sem tags + `CreateAlias alias/cortex/tenant/<id>`); DEK por registro (ou por raiz de linhagem) via `GenerateDataKey`, guardada cifrada em `record_key` (mutável, fora do ledger, RLS); AES-GCM local com `cryptography`; cache de DEK em memória com descarte; `body_ciphertext` passa a ser gravado pelo ledger.
**Não inclui.** Erasure (24).
**Critérios de aceite.**
1. Integration: `body` no banco não contém o texto em claro (busca por substring japonesa falha); leitura pela app devolve o texto.
2. Dois registros do mesmo tenant têm DEKs diferentes; um registro com `derived_from` usa a DEK da raiz.
3. `record_key` é a única tabela de dados com `DELETE` permitido ao `org_brain_app`.
4. Nenhum `if emulator` no código; só env.
**Validação.** `make test-all`
**Dependências.** 5, 9. **Tamanho.** ~300 linhas.

## 15. Interface de domain pack + pack falso de teste
**Contexto.** D18: núcleo não sabe o workflow; packs são finos.
**Inclui.** `app/domain_packs/base.py`: `DomainPack` (nome, tipos de entidade, `resolve_entities(case) -> [EntityMatch(key, confidence, evidence)]`, declarações CEL e conversor de contexto, `targets` válidos, esquema Pydantic do `body` de regra e de correção, `correction_to_rule(correction) -> RuleDraft`, `search_text(case)`); registro por nome; `tests/fakes/fake_pack.py` com um domínio inventado ("widgets") usado por todos os testes do núcleo; teste que falha se o núcleo importar `custom_invoices`/`cash_application` ou comparar nome de workflow.
**Não inclui.** Packs reais (25, 26).
**Critérios de aceite.** Interface tipada (Pyright strict no módulo); fake pack passa num teste de contrato reutilizável (`assert_pack_contract(pack)`); guarda de import ativa.
**Validação.** `make test && make typecheck`
**Dependências.** 11. **Tamanho.** ~250 linhas.

## 16. API `/v1/cortex`: esqueleto, erros e contrato único
**Contexto.** D15 / Q7: code-first; `openapi.json` é o spec.
**Inclui.** Router `/v1/cortex` com `require_scope("cortex:read"|"cortex:write")`, modelo de erro, paginação; `scripts/export_openapi.py` → `libraries/cortex-contract/openapi.json`; extensão `x-mcp-tool` nas operações amplas; geração de tipos TS (`openapi-typescript` em container) → `libraries/cortex-contract/ts/`; `make contract`, `make contract-check`; teste que nenhum schema expõe `id` numérico.
**Não inclui.** Lógica das rotas (17+); servidor MCP.
**Critérios de aceite.**
1. `make contract-check` falha se uma rota mudar sem regenerar o spec.
2. Teste fixa a lista de `x-mcp-tool` (`request_context_pack`, `verify_output`, `submit_correction`).
3. Nenhum campo inteiro chamado `id`/`*_id` numérico nos schemas.
**Validação.** `make contract && make contract-check && make test`
**Dependências.** 13, 15. **Tamanho.** ~250 linhas.

## 17. Admission gate
**Contexto.** Rev. B: tag & classify → extract → resolve entities → admission policy → review queue; regra 6.
**Inclui.** `POST /v1/cortex/records` (proposta): checa tenant (identidade), escopo existe e é permitido (`approval.rego`, `memory.write`), sensibilidade ≤ teto, `entities` canônicas e opacas (regex de chave + rejeita nomes), `schema_version` conhecido, CEL compila (11), regra Q6 (CEL não compara `customer_id` fora de `entities`), `target` válido no pack; grava `proposed` cifrado (14) + abre/agrupa caso na fila (um caso por `(tenant, target, entities)` aberto — base do cenário 6); 422 com motivos estruturados.
**Não inclui.** Aprovação (18).
**Critérios de aceite** (com o pack falso).
1. CEL inválida → 422 `invalid_condition`, nada gravado.
2. `entities` com nome de pessoa → 422 `non_canonical_entity`.
3. Sensibilidade acima do teto do consumidor → 403 com motivo do OPA.
4. Duas propostas para o mesmo conflito abrem **um** caso.
**Validação.** `make test-all && make contract-check`
**Dependências.** 14, 16. **Tamanho.** ~380 linhas.

## 18. Fila de revisão e aprovação por dono nomeado
**Contexto.** Doc 06: dono IBM + backup aprovam; D17; Q5, Q9.
**Inclui.** Tabelas `review_case`, `review_decision` (append-only); rotas `GET /v1/cortex/review-cases`, `GET .../{id}` (proposta + evidência + casos afetados), `POST .../{id}/approve` (define authority/enforcement finais, exige `memory.approve` + separação de funções no OPA), `POST .../{id}/reject` (versão `deprecated` sem aprovação + `decision` com motivo, `derived_from` → proposta); campo derivado `outcome` (`pending|approved|rejected|retired|superseded`); dono + backup por escopo no seed.
**Não inclui.** UI (28).
**Critérios de aceite.**
1. Corretor tenta aprovar → 403 com motivo do OPA.
2. Aprovação grava `approved_by {approver_type:user, user_id, decision_id}` e versão `approved`; rejeição gera `outcome: rejected` sem nunca ter versão `approved`.
3. Aprovação com OPA parado → 503 (falha fechada também nas escritas).
**Validação.** `make test-all && make contract-check`
**Dependências.** 17. **Tamanho.** ~380 linhas.

## 19. Access gate (1/2): aplicabilidade e resolução
**Contexto.** Regra 4: constraints não perdem; cenário 2 e base do 3.
**Inclui.** `app/services/access_gate/applicability.py`: SQL sobre `memory_record_current` (`kind=rule`, `enforcement in (mandatory, prohibition)` **e** `default`, `status=approved`, `scope = ANY(readable ∩ path_up(scope pedido))`, `entities` vazio ou interseção com as do caso, vigente em `as_of` = data do evento), depois avaliação CEL; `resolve.py`: por `target`, prohibition/mandatory vencem default, depois authority, depois escopo mais próximo; perdedores rotulados `overridden_by`.
**Não inclui.** Busca, pack, HTTP (20).
**Critérios de aceite** (integration, pack falso).
1. Regra mandatory sem nenhuma palavra em comum com o caso entra mesmo assim (base do cenário 3).
2. Instrução local default × proibição global no mesmo `target`: proibição aplicada, local `overridden_by` (cenário 2), em qualquer ordem de authority/escopo.
3. Regra com entidade de outro cliente nunca aparece (caso não relacionado).
4. Regra fora da vigência em `as_of` não aparece; `as_of` vem do caso, não do relógio.
**Validação.** `make test-all`
**Dependências.** 10, 18. **Tamanho.** ~350 linhas.

## 20. Access gate (2/2): busca revalidada e context pack armazenado
**Contexto.** Doc 12, fluxo 2; critério (c) parte 1; served packs (stage 1).
**Inclui.** `POST /v1/cortex/context-packs` (`x-mcp-tool`): authorise (OPA) → candidatos da `SearchProjection` → **revalidação contra o ledger** (status, vigência em T, tenant, escopo, sensibilidade) → aplicabilidade (19) → resolve → pack; tabela `served_pack` append-only (pack ID ULID, trace ID, consumidor, usuário final, propósito, `policy_revision`, `as_of`, `(record_id, version)` + papel no pack, snapshot com cada `body` cifrado pela DEK do próprio registro); `GET .../context-packs/{id}` devolve o pack armazenado (replay sem re-executar).
**Não inclui.** Verify (21), export (23).
**Critérios de aceite.**
1. Registro retirado no ledger mas ainda na projeção (outbox não drenado) **não** entra no pack.
2. Pack guarda `policy_revision` do OPA e os pares `(record_id, version)`.
3. `GET` depois de mudar regras devolve o pack original, idêntico byte a byte no conteúdo armazenado.
4. Pack de tenant A não é legível com a chave de tenant B (404).
**Validação.** `make test-all && make contract-check`
**Dependências.** 19. **Tamanho.** ~380 linhas.

## 21. Verify (release check)
**Contexto.** Regra 5; D3; cenário 4.
**Inclui.** `POST /v1/cortex/verify` (`x-mcp-tool`): output + pack ID → `pass` ou `hold` com motivos (forma do Assurance UAT): cada `mandatory`/`prohibition` do pack ainda `approved` e vigente em `as_of` (SQL no ledger, não na projeção); refaz a aplicabilidade e dá `hold` se surgiu regra obrigatória que não estava no pack; checagem de satisfação delegada ao pack (`DomainPack.check_output`, opcional, ex. "campo X presente"); resultado gravado (append-only) ligado ao pack.
**Não inclui.** Bloqueio no sistema da IBM (D3).
**Critérios de aceite.**
1. **(c)** Regra retirada depois do pack, com a projeção atrasada → `hold` com `reason: rule_no_longer_approved`.
2. Nova regra mandatory aprovada depois do pack → `hold: new_mandatory_rule`.
3. Nada mudou → `pass`; pack inexistente ou de outro tenant → 404.
**Validação.** `make test-all && make contract-check`
**Dependências.** 20. **Tamanho.** ~300 linhas.

## 22. Writeback: correção → decisão → regra proposta
**Contexto.** Doc 12, fluxo 4; prova do stage 1.
**Inclui.** `POST /v1/cortex/corrections` (`x-mcp-tool`, idempotency key): proposta do AI × final, pack ID, revisor → `kind=decision` (`derived_from` = registros do pack) → `DomainPack.correction_to_rule` → proposta via admission gate (17) com `asserted_by.type=policy`, authority ≤ 10 até aprovação; limiares D17 (`min_distinct_cases`, `min_distinct_correctors`, `window_days`; demo `1/1/30`).
**Não inclui.** Sugestão por LLM.
**Critérios de aceite.**
1. Repetir com a mesma idempotency key não duplica nada.
2. Com `1/1/30`: 1 correção → 1 caso de revisão; com `3/2/30`: só abre no 3º caso com 2 corretores distintos.
3. A regra aprovada aparece no pack do **próximo** caso aplicável e não aparece no não relacionado (pack falso).
**Validação.** `make test-all && make contract-check`
**Dependências.** 21. **Tamanho.** ~330 linhas.

## 23. Evidência: export do pack para S3
**Contexto.** Doc 12, fluxo 5; Rev. C: COS → S3.
**Inclui.** `POST /v1/cortex/context-packs/{id}/export`: manifesto JSON (pack, registros e versões, aprovações, `policy_revision`, verify) cifrado por tenant → `PutObject` no bucket de evidência com retenção Object Lock (modo e prazo por config); chave do objeto determinística; job pelo outbox.
**Não inclui.** Selos Merkle, watsonx.governance.
**Critérios de aceite.**
1. Objeto exportado abre e o hash do manifesto confere com o pack.
2. Tentativa de apagar a versão sob retenção falha — ou, se a sonda deu 501, teste `xfail(reason="emulator: Object Lock 501")` e gap em R3 (Q11).
3. Export repetido é idempotente.
**Validação.** `make test-all`
**Dependências.** 22, 5. **Tamanho.** ~250 linhas.

## 24. Erasure: registro/linhagem e tenant (crypto-shredding)
**Contexto.** Stage 1: "erasure manual testada, inclusive dos packs"; Q10.
**Inclui.** CLI/rota admin `erase-record` (apaga DEKs da linhagem `derived_from`, grava versão `erased`, apaga linhas de projeção) e `erase-tenant` (`ScheduleKeyDeletion` da KEK + descarte de cache + projeções); relatório do que foi apagado.
**Critérios de aceite.**
1. Após `erase-record`, o pack antigo continua íntegro, mas o `body` daquele registro (e dos derivados) é ilegível; os outros registros do pack continuam legíveis.
2. Após `erase-tenant`, `Decrypt` falha (ou, se a sonda mostrou que não falha, `DisableKey` + registro no PR) e o export S3 do tenant não abre.
3. `make reindex` depois do erasure não ressuscita texto apagado.
**Validação.** `make test-all`
**Dependências.** 23. **Tamanho.** ~280 linhas.

## 25. Domain pack `custom_invoices`
**Contexto.** D18, primeira possibilidade.
**Inclui.** `app/domain_packs/custom_invoices/`: entidades (`customer:`, `channel:`), resolvedor (por `customer_id`), declarações CEL (`invoice`), `targets` (`invoice.registration_number`, `invoice.tax_breakdown`, `invoice.remarks.po_number`, `invoice.attachments.kenshusho`…), esquemas de regra/correção, `correction_to_rule`, `check_output`; carga de `rules.json` no `make seed`; runner do golden set.
**Critérios de aceite.** Contrato do pack passa; golden set de `custom_invoices` 100% (packs, ausências, verify, outcome), incluindo os cenários 2, 3 e 4; o núcleo não mudou nesta issue (diff só no pack, seed e testes).
**Validação.** `make seed && make test-all`
**Dependências.** 22, 6. **Tamanho.** ~350 linhas.

## 26. Domain pack `cash_application`
**Contexto.** D18, segunda possibilidade.
**Inclui.** `app/domain_packs/cash_application/`: normalizador de nome bancário (meia/largura total, `ｶ)`/`(ｶ`/`ｶﾌﾞｼｷｶﾞｲｼｬ`, truncamento por prefixo) → `payer:` → `customer:` com evidência e confiança; mais de um candidato → revisão, nunca auto-match; declarações CEL (`payment`, `invoice`); regras de tarifa (≤ ¥880 → 支払手数料) e 差押 (mandatory); `correction_to_rule`; golden set.
**Critérios de aceite.** Contrato do pack; os três nomes resolvem para CUST-0042 com `high`; ambíguo → `ambiguous` + caso de revisão; 差押 sempre bloqueia; golden set 100%; núcleo inalterado.
**Validação.** `make seed && make test-all`
**Dependências.** 25, 7. **Tamanho.** ~350 linhas.

## 27. Frontend: esqueleto `frontend/arbitr` em container
**Contexto.** Padrão Nuxt 4 do monorepo (discovery 2.3).
**Inclui.** App Nuxt 4 mínima (`ssr: false`, Nuxt UI 4.6, Tailwind 4.2, Pinia 3, i18n `en`, Zod), mesmo `eslint.config.mjs`, guard `check-v2-styles.mjs` e tokens brand v2 copiados, `app/utils/api/client.ts` + `cortex.ts` usando os tipos de `libraries/cortex-contract/ts`; proxy de dev que injeta a API key no servidor (nunca no bundle do browser); serviço `frontend` (porta 5001) no compose; Vitest; `make lint`/`make test` incluem o frontend.
**Critérios de aceite.** `make dev` serve `localhost:5001`; `make lint`, `make typecheck` (vue-tsc) e Vitest passam em container; `grep` no bundle build não encontra a API key.
**Validação.** `make dev && curl -sI localhost:5001 && make lint && make typecheck && make test`
**Dependências.** 1, 16. **Tamanho.** ~300 linhas (sem lockfile).

## 28. Tela Decision Desk
**Contexto.** Doc 06: dono lê proposta + evidência e aprova; Japanese UI fica como lacuna.
**Inclui.** `app/pages/cortex/decision-desk.vue` + `CortexDecisionDesk*.vue`: lista de casos (filtro por status), detalhe (texto da regra, condição, entities, escopo, correção de origem, casos afetados, `outcome`), aprovar (authority/enforcement) e rejeitar (motivo), erros do OPA mostrados com o motivo; seletor de usuário de dev (header `X-End-User-Id`), documentado; `data-test-id` `cortex-decision-desk-<element>-<control-type>` em todo controle; i18n só `en`; Playwright em container contra `make dev`.
**Critérios de aceite.**
1. Playwright: revisor corrige (via API) → caso aparece → dono aprova na tela → regra aparece no próximo pack.
2. Playwright: o corretor vê o botão de aprovar desabilitado/negado com o motivo.
3. Vitest dos componentes; ESLint + guard brand v2 + a11y sem violações; todos os controles com `data-test-id` (teste varre o DOM).
**Validação.** `make dev && make seed && make test-e2e-ui && make lint && make test`
**Dependências.** 27, 18, 20. **Tamanho.** ~400 linhas.

## 29. End-to-end nos dois workflows + README final
**Contexto.** Prova do stage 1: "uma correção aprovada melhora o próximo caso aplicável, fica fora do não relacionado e continua atribuível".
**Inclui.** `tests/e2e/` com **um** harness parametrizado por workflow (`custom_invoices`, `cash_application`) que só usa a API pública: caso feliz → correção → aprovação → próximo caso aplicável → não relacionado → verify → export → erasure; cenários 2, 3 e 4 com asserts explícitos; critério (a) rodando `make reindex` no meio; relatório por workflow; README completo (pré-requisitos, `make dev`, fluxo da demo, limites do emulador, desvios registrados, mapa IBM ↔ AWS).
**Critérios de aceite.**
1. Clone limpo → `make dev && make e2e` passa nos dois workflows.
2. Cenários 2, 3, 4 e critério (a) aparecem como testes nomeados no relatório.
3. Cada regra aplicada no pack final aponta para a correção, o corretor, o aprovador e a `policy_revision` (atribuível).
**Validação.**
```bash
git clone . /tmp/cortex-e2e && cd /tmp/cortex-e2e && cp .env.example .env
make dev && make e2e && make test-all && make lint && make typecheck
```
**Dependências.** 24, 26, 28. **Tamanho.** ~350 linhas.

## 30. LLM via LiteLLM (`LITELLM_BASE_URL=https://devs-ai.straker.ai`)
**Contexto.** Pedido do usuário em 2026-11-22. O LiteLLM expõe uma API compatível com OpenAI (`/v1/chat/completions`, `/v1/embeddings`). Hoje a PoC não usa LLM: a sugestão de regra é determinística (`DomainPack.correction_to_rule`, issue 22, "Não inclui: sugestão por LLM") e a busca usa vetores de teste fixos (`vector(8)`, migração 002). A regra 4 continua valendo: **nenhuma regra mandatória ou proibição vem de um LLM**; o LLM só propõe, a admissão valida (CEL compilado, entidades canônicas) e uma pessoa aprova.
**Inclui.**
- `app/clients/llm.py`: um `LlmClient` (Protocol) e a implementação `LiteLlmClient` (httpx, TLS verificado, timeout, retries com backoff, fail-closed: sem chave ou sem resposta → a funcionalidade fica desligada, nunca inventa saída). Config: `LITELLM_BASE_URL` (padrão `https://devs-ai.straker.ai`, no `.env.example` porque não é segredo), `LITELLM_CHAT_MODEL`, `LITELLM_EMBEDDING_MODEL`.
- **Chave:** `LITELLM_API_KEY` só no `.env` local (git-ignored, nunca no `.env.example`, no código nem na imagem). O `make provision` (Terraform) grava a chave no Secrets Manager do emulador (`cortex-poc/litellm-api-key`), como já é feito com os hashes das chaves de consumidor; o serviço lê de lá na inicialização. Política IAM `secretsmanager:GetSecretValue` só nesse segredo. A chave nunca aparece em log, erro ou resposta (teste).
- Usos, atrás de flags desligadas por padrão (decidir na abertura da issue, ver perguntas):
  - (a) **Sugestão de regra por LLM** no writeback: o pack de domínio monta o prompt a partir da correção; o LLM devolve um rascunho (texto, `targets`, condição CEL); o rascunho passa pela admissão (17) como `asserted_by.type=model`, authority ≤ 10, e vai para a fila de revisão. Condição inválida → `invalid_condition`, nada é proposto.
  - (b) **Embeddings na projeção de busca**: `SearchProjection` passa a gravar o embedding do texto do registro (migração: dimensão do `vector` do modelo escolhido); a busca continua só propondo candidatos, revalidados no ledger (20).
- Testes: `make test` continua hermético (um `FakeLlmClient`); integração contra um servidor fake local no compose de teste; um alvo opcional `make test-llm` que chama o LiteLLM real com dados **sintéticos** e só roda com `LITELLM_API_KEY` presente.
**Não inclui.** LLM no caminho de aplicabilidade/resolução (19) ou no verify (21); aprovação automática; envio de dados reais.
**Critérios de aceite.**
1. Sem `LITELLM_API_KEY`, tudo roda como hoje e `make test-all` passa; com a chave, `make test-llm` faz uma chamada real e mostra o modelo usado.
2. Um rascunho do LLM com CEL inválido nunca vira proposta; um válido vira proposta `proposed`, authority ≤ 10, `asserted_by.type=model`, nunca aprovado sozinho.
3. A chave não aparece em nenhum log, imagem, bundle ou resposta (teste que procura um canário, como o `check:bundle` da issue 27).
**Respostas (usuário, 2026-11-22).** O LiteLLM é a porta de **toda** IA da PoC: os usos (a) e (b). Chat: `anthropic/claude-sonnet-5-5` (listado em `/v1/models`, que expõe 49 modelos); embedding: `azure/text-embedding-ada-002` (1536 dimensões), também listado. Os dados sintéticos podem ser enviados: `devs-ai.straker.ai` é ambiente de UAT.
**Validação.** `make provision && make test-all && make test-llm`
**Dependências.** 22 (writeback), 20 (busca revalidada). **Tamanho.** ~300 linhas.

---

## Revisão do Nake e resposta do Lee (2026-11-22)

O que já está coberto e **não** vira issue:
- **Busca de regras por escopo e precedência:** issue 19 (`access_gate/applicability.py`, `resolve.py`), mais os golden sets e o e2e. As regras mandatórias nunca vêm da busca (cenário 3).
- **Normalizador de nome do pagador dentro do entity match:** issue 26. Ele é parte de `cash_application.resolve_entities`, não um passo próprio do gate.
- **Export:** issue 23.

As issues abaixo cobrem o que falta.

## 31. Escopo só aperta: checagem na entrada
**Contexto.** Nake: "lower levels can only tighten mandatory rules has to be checked when records come in, and precedence applied when they're read". A leitura já resolve (issue 19: uma restrição nunca perde). A entrada não checa nada: uma regra `default` local que contradiz uma proibição herdada (cenário 2) é admitida e só perde na hora de ler.
**Inclui.**
- Na admissão (`admission.validate_proposal`, com acesso ao ledger), procurar as **restrições aprovadas e vigentes nos escopos ancestrais** que compartilham um `target` com a proposta e são compatíveis em entidades (o mesmo filtro da aplicabilidade, sem avaliar CEL).
- Uma proposta num escopo mais baixo que **afrouxa** uma delas (`default`, ou polaridade oposta à da restrição) gera o problema `loosens_inherited_constraint`, com o `record_id` da restrição.
- Uma proposta que **aperta** (uma restrição igual ou mais estrita) passa.
- Decidir na abertura da issue: **bloquear** (422) ou **admitir com aviso** no caso de revisão, mostrado no Decision Desk como "esta regra local não pode vencer X". O padrão proposto é bloquear em admissões vindas de modelo ou de policy e avisar em admissões de pessoas.
- O seed `CI-L-SMALL-OMIT-REGNO` (cenário 2) passa a exigir a marcação explícita `exception_of: <record_id>`. Sem ela, o seed é recusado.
**Critérios de aceite.**
1. Uma `default` local contra uma `prohibition` global no mesmo target → `loosens_inherited_constraint`.
2. Uma `mandatory` local mais estrita → admitida.
3. A precedência na leitura continua igual (cenário 2 do golden set).
**Dependências.** 17, 19. **Dono sugerido.** WS3 (entrada) com WS2 (leitura).

## 32. Regiões: como registros Global chegam ao Japão (ADR + protótipo)
**Contexto.** Nake: "If each region is its own deployment, how do Global records reach Japan?" O PoC tem uma deployment só, e "Japan region for APPI" está em aberto (doc 12).
**Inclui.**
- `docs/adr/0002-regions.md` com três opções, cada uma com prós e contras (residência, latência, erasure, lineage):
  - (a) uma deployment global no Japão;
  - (b) deployments por região, com um **escopo Global publicado** que replica só registros aprovados e não pessoais, de leitura, com proveniência, `record_id` estável, e supersede e retire propagados;
  - (c) federação na leitura.
- Protótipo da opção escolhida no compose. Se for a (b): um job de replicação entre dois ledgers (dois bancos), idempotente; erasure na origem propaga; registro pessoal nunca sai da região.
**Critérios de aceite.**
1. ADR aprovado.
2. Com a (b): uma regra Global aprovada na região A aparece no pack da região B; retirada em A, o verify segura o pack em B; um registro pessoal não replica.
**Dependências.** decisão (WS6 + Legal/IBM). **Dono sugerido.** WS6 com WS1.

## 33. Supersede e expiração pela fila de revisão
**Contexto.** Nake: "Every record needs validity, supersedes and derived_from from day one." Os campos existem desde a issue 8 (envelope de 19 campos: `valid_from`/`valid_to`, `supersedes`, `derived_from`), e a vigência é aplicada na aplicabilidade e no verify. Faltam os fluxos.
**Inclui.**
- Uma proposta pode declarar `supersedes: <record_id>`. Aprovada, o antigo vira `superseded` (`ledger.supersede`, que já existe) e os packs que dependiam dele são segurados pelo verify.
- O dono pode definir `valid_to` (e `valid_from`) na aprovação.
- **Expiração básica (estágio 1):** um job marca como `stale` os registros aprovados depois de `valid_to`, a fila mostra o que expira em N dias, e a lineage é preservada.
**Critérios de aceite.**
1. Supersede pelo Decision Desk; o verify segura com `rule_no_longer_approved` e o novo registro aparece no próximo pack.
2. Uma regra expirada sai dos packs na data de negócio, e o verify segura o pack antigo.
3. Lineage: `derived_from` e `supersedes` aparecem no detalhe e no export.
**Dependências.** 18, 21. **Dono sugerido.** WS1.

## 34. Propostas de modelo: decidir se ficam fora do ledger até aprovar
**Contexto.** Nake: "model-proposed rules go through the review queue like everything else. They never write to the ledger directly." No PoC, toda proposta (de pessoa, policy ou modelo, issues 22 e 30) já passa pela fila e nunca é aprovada sozinha. Ela entra no ledger como `proposed`, invisível aos packs, que só leem `approved`.
**Inclui.**
- Decidir:
  - (a) manter assim, porque o ledger guarda o histórico inteiro, inclusive o rejeitado, como Rev. B regra 3 e Q5;
  - (b) propostas vivem só na fila de revisão (`review_proposal`, selado por registro), e o ledger recebe o registro **só na aprovação**.
- Se for a (b): migrar a admissão e o writeback, garantir que a rejeição continue auditável (decisão no ledger, proposta na fila), e ajustar erasure e export.
**Critérios de aceite.** Decisão registrada. Se for a (b): nenhuma linha em `memory_record` antes da aprovação, e os golden sets continuam 100%.
**Dependências.** decisão (Nake / WS3). **Dono sugerido.** WS3.

## 35. Workflow da Fase 1: subconjunto de Payment Reconciliation
**Contexto.** Lee (2026-11-22): o workflow da Fase 1 do PoC IBM **não é AskQ2C**. É um subconjunto pequeno de **Payment Reconciliation**, definido por Lee e Erika do Pre-Workshop ao Full Workshop, em dezembro. O AskQ2C foi a proposta original da IBM. O pack `cash_application` é o mais próximo: casar pagamento com fatura, nome do pagador, tarifa, 差押.
**Inclui.**
- Quando o workshop entregar o subconjunto:
  - ajustar o pack (renomear para `payment_reconciliation` ou estender `cash_application`) e os dados sintéticos;
  - montar o golden set do subconjunto;
  - definir o consumidor (hoje `askq2c-agent`) e o propósito;
  - definir os **revisores e donos reais por escopo** (hoje OWN-001/OWN-002 no seed).
- `custom_invoices` fica como o segundo pack que prova o D18.
- Renomear as referências "AskQ2C" na documentação.
**Critérios de aceite.** O golden set do subconjunto fecha 100%, e o `make e2e` roda nele.
**Dependências.** entregáveis do workshop (Lee, Erika). **Dono sugerido.** WS5.

## 36. Integração da Fase 1: um endpoint que devolve o context pack
**Contexto.** Nake: "Are MCP + REST and the release check in Phase 1 because of the IBM integration? If so, keep it to one endpoint that returns a context pack." O PoC expõe a API inteira, com três operações marcadas `x-mcp-tool`, mas não tem servidor MCP.
**Inclui.**
- Um **perfil de consumidor** com escopo `cortex:pack`, que só permite `request_context_pack` e `get_context_pack`, aplicado ao consumidor da IBM.
- Verify, corrections e as demais rotas ficam fora desse perfil (403), sem mudar o contrato.
- Decidir se verify (D3) entra na Fase 1. Sem ele, as regras mandatórias são consultivas.
**Critérios de aceite.** O consumidor da IBM recebe 403 em tudo que não é pack, e o contrato documenta o perfil.
**Dependências.** 16, 20. **Dono sugerido.** WS2.

## 37. Os seis cenários de falha como os primeiros testes do golden set (WS7)
**Contexto.** Nake: "Make the six failure scenarios the first tests in the WS7 golden set." Os cenários 2, 3 e 4 já são testes nomeados (golden sets e `make e2e`). Os cenários 1, 5 e 6 não têm teste próprio.
**Inclui.** Uma suíte `scenarios`, com um teste por cenário da Rev. B e a mesma numeração, rodando primeiro nos dois packs:
- **Cenário 1:** importar um TM com uma oferta expirada não renova aprovação nem torna a oferta publicável. Precisa de import pela admissão; hoje não há import.
- **Cenário 5:** pedir o pack depois de trocar o modelo de embedding ou rodar `make reindex` devolve o pack armazenado, e um pack novo diz que é novo. Gravar no `served_pack` a versão da projeção e do modelo de embedding.
- **Cenário 6:** um conflito encontrado por centenas de jobs, por exemplo 200 correções, gera **um** caso de revisão, com o trabalho afetado (`affected_cases`) inteiro visível no Decision Desk, paginado.
**Critérios de aceite.** `make scenarios` mostra os seis cenários nomeados, todos passando, nos dois workflows.
**Dependências.** 33 (expiração), import pela admissão (cenário 1). **Dono sugerido.** WS7.

---

## Fora do plano (pelo brief)
MCP server (só o contrato já marca as ferramentas), selos Merkle, replay completo, recertificação, consolidação, evolução de schema, migração v1, Elasticsearch, Redis Streams, SSO, UI em japonês, CI/CD, deploy real. Cenários 1, 5 e 6 da Rev. B não têm teste dedicado (o 5 é coberto em parte pela issue 20; o 6, pelo agrupamento da issue 17).

## 38. Decision Desk: o que o teste como usuário encontrou
**Contexto.** Teste da aplicação como usuário (2026-11-23, `docs/user-test-prompt.md`). Tudo funcionou, mas uma pessoa não conseguia entender ou fazer algumas coisas no Decision Desk.
**Inclui.**
1. Mostrar a vigência no detalhe, e permitir que o dono defina `valid_from`/`valid_to` na aprovação (a API aceita desde a issue 33).
2. "Veio de" em palavras: modelo, regra do pack, proposta direta (quem), import (sistema, quem, autoridade pedida e o teto), replicação (região).
3. O aviso de afrouxamento mostra a regra herdada pelo texto, e não por um id. O texto é aberto na leitura, nunca copiado para `source`.
4. A página inicial `/cortex` tem um link para o Decision Desk.
5. Os botões desabilitados parecem desabilitados.
6. Trocar a pessoa, o escopo ou o status limpa o aviso anterior.
7. Um painel "Vencendo em breve" (90 dias) no escopo, com o texto de cada regra.
**Critérios de aceite.** Os sete itens são verificados no navegador e cobertos por testes de unidade (frontend) e de integração (API).
**Dependências.** 28, 31, 33. **Dono sugerido.** WS3 (Decision Desk).

## 39. Testar em japonês e em inglês: conjuntos de exemplos selecionáveis na tela
**Contexto.** Pedido de 2026-11-23: uma forma de testar a aplicação na versão japonesa e numa versão em inglês, com um componente na tela que troque os exemplos usados.
**Inclui.**
- Um tenant de exemplo em inglês (`en`, Fictitious Group UK), com pessoas, dono e backup, um agente e um Decision Desk próprios.
- `data/examples/ja.json` e `en.json`: a mesma história nos dois idiomas (correção, regra local que afrouxa uma regra do grupo, import com validade).
- No Decision Desk, o seletor **Examples** troca o tenant, as pessoas, o escopo e a chave do Desk. O botão **Load example cases** cria os casos pela API pública e mostra o que fez. Carregar de novo não duplica nada.
- O rascunho de regra (do pack e do modelo) segue o idioma da nota do revisor.
**Critérios de aceite.** Nos dois idiomas, a primeira carga cria tudo, a segunda diz "Already there" em tudo, e no conjunto em inglês todo texto que uma pessoa lê está em inglês.
**Dependências.** 28, 30, 38. **Dono sugerido.** WS7 (testes) com WS3.

---

## Dev Brief da IBM e Workshop 1 (2026-11-22/23)

O brief muda o papel do Cortex no fluxo. **A IA da IBM faz o casamento** (rule engine → LLM → especialista no CAT), e **o Cortex é a memória dela**: regras aprovadas e versionadas, com dono, mais casos passados, e um loop que aprende com cada revisão. O CAT já mapeia pagador → cliente. A IBM não chama um release check: o audit trail dela recebe o rule ID, a versão, o aprovador e um ID de referência. Números: 2.700 pagamentos/mês, 74% automáticos, ~700 manuais (440 h), 192 regras para 164 clientes em Excel, meta de 95%+ (85% no PoC). Workshop em 7 dez; PoC até fevereiro.

Consequências para as decisões em aberto:
- **D3 (verify na Fase 1): não.** Fica para depois do PoC.
- **Regiões (32):** o PoC é um ambiente dedicado no Japão (opção a). O ADR 0002 vale para depois.
- **O normalizador de nome do pagador (26) sai do caminho da Fase 1:** o CAT já faz isso.
- **MCP para as ferramentas de IA dos especialistas:** a confirmar com a IBM.

As issues abaixo seguem a ordem de dependência. O pack novo, `cash_allocation`, é o fluxo da IBM. O `cash_application` fica como o pack anterior que prova o D18.

## 40. Formato de regra em duas camadas (pack `cash_allocation`)
**Contexto.** Slides 15–16: campos exatos que o rule engine da IBM roda, mais uma orientação em texto que o LLM lê, numa cópia só, guardada no Cortex. O slide 15 pede ao time de dev um exemplo real.
**Inclui.**
- Pack `cash_allocation`. O corpo da regra tem `text_ja` (a orientação para o LLM), `exception` (um tipo) e `engine` (parâmetros tipados por tipo):
  - pagamento combinado: como escolher as faturas;
  - tarifa bancária: tarifa esperada, máximo e tratamento;
  - diferença de imposto: arredondamento, tolerância e tratamento;
  - pagamento duplicado: reter, devolver ou aplicar na próxima fatura;
  - pagamento parcial: deixar em aberto, ou parcelas.
- O `condition` (CEL) diz quando a regra vale.
- O exemplo do slide 15 (Yamada Shoji, tarifa de ¥660), em `docs/examples/rule-format.md`.
**Critérios de aceite.** Cada tipo tem um schema validado na admissão, e um parâmetro inválido é recusado com o caminho do campo. O exemplo é admitido e aparece no pack de um caso que casa.
**Dependências.** 19, 22. **Dono sugerido.** WS5.

## 41. A entrada que a IA da IBM manda
**Contexto.** Slide 22: o número do cliente, o valor, a data, o gap, as faturas candidatas e os detalhes de remessa. O CAT já resolve o pagador.
**Inclui.** O schema do caso em `cash_allocation`. A entidade `customer:<id>` vem com confiança alta, sem normalizador. Variáveis de CEL: `payment` (cliente, valor, data, gap, número de candidatas, total das candidatas, se há remessa). Dados sintéticos e casos de teste.
**Critérios de aceite.** Um caso no formato da IBM recebe as regras do cliente e as globais, e nenhuma de outro cliente.
**Dependências.** 40.

## 42. Casos passados como memória
**Contexto.** Brief §03, itens 2 e 3: guardar cada caso revisado, confirmado ou corrigido, com o motivo; devolver casos parecidos.
**Inclui.**
- `POST /outcomes`: o resultado final, o motivo, quem decidiu, as faturas escolhidas e o tipo de exceção. Vira um registro `kind=case`, aprovado na origem, porque é um fato observado.
- O context pack devolve `similar_cases`: o mesmo cliente primeiro, depois o mesmo tipo de exceção e a proximidade do gap. Cada um revalidado no ledger.
- Os casos entram na erasure e na lineage.
**Critérios de aceite.** Um resultado gravado aparece como caso parecido no próximo pack do mesmo cliente, e nunca no de outro tenant.
**Dependências.** 41.

## 43. Perfil da IA da IBM: contexto, sincronização de regras, resultado
**Contexto.** Slide 20. O perfil "só pack" (36) passa a ter três operações. Sem verify.
**Inclui.** Os escopos `cortex:pack`, `cortex:rules-sync` e `cortex:outcomes` no `ibm-pr-agent`. Todo o resto continua 403. O audit trail recebe a referência: rule ID, versão e aprovador por regra, mais o `pack_id`.
**Dependências.** 36, 42, 44.

## 44. Sincronização de regras para o rule engine
**Contexto.** Brief §03, item 7: sincronizar diariamente ou a cada mudança.
**Inclui.** `GET /rules/sync`: as regras aprovadas, com versão, aprovador e vigência, e um cursor para pedir só o que mudou (inclusive regras retiradas). Para um rule engine, só os campos `engine`.
**Critérios de aceite.** Uma regra aprovada aparece no próximo sync, uma retirada aparece como removida, e o cursor não repete nada.
**Dependências.** 40.

## 45. Dono por cliente e grupos de clientes
**Contexto.** Slide 18: o dono é o especialista de caixa daquele cliente, com um backup. Uma matriz paga pelas subsidiárias.
**Inclui.** Donos por entidade (`customer:<id>`), que valem antes do dono do escopo. Uma hierarquia de clientes (matriz → subsidiárias): a regra da matriz vale para as subsidiárias. O OPA decide com o dono do cliente.
**Dependências.** 18, 41.

## 46. Detectar padrões ("a mesma correção 6 vezes")
**Contexto.** Brief §02 e slide 17.
**Inclui.** Um limite configurável por tipo de exceção (o padrão é 6 em 90 dias, por cliente). Um job em lote sobre o histórico de resultados (importado do CAT) que sugere regras. A métrica de sugestões aceitas pelos donos.
**Dependências.** 42.

## 47. Carga das 192 regras e aprovação em lote
**Contexto.** Slide 13: regras em Excel, indo para Markdown/JSON, com um guia de colunas.
**Inclui.** Um adaptador CSV/JSON → `/imports`, com o mapeamento de colunas. No Decision Desk, aprovar em lote por dono, com a autoridade e o tipo, item por item ainda registrado.
**Dependências.** 37, 40.

## 48. Alertas para o dono e a virada de abril
**Contexto.** Slide 18: data de revisão vencida, regras sobrescritas com frequência, conflitos. Em abril muitas regras mudam de uma vez.
**Inclui.** `review_by` por regra. A contagem de vezes que um resultado contrariou a regra aplicada. Um painel com esses alertas. A renovação de vigência em lote, pela fila.
**Dependências.** 42, 33.

## 49. Replay, shadow e as métricas de sucesso
**Contexto.** Slide 26.
**Inclui.** Um harness que repete o histórico de pagamentos contra as faturas abertas naquele dia e mede: a porcentagem com evidência de regra, a concordância com o especialista (shadow) e as sugestões aceitas. Roda com dados sintéticos até haver dados da IBM num ambiente próprio.
**Dependências.** 42, 46.

---

## Revisão da demo (2026-11-23): o que falta

## 50. O aviso de conflito no tenant japonês, e o critério "nenhuma regra obrigatória perdida"
**Contexto.** A regra dos ¥5.000 no conjunto 日本語 chegou com `inherited_conflicts: []`. Causa: o stack estava sem `make seed`, e o conjunto japonês dependia do seed para ter as regras de grupo (登録番号). O conjunto inglês traz as suas. A checagem também só olhava escopos acima do da proposta, nunca o próprio.
**Inclui.** O conjunto 日本語 traz as regras de grupo dele: as já presentes contam como "já estavam", e as que faltam são importadas e aprovadas pelo dono do grupo. O tenant `jp` ganha um dono na raiz (OWN-001/OWN-002). A checagem considera também as restrições do mesmo escopo. Um teste de ponta a ponta: num stack sem seed, carregar o conjunto japonês mostra os dois avisos.
**Dependências.** 31, 39.

## 51. A autoridade do formulário é a proposta
**Contexto.** A regra é proposta com 10, e o formulário abria com 20.
**Inclui.** O formulário abre com a autoridade proposta, e uma dica explica o teto de 10 para máquinas e importações e os valores usuais na aprovação.

## 52. Evidências e o que muda ao aprovar
**Contexto.** O doc 06 promete que a regra aprovada vale para toda fatura do cliente. A tela mostrava só o ID do caso de origem, sem trecho da correção, sem a fatura e sem prévia.
**Inclui.**
- A decisão guarda o caso corrigido, selado por registro e apagável.
- O detalhe mostra, por evidência, o trecho da correção (antes → depois, nota) e a fatura ou o pagamento.
- `GET /review-cases/{id}/preview`: o pack do caso de origem hoje e como ficaria com a regra aprovada, e a quem ela passa a valer (a condição em palavras).

## 53. Casos de cash application nos exemplos
**Contexto.** O escopo Q2C 入金消込チーム ficava vazio na demo.
**Inclui.** Os conjuntos 日本語 e English carregam também casos do pack `cash_allocation`: um resultado corrigido (regra aprendida), uma regra local que afrouxa uma regra de grupo, e uma importação. O tenant `en` ganha o escopo de cash application.

## 54. Login e modo de apresentação
**Contexto.** "Acting as (dev)", o aviso de `X-End-User-Id`, `make seed` e "Load example cases" aparecem para quem assiste à demo.
**Inclui.** Uma tela de login: escolher a pessoa, sem senha (no PoC; em produção, o SSO da IBM). O Desk usa a pessoa logada e permite sair. As ferramentas de dev ficam num painel separado, que só aparece com o modo de desenvolvedor ligado.

## 55. Interface em japonês
**Contexto.** Os rótulos estavam em inglês (lacuna do doc 06, P7). O idioma mudava com o tenant, não com a tela.
**Inclui.** O locale `ja` completo e um seletor de idioma da interface, independente do conjunto de exemplos e lembrado no navegador.

## 56. Frontend com cara de produto
**Inclui.** Um app shell: cabeçalho com a marca, navegação, idioma e usuário. O Desk em cards, com badges de estado, hierarquia tipográfica, estados vazios e de carregamento.
