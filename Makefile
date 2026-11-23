# Cortex v2 PoC — `make` for everything (house rule). Every target calls
# docker compose; the host only needs Docker. Run `make help`.

COMPOSE   ?= docker compose
TEST_COMPOSE = docker compose -p cortex-poc-test -f docker-compose.test.yml
SVC       ?= org-brain-service
DEV_TOOLS  = $(COMPOSE) run --rm --no-deps org-brain-dev
FRONTEND   = $(COMPOSE) run --rm --no-deps frontend-tools
PLATFORMS ?= linux/arm64,linux/amd64
BUILDER   ?= cortex-poc-multiarch

YELLOW := \033[33m
GREEN  := \033[32m
RESET  := \033[0m

.DEFAULT_GOAL := help
.PHONY: help init-env dev stop logs ps shell build build-tools build-multiarch lock erase-record erase-tenant \
        test scenarios test-integration test-llm test-e2e-ui e2e test-policies test-all opa-reload probe-emulator \
        tf-cache provision tf-plan tf-lock gen-data migrate migrate-check reindex seed contract contract-check lint format typecheck

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Stack ────────────────────────────────────────────────────────────────────

init-env: ## Create .env from .env.example, or add the keys it lacks (never overwrites)
	@[ -f .env ] || { cp .env.example .env; echo "$(GREEN)Created .env from .env.example$(RESET)"; }
	@grep -E '^[A-Z_][A-Z0-9_]*=' .env.example | while IFS= read -r line; do \
		key=$${line%%=*}; \
		grep -qE "^$${key}=" .env || { echo "$$line" >> .env; echo "$(GREEN)Added $$key to .env$(RESET)"; }; \
	done

dev: init-env ## Build and start the whole stack, wait until healthy
	@echo "$(YELLOW)Starting the Cortex PoC stack...$(RESET)"
	$(COMPOSE) up -d --build --wait aws-emulator postgres opa
	@$(MAKE) --no-print-directory provision
	$(COMPOSE) up -d --build --wait
	@echo "$(GREEN)Stack up — org-brain-service: http://127.0.0.1:$${ORG_BRAIN_SERVICE_PORT:-5008}/health · Cortex: http://localhost:$${FRONTEND_PORT:-5001}/cortex$(RESET)"

stop: ## Stop the stack and DELETE its volumes (the stack is ephemeral)
	$(COMPOSE) --profile tools down -v --remove-orphans

logs: ## Follow logs (all services, or SVC=<name>)
	$(COMPOSE) logs -f $(if $(filter command line,$(origin SVC)),$(SVC),)

ps: ## Show service status
	$(COMPOSE) ps

shell: ## Open a shell in a running service (SVC=<name>)
	$(COMPOSE) exec $(SVC) sh

# ── Build ────────────────────────────────────────────────────────────────────

build: ## Build the stack images for the host architecture
	$(COMPOSE) build

build-tools: init-env ## Build the test/lint images (backend and frontend)
	$(COMPOSE) --profile tools build org-brain-dev frontend-tools

build-multiarch: ## Build every image for linux/arm64 and linux/amd64 (no push, no load)
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 || docker buildx create --name $(BUILDER) --driver docker-container >/dev/null
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) \
		-f services/org-brain-service/Dockerfile --target runtime .
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) -f infra/postgres/Dockerfile .
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) infra/aws-emulator

tf-cache:
	@docker volume inspect cortex-poc-terraform-plugins >/dev/null 2>&1 || docker volume create cortex-poc-terraform-plugins >/dev/null

provision: init-env tf-cache ## Terraform apply against the AWS Emulator (idempotent; also run by make dev)
	$(COMPOSE) run --rm terraform apply

tf-plan: init-env tf-cache ## Terraform plan; exit 0 = no changes, 2 = changes pending
	$(COMPOSE) run --rm terraform plan

tf-lock: init-env tf-cache ## Refresh provider hashes for linux_arm64 + linux_amd64 (.terraform.lock.hcl)
	$(COMPOSE) run --rm terraform-lock

probe-emulator: init-env build-tools ## AWS Emulator fidelity probe -> docs/emulator-probe-<version>.md (gate for AWS issues)
	$(COMPOSE) up -d --build --wait aws-emulator
	$(COMPOSE) run --rm aws-probe

migrate: init-env ## Alembic upgrade head as the table owner (make dev runs it too)
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) run --rm --build org-brain-migrate

