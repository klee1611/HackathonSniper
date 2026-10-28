.DEFAULT_GOAL := help
.PHONY: help setup run dry-run test lint format type-check check setup-notion setup-notion-dry eval clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[32m%-17s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

setup: ## Install Python + dependencies and create .env
	uv sync
	@test -f .env || cp .env.example .env

run: ## Run discovery (writes to Notion)
	uv run python -m hackathon_sniper

dry-run: ## Run discovery without writing to Notion
	uv run python -m hackathon_sniper --dry-run

test: ## Unit + scenario tests (no network)
	uv run pytest

lint: ## Ruff lint + format check
	uv run ruff check . && uv run ruff format --check .

format: ## Auto-format and fix lint
	uv run ruff format . && uv run ruff check --fix .

type-check: ## mypy (strict)
	uv run mypy

check: lint type-check test ## Everything CI runs

setup-notion: ## One-time Notion setup: make setup-notion PARENT=<page id or URL>
	uv run python scripts/setup_notion.py $(if $(PARENT),--parent-page "$(PARENT)")

setup-notion-dry: ## Preview Notion setup, no writes: make setup-notion-dry PARENT=<page>
	uv run python scripts/setup_notion.py --dry-run $(if $(PARENT),--parent-page "$(PARENT)")

eval: ## Verifier extraction accuracy (real Groq calls)
	uv run python evals/extraction_eval.py

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache
