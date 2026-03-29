.DEFAULT_GOAL := help
.PHONY: help install build test test-unit test-integration test-smoke test-watch test-coverage clean dev run lint type-check format setup

# Configuration
NODE_ENV ?= development
PNPM := pnpm
TSC := $(PNPM) tsc
JEST := $(PNPM) jest

# Colors for beautiful output
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m
CHECKMARK := ✅
ROCKET := 🚀
WRENCH := 🔧
TEST_TUBE := 🧪

help: ## Show this help message
	@echo "$(CYAN)$(ROCKET) Hackathon Discovery Agent - Node.js/TypeScript$(RESET)"
	@echo "================================================"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { printf "  $(GREEN)%-15s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install dependencies with pnpm
	@echo "$(CYAN)$(WRENCH) Installing dependencies...$(RESET)"
	$(PNPM) install
	@echo "$(GREEN)$(CHECKMARK) Dependencies installed!$(RESET)"

setup: install ## Setup complete development environment
	@echo "$(CYAN)$(WRENCH) Setting up development environment...$(RESET)"
	@if [ ! -f .env ]; then cp .env.example .env; fi
	@echo "$(GREEN)$(CHECKMARK) Development environment ready!$(RESET)"
	@echo ""
	@echo "$(YELLOW)Next steps:$(RESET)"
	@echo "  1. Edit .env with your API keys for production mode"
	@echo "  2. Run '$(GREEN)make test-unit$(RESET)' for isolated unit tests"
	@echo "  3. Run '$(GREEN)make dev$(RESET)' to start development mode"

build: ## Build TypeScript to JavaScript (production)
	@echo "$(CYAN)$(WRENCH) Building TypeScript...$(RESET)"
	$(TSC) --project tsconfig.build.json
	@echo "$(GREEN)$(CHECKMARK) Build complete!$(RESET)"

build-all: ## Build TypeScript including tests for production
	@echo "$(CYAN)$(WRENCH) Building all TypeScript files...$(RESET)"
	$(TSC)
	@echo "$(GREEN)$(CHECKMARK) Full build complete!$(RESET)"

dev: ## Start in development mode (no build required)
	@echo "$(CYAN)$(ROCKET) Starting development mode...$(RESET)"
	NODE_ENV=development $(PNPM) run dev

run: build-all ## Run production build
	@echo "$(CYAN)$(ROCKET) Running production build...$(RESET)"
	NODE_ENV=production node dist/src/index.js

# Testing commands with proper naming
test: test-unit ## Run all unit tests (default test command)

test-unit: ## Run unit tests with mocks (ZERO external dependencies)
	@echo "$(CYAN)$(TEST_TUBE) Running unit tests with mocks...$(RESET)"
	@echo "$(YELLOW)$(CHECKMARK) No API keys needed - complete isolation!$(RESET)"
	NODE_ENV=test $(JEST) --config=jest.mock.config.js
	@echo "$(GREEN)$(CHECKMARK) Unit tests completed!$(RESET)"

test-integration: ## Run integration tests with real APIs (requires API keys)
	@echo "$(CYAN)$(TEST_TUBE) Running integration tests...$(RESET)"
	@echo "$(YELLOW)⚠️  Requires real API keys in .env$(RESET)"
	NODE_ENV=test $(JEST)
	@echo "$(GREEN)$(CHECKMARK) Integration tests completed!$(RESET)"

test-smoke: build-all ## Run smoke test of full pipeline (with mocks)
	@echo "$(CYAN)$(TEST_TUBE) Running smoke test...$(RESET)"
	NODE_ENV=test node dist/src/index.js
	@echo "$(GREEN)$(CHECKMARK) Smoke test completed!$(RESET)"

test-watch: ## Run unit tests in watch mode
	@echo "$(CYAN)$(TEST_TUBE) Running tests in watch mode...$(RESET)"
	NODE_ENV=test $(JEST) --config=jest.mock.config.js --watch

test-coverage: ## Run tests with coverage report
	@echo "$(CYAN)$(TEST_TUBE) Running tests with coverage...$(RESET)"
	NODE_ENV=test $(JEST) --config=jest.mock.config.js --coverage
	@echo "$(GREEN)$(CHECKMARK) Coverage report generated!$(RESET)"

lint: ## Lint code with ESLint
	@echo "$(CYAN)$(WRENCH) Linting code...$(RESET)"
	$(PNPM) run lint

lint-fix: ## Fix linting issues automatically
	@echo "$(CYAN)$(WRENCH) Fixing linting issues...$(RESET)"
	$(PNPM) run lint:fix
	@echo "$(GREEN)$(CHECKMARK) Linting fixes applied!$(RESET)"

type-check: ## Run TypeScript type checking
	@echo "$(CYAN)$(WRENCH) Type checking...$(RESET)"
	$(TSC) --noEmit
	@echo "$(GREEN)$(CHECKMARK) Type checking passed!$(RESET)"

format: ## Format code with Prettier
	@echo "$(CYAN)$(WRENCH) Formatting code...$(RESET)"
	$(PNPM) run format
	@echo "$(GREEN)$(CHECKMARK) Code formatted!$(RESET)"

format-check: ## Check code formatting
	@echo "$(CYAN)$(WRENCH) Checking code formatting...$(RESET)"
	$(PNPM) run format:check

clean: ## Clean build artifacts and cache
	@echo "$(CYAN)$(WRENCH) Cleaning build artifacts...$(RESET)"
	$(PNPM) run clean
	$(PNPM) store prune
	@echo "$(GREEN)$(CHECKMARK) Cleaned!$(RESET)"

ci: lint type-check test-unit ## Run CI checks (lint, type-check, unit tests)
	@echo "$(GREEN)$(CHECKMARK) All CI checks passed!$(RESET)"

pre-deploy: ci build ## Pre-deployment checks and build
	@echo "$(GREEN)$(CHECKMARK) Ready for deployment!$(RESET)"

# Show current status
status: ## Show project status
	@echo "$(CYAN)Project Status:$(RESET)"
	@echo "  Node version: $$(node --version)"
	@echo "  pnpm version: $$(pnpm --version)"
	@echo "  TypeScript:   $$($(TSC) --version)"
	@if [ -f .env ]; then echo "  Environment:  $(GREEN)Configured$(RESET)"; else echo "  Environment:  $(YELLOW)Not configured$(RESET)"; fi
	@if [ -d node_modules ]; then echo "  Dependencies: $(GREEN)Installed$(RESET)"; else echo "  Dependencies: $(RED)Not installed$(RESET)"; fi
	@if [ -d dist ]; then echo "  Build:        $(GREEN)Built$(RESET)"; else echo "  Build:        $(YELLOW)Not built$(RESET)"; fi
