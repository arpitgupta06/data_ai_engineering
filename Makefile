# Developer entrypoint for the repo. Windows without make: use .\dev.ps1 <target>.
#
# Everything runs from the project directory, so paths in compose, pyproject and
# the scripts keep resolving the way they do when invoked by hand.

PROJECT := projects/01-chain-risk-copilot
COMPOSE := docker compose -f infra/docker-compose.yml
RUN     := cd $(PROJECT) &&
LONG_RUNNING := redpanda redpanda-console minio postgres-ops

.DEFAULT_GOAL := help
.PHONY: help setup up down ps logs smoke ingest fmt lint test verify clean

help: ## List the targets
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

setup: ## Create infra/.env if absent, uv sync --all-groups, install pre-commit hooks
	@$(RUN) if [ -f infra/.env ]; then echo "infra/.env exists - left untouched"; \
	  else cp infra/.env.example infra/.env && echo "created infra/.env from .env.example"; fi
	$(RUN) uv sync --all-groups
	$(RUN) uv run pre-commit install

# `up --wait` counts the one-shot minio-init exiting (0) as a failure, so wait on the
# long-running services only, then run minio-init separately and use its exit code.
up: ## Start the stack and wait until every service is healthy
	$(RUN) $(COMPOSE) up -d --wait $(LONG_RUNNING)
	$(RUN) $(COMPOSE) up --no-deps --exit-code-from minio-init minio-init

down: ## Stop the stack, KEEP data
	$(RUN) $(COMPOSE) down

ps: ## Show service status
	$(RUN) $(COMPOSE) ps

logs: ## Follow logs (all services, or SERVICE=redpanda)
	$(RUN) $(COMPOSE) logs -f --tail=100 $(SERVICE)

smoke: ## Run the end-to-end smoke test against the running stack
	$(RUN) uv run python scripts/smoke_test.py

ingest: ## Ingest the 7-day window into bronze (needs the stack up)
	$(RUN) uv run python -m chainrisk.ingest.bronze

fmt: ## Format and auto-fix with ruff
	$(RUN) uv run ruff format .
	$(RUN) uv run ruff check --fix .

lint: ## ruff check, ruff format --check, mypy
	$(RUN) uv run ruff check .
	$(RUN) uv run ruff format --check .
	$(RUN) uv run mypy

test: ## Unit tests (no running stack needed)
	$(RUN) uv run pytest

verify: lint test ## lint + test: the pre-push check

clean: ## DESTROYS DATA: stop the stack and delete its volumes (down -v)
	$(RUN) $(COMPOSE) down -v