migrate-check: build-tools ## One Alembic head, every revision id <= 32 chars (also part of make lint)
	$(DEV_TOOLS) pipenv run python scripts/check_migrations.py

seed: ## Register the seed's tenants and (re)load their scope tree and grants into OPA
	$(COMPOSE) exec org-brain-service pipenv run python -m app.cli.seed

reindex: ## Delete and rebuild every tenant's search projection from the ledger (stack must be up)
	$(COMPOSE) run --rm --no-deps org-brain-service pipenv run python -m app.cli.reindex

migrate-hq: ## The regions prototype (ADR 0002): migrate the HQ region's ledger (database org_brain_hq)
	$(COMPOSE) run --rm -e POSTGRES_DB=org_brain_hq org-brain-migrate

replicate: migrate-hq ## Copy HQ's published Global records into this region's ledger (idempotent; ADR 0002, issue 32)
	$(COMPOSE) run --rm --no-deps org-brain-service pipenv run python -m app.cli.replicate --from-db org_brain_hq --region hq

load-rules: init-env ## Load customer rules from a spreadsheet (issue 47): SHEET= GUIDE= (default: the sample in data/examples/rule-sheet)
	@set -a && . ./.env && set +a && $(COMPOSE) run --rm --no-deps -e CORTEX_E2E_AGENT_KEY org-brain-service \
		pipenv run python -m app.cli.load_rules --sheet $(or $(SHEET),/data/examples/rule-sheet/rules.csv) \
		--guide $(or $(GUIDE),/data/examples/rule-sheet/column-guide.json) \
		--scope jp/finance/q2c-cash-application --by CS-101 --api http://aws-emulator:5008

mine-patterns: ## Suggest rules from a history of resolved payments (issue 46): HISTORY= (default: the synthetic one)
	$(COMPOSE) run --rm --no-deps org-brain-service pipenv run python -m app.cli.mine_patterns \
		--tenant jp --domain cash_allocation --scope jp/finance/q2c-cash-application \
		--history $(or $(HISTORY),/data/synthetic/cash_allocation/history.jsonl) --now 2026-11-23

erase-record: ## Crypto-shred a record and its descendants: TENANT= RECORD= BY= REASON=
	$(COMPOSE) exec org-brain-service pipenv run python -m app.cli.erase record \
		--tenant "$(TENANT)" --record "$(RECORD)" --by "$(BY)" --reason "$(REASON)"

erase-tenant: ## Crypto-shred a whole tenant (cannot be undone): TENANT= BY= REASON= CONFIRM=yes
	$(COMPOSE) exec org-brain-service pipenv run python -m app.cli.erase tenant \
		--tenant "$(TENANT)" --by "$(BY)" --reason "$(REASON)" $(if $(filter yes,$(CONFIRM)),--yes,)

OPENAPI_TS = npx --yes openapi-typescript@7.13.0

contract: build-tools ## Regenerate the API spec (libraries/cortex-contract/openapi.json) and its TS types
	$(DEV_TOOLS) pipenv run python -m scripts.export_openapi --out /libraries/cortex-contract/openapi.json
	$(COMPOSE) run --rm contract-ts sh -c "$(OPENAPI_TS) openapi.json -o ts/cortex.d.ts"

contract-check: build-tools ## Fail if the committed spec or TS types differ from what the code produces
	$(DEV_TOOLS) pipenv run python -m scripts.export_openapi --out /libraries/cortex-contract/openapi.json --check
	$(COMPOSE) run --rm contract-ts sh -c "$(OPENAPI_TS) openapi.json -o /tmp/cortex.d.ts >/dev/null && cmp ts/cortex.d.ts /tmp/cortex.d.ts && echo 'ts/cortex.d.ts matches the spec'"

gen-data: build-tools ## Generate data/synthetic/ (deterministic; rerun must leave git diff empty)
	$(COMPOSE) run --rm synthetic

lock: init-env ## Regenerate Pipfile.lock (SVC=<service>, default org-brain-service)
	SVC=$(SVC) $(COMPOSE) run --rm --no-deps pipenv-lock

# ── Checks (all in containers) ───────────────────────────────────────────────

test: build-tools ## Hermetic unit tests (no network): backend, then frontend Vitest + bundle check
	$(DEV_TOOLS) pipenv run pytest -q $(ARGS)
	$(FRONTEND) npm run test:run
	$(FRONTEND) npm run check:bundle

E2E = $(COMPOSE) run --rm --no-deps -T \
	-e CORTEX_E2E_AGENT_KEY -e CORTEX_E2E_IBM_KEY -e CORTEX_DESK_API_KEY \
	-v ./tests/e2e:/e2e:ro -v ./.e2e:/state org-brain-service pipenv run python /e2e/run.py

e2e: init-env ## End to end on a FRESH running stack (make dev), both workflows, public API; reindex and erasure in between
	@$(COMPOSE) ps --status running --services | grep -qx org-brain-service || \
		{ echo "the stack is not up: run make dev"; exit 1; }
	@mkdir -p .e2e && rm -f .e2e/erase.txt
	@$(MAKE) --no-print-directory seed >/dev/null
	@set -a && . ./.env && set +a && $(E2E) --phase before
	@echo "== make reindex (criterion a)" && $(MAKE) --no-print-directory reindex
	@set -a && . ./.env && set +a && $(E2E) --phase after-reindex
	@while read -r tenant record; do \
		echo "== make erase-record $$record"; \
		$(MAKE) --no-print-directory erase-record TENANT=$$tenant RECORD=$$record BY=e2e REASON="e2e: erase the learned rule" </dev/null >/dev/null || exit 1; \
	done < .e2e/erase.txt
	@set -a && . ./.env && set +a && $(E2E) --phase after-erasure
	@set -a && . ./.env && set +a && $(E2E) --phase report

test-llm: build-tools ## The real LiteLLM, opt-in (needs LITELLM_API_KEY in .env): chat drafts an admissible rule, embeddings have meaning
	$(COMPOSE) --profile tools run --rm --no-deps org-brain-llm-check pipenv run pytest -q -s -m llm tests/llm $(ARGS)

test-e2e-ui: ## Playwright (Decision Desk) against the running stack: run `make dev && make seed` first
	@$(COMPOSE) ps --status running --services | grep -qx frontend || { echo "the stack is not up: run make dev && make seed"; exit 1; }
	$(COMPOSE) --profile tools build e2e-ui
	$(COMPOSE) --profile tools run --rm e2e-ui

scenarios: ## The six Rev. B failure scenarios, one named test each (ephemeral stack)
	@$(TEST_COMPOSE) build --quiet
	@$(TEST_COMPOSE) run --rm org-brain-it pipenv run pytest -q -rA -m integration tests/integration/test_scenarios.py $(ARGS); \
		status=$$?; $(TEST_COMPOSE) down -v --remove-orphans >/dev/null 2>&1; exit $$status

test-integration: ## Integration tests on an ephemeral stack (own project, no host ports, wiped after)
	@$(TEST_COMPOSE) build --quiet
	@$(TEST_COMPOSE) run --rm org-brain-it pipenv run pytest -q -m integration $(ARGS); \
		status=$$?; $(TEST_COMPOSE) down -v --remove-orphans >/dev/null 2>&1; exit $$status

test-policies: init-env ## opa test on policies/ (in a container)
	$(COMPOSE) run --rm opa-tools test /policies --verbose

test-all: scenarios test test-integration test-policies ## Everything that defines "tests pass" (ADR 0001): the six scenarios first
	@echo "$(GREEN)All test tiers passed$(RESET)"

opa-reload: ## Rebuild the policy bundle and restart OPA (after editing policies/)
	$(COMPOSE) run --rm opa-bundle
	$(COMPOSE) restart opa

lint: build-tools ## Ruff + migrate-check (Python), opa check --strict + Regal (Rego), terraform fmt -check, ESLint + v2 style guard (frontend)
	$(DEV_TOOLS) sh -c "pipenv run ruff check app tests scripts alembic && pipenv run ruff format --check app tests scripts alembic"
	$(DEV_TOOLS) pipenv run python scripts/check_migrations.py
	$(COMPOSE) run --rm --no-deps --entrypoint terraform terraform-lock fmt -check -recursive -diff /workspace/infra/terraform
	$(COMPOSE) run --rm opa-tools check --strict /policies
	$(COMPOSE) run --rm regal lint /policies
	$(FRONTEND) npm run lint

format: build-tools ## Ruff format + autofix, terraform fmt (writes to the working tree)
	$(COMPOSE) run --rm --no-deps --entrypoint terraform terraform-lock fmt -recursive /workspace/infra/terraform
	$(DEV_TOOLS) sh -c "pipenv run ruff format app tests scripts alembic && pipenv run ruff check --fix app tests scripts alembic"

typecheck: build-tools ## Pyright (strict on app/), vue-tsc (frontend)
	$(DEV_TOOLS) pipenv run pyright
	$(FRONTEND) npm run typecheck
