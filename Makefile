# Cortex v2 PoC — `make` for everything (house rule). Every target calls
# docker compose; the host only needs Docker. Run `make help`.

COMPOSE   ?= docker compose
TEST_COMPOSE = docker compose -p cortex-poc-test -f docker-compose.test.yml
SVC       ?= org-brain-service
DEV_TOOLS  = $(COMPOSE) run --rm --no-deps org-brain-dev
PLATFORMS ?= linux/arm64,linux/amd64
BUILDER   ?= cortex-poc-multiarch

YELLOW := \033[33m
GREEN  := \033[32m
RESET  := \033[0m

.DEFAULT_GOAL := help
.PHONY: help init-env dev stop logs ps shell build build-tools build-multiarch lock \
        test test-integration probe-emulator lint format typecheck

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
	$(COMPOSE) up -d --build --wait
	@echo "$(GREEN)Stack up — org-brain-service: http://127.0.0.1:$${ORG_BRAIN_SERVICE_PORT:-5008}/health$(RESET)"

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

build-tools: init-env ## Build the test/lint image
	$(COMPOSE) --profile tools build org-brain-dev

build-multiarch: ## Build every image for linux/arm64 and linux/amd64 (no push, no load)
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 || docker buildx create --name $(BUILDER) --driver docker-container >/dev/null
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) \
		-f services/org-brain-service/Dockerfile --target runtime .
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) -f infra/postgres/Dockerfile .
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) infra/aws-emulator

probe-emulator: init-env build-tools ## AWS Emulator fidelity probe -> docs/emulator-probe-<version>.md (gate for AWS issues)
	$(COMPOSE) up -d --build --wait aws-emulator
	$(COMPOSE) run --rm aws-probe

lock: init-env ## Regenerate Pipfile.lock (SVC=<service>, default org-brain-service)
	SVC=$(SVC) $(COMPOSE) run --rm --no-deps pipenv-lock

# ── Checks (all in containers) ───────────────────────────────────────────────

test: build-tools ## Hermetic unit tests (no network, no stack)
	$(DEV_TOOLS) pipenv run pytest -q $(ARGS)

test-integration: ## Integration tests on an ephemeral stack (own project, no host ports, wiped after)
	@$(TEST_COMPOSE) build --quiet
	@$(TEST_COMPOSE) run --rm org-brain-it pipenv run pytest -q -m integration $(ARGS); \
		status=$$?; $(TEST_COMPOSE) down -v --remove-orphans >/dev/null 2>&1; exit $$status

lint: build-tools ## Ruff check + format check
	$(DEV_TOOLS) sh -c "pipenv run ruff check app tests scripts && pipenv run ruff format --check app tests scripts"

format: build-tools ## Ruff format + autofix (writes to the working tree)
	$(DEV_TOOLS) sh -c "pipenv run ruff format app tests scripts && pipenv run ruff check --fix app tests scripts"

typecheck: build-tools ## Pyright (strict on app/)
	$(DEV_TOOLS) pipenv run pyright
