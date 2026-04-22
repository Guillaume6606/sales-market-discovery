# =============================================================================
# Market Discovery Makefile
# Unified command center for development, testing, and operations
# =============================================================================
.PHONY: help
.DEFAULT_GOAL := help

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
CYAN := \033[0;36m
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

DC := docker-compose
DC_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

# Load deploy config if present
-include .deploy.env
export

SSH_USER ?= root
SSH_PORT ?= 22
DEPLOY_DIR ?= /opt/market-discovery
SSH_CMD := ssh -p $(SSH_PORT) $(SSH_USER)@$(SSH_HOST)
SCP_CMD := scp -P $(SSH_PORT)

# =============================================================================
# Help
# =============================================================================

help: ## Show this help message
	@echo "$(CYAN)========================================================$(NC)"
	@echo "$(CYAN)  Market Discovery - Development & Operations$(NC)"
	@echo "$(CYAN)========================================================$(NC)"
	@echo ""
	@echo "$(GREEN)Development:$(NC)"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E '^(install|fmt|lint|test)' | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Docker:$(NC)"
	@grep -hE '^(up|down|stop|logs|build|rebuild|restart|status|health|clean):.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Services:$(NC)"
	@grep -hE '^(backend|ingestion|ui|db)-.*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Local Dev (no Docker):$(NC)"
	@grep -hE '^dev-.*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Operations:$(NC)"
	@grep -hE '^(audit|ingest:|migrate).*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Deployment (VPS):$(NC)"
	@grep -hE '^(deploy|remote-|backup|setup-vps|telegram-|caddy-).*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-28s$(NC) %s\n", $$1, $$2}'

# =============================================================================
# Development
# =============================================================================

install: ## Install all dependencies
	@echo "$(GREEN)Installing dependencies...$(NC)"
	uv sync --all-extras
	@echo "$(GREEN)Done$(NC)"

fmt: ## Format code (ruff)
	@echo "$(GREEN)Formatting...$(NC)"
	uv run ruff check --fix .
	uv run ruff format .
	@echo "$(GREEN)Done$(NC)"

lint: ## Lint code (ruff)
	uv run ruff check .

test: ## Run unit tests
	uv run pytest tests/unit/ -q

test-v: ## Run unit tests (verbose)
	uv run pytest tests/unit/ -v

test-smoke: ## Run smoke tests inside Docker (real APIs + container health)
	docker-compose run --rm \
		-v $(PWD)/tests:/app/tests \
		ingestion \
		uv run --with pytest --with pytest-asyncio --with httpx \
		pytest tests/smoke/ -v --tb=short -x

# =============================================================================
# Docker Compose
# =============================================================================

up: ## Start all services
	@echo "$(GREEN)Starting all services...$(NC)"
	$(DC) up -d --build
	@echo ""
	@echo "$(GREEN)Services started:$(NC)"
	@echo "  Backend:    $(CYAN)http://localhost:8000$(NC)"
	@echo "  UI:         $(CYAN)http://localhost:8501$(NC)"
	@echo "  PostgreSQL: $(CYAN)localhost:5432$(NC)"
	@echo "  Redis:      $(CYAN)localhost:6379$(NC)"

down: ## Stop all services and remove volumes
	@echo "$(YELLOW)Stopping services...$(NC)"
	$(DC) down -v
	@echo "$(GREEN)Done$(NC)"

stop: ## Stop all services (keep volumes)
	$(DC) down

logs: ## Follow logs from all services
	$(DC) logs -f --tail=100

build: ## Build all Docker images
	@echo "$(GREEN)Building images...$(NC)"
	$(DC) build
	@echo "$(GREEN)Done$(NC)"

rebuild: ## Rebuild images without cache
	@echo "$(GREEN)Rebuilding images (no cache)...$(NC)"
	$(DC) build --no-cache
	@echo "$(GREEN)Done$(NC)"

restart: ## Restart all services
	$(DC) restart

status: ## Show running containers
	$(DC) ps

health: ## Check health of local services
	@echo "$(GREEN)Checking service health...$(NC)"
	@echo -n "  Backend:  "
	@curl -sf -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null && echo "$(GREEN)OK$(NC)" || echo "$(RED)FAIL$(NC)"
	@echo -n "  UI:       "
	@curl -sf -o /dev/null -w "%{http_code}" http://localhost:8501 2>/dev/null && echo "$(GREEN)OK$(NC)" || echo "$(RED)FAIL$(NC)"
	@echo -n "  Postgres: "
	@$(DC) exec -T db pg_isready -U app >/dev/null 2>&1 && echo "$(GREEN)OK$(NC)" || echo "$(RED)FAIL$(NC)"
	@echo -n "  Redis:    "
	@$(DC) exec -T redis redis-cli ping >/dev/null 2>&1 && echo "$(GREEN)OK$(NC)" || echo "$(RED)FAIL$(NC)"

clean: ## Stop services, remove volumes and images
	@echo "$(YELLOW)Cleaning up...$(NC)"
	$(DC) down -v --rmi local
	@echo "$(GREEN)Done$(NC)"

# =============================================================================
# Per-Service Targets
# =============================================================================

backend-logs: ## Follow backend logs
	$(DC) logs -f backend

ingestion-logs: ## Follow ingestion worker logs
	$(DC) logs -f ingestion

ui-logs: ## Follow UI logs
	$(DC) logs -f ui

backend-sh: ## Shell into backend container
	$(DC) exec backend bash

ingestion-sh: ## Shell into ingestion container
	$(DC) exec ingestion bash

ui-sh: ## Shell into UI container
	$(DC) exec ui bash

backend-restart: ## Restart backend only
	$(DC) restart backend

ingestion-restart: ## Restart ingestion worker only
	$(DC) restart ingestion

ui-restart: ## Restart UI only
	$(DC) restart ui

db-shell: ## Open psql shell
	$(DC) exec db psql -U app -d app

# =============================================================================
# Local Dev (outside Docker, requires db+redis running)
# =============================================================================

dev-backend: ## Run backend locally (port 8000)
	uv run uvicorn backend.main:app --reload

dev-ui: ## Run Streamlit UI locally (port 8501)
	uv run streamlit run ui/app.py

dev-worker: ## Run ingestion worker locally
	uv run arq ingestion.worker.WorkerSettings

# =============================================================================
# Database
# =============================================================================

migrate: ## Apply database migrations
	uv run alembic upgrade head

migrate-new: ## Create new migration (usage: make migrate-new MSG="add column")
	uv run alembic revision --autogenerate -m "$(MSG)"

# =============================================================================
# Operations
# =============================================================================

audit: ## Run connector audit in Docker (all connectors)
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		xvfb-run -a uv run python -m ingestion.audit_cli \
		--html-only

audit-vinted: ## Run Vinted-only audit in Docker
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		xvfb-run -a uv run python -m ingestion.audit_cli \
		--connectors vinted --html-only

audit-lbc: ## Run LeBonCoin-only audit in Docker
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		xvfb-run -a uv run python -m ingestion.audit_cli \
		--connectors leboncoin --html-only

audit-ebay: ## Run eBay-only audit in Docker
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		xvfb-run -a uv run python -m ingestion.audit_cli \
		--connectors ebay --html-only

ingest: ## Run full ingestion in Docker
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		uv run python -c "import asyncio; from ingestion.ingestion import run_full_ingestion_all; asyncio.run(run_full_ingestion_all())"

# =============================================================================
# Deployment (VPS)
# =============================================================================

deploy: ## Full deploy to VPS (sync + build + migrate + restart)
	@test -n "$(SSH_HOST)" || (echo "$(RED)Set SSH_HOST in .deploy.env$(NC)" && exit 1)
	@echo "$(GREEN)Deploying to $(SSH_HOST)...$(NC)"
	bash infra/deploy.sh

deploy-quick: ## Quick deploy to VPS (sync + restart, no rebuild)
	@test -n "$(SSH_HOST)" || (echo "$(RED)Set SSH_HOST in .deploy.env$(NC)" && exit 1)
	@echo "$(GREEN)Quick deploy to $(SSH_HOST)...$(NC)"
	SSH_QUICK=1 bash infra/deploy.sh

deploy-env: ## Copy .env.prod to VPS as .env (run once or after config changes)
	@test -n "$(SSH_HOST)" || (echo "$(RED)Set SSH_HOST in .deploy.env$(NC)" && exit 1)
	@test -f .env.prod || (echo "$(RED).env.prod not found$(NC)" && exit 1)
	$(SCP_CMD) .env.prod $(SSH_USER)@$(SSH_HOST):$(DEPLOY_DIR)/.env
	@echo "$(GREEN).env.prod copied to $(SSH_HOST):$(DEPLOY_DIR)/.env$(NC)"

remote-logs: ## Follow remote logs (all services)
	$(SSH_CMD) "cd $(DEPLOY_DIR) && $(DC_PROD) logs -f --tail=100"

remote-health: ## Check remote service health
	@echo "$(GREEN)Checking $(SSH_HOST)...$(NC)"
	@$(SSH_CMD) "cd $(DEPLOY_DIR) && $(DC_PROD) ps"
	@echo ""
	@curl -sf https://$(DOMAIN)/health | python3 -m json.tool 2>/dev/null || echo "$(RED)Health endpoint unreachable$(NC)"

remote-status: ## Show remote container status
	$(SSH_CMD) "cd $(DEPLOY_DIR) && $(DC_PROD) ps"

remote-shell: ## SSH into VPS project directory
	ssh -t -p $(SSH_PORT) $(SSH_USER)@$(SSH_HOST) "cd $(DEPLOY_DIR) && bash"

remote-db: ## Open psql on remote
	ssh -t -p $(SSH_PORT) $(SSH_USER)@$(SSH_HOST) "cd $(DEPLOY_DIR) && $(DC_PROD) exec db psql -U app -d app"

backup: ## Run database backup on VPS
	$(SSH_CMD) "bash $(DEPLOY_DIR)/infra/backup.sh"

setup-vps: ## One-time VPS provisioning (Docker, firewall, systemd, cron)
	@test -n "$(SSH_HOST)" || (echo "$(RED)Set SSH_HOST in .deploy.env$(NC)" && exit 1)
	@echo "$(GREEN)Setting up VPS at $(SSH_HOST)...$(NC)"
	bash infra/setup-vps.sh

telegram-setup: ## Register Telegram webhook with your domain
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "$(RED)Set TELEGRAM_BOT_TOKEN in .env$(NC)" && exit 1)
	@test -n "$(DOMAIN)" || (echo "$(RED)Set DOMAIN in .deploy.env$(NC)" && exit 1)
	@curl -s "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook?url=https://$(DOMAIN)/webhooks/telegram&secret_token=$(TELEGRAM_WEBHOOK_SECRET)" | python3 -m json.tool

telegram-test: ## Send test message to verify Telegram config
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "$(RED)Set TELEGRAM_BOT_TOKEN in .env$(NC)" && exit 1)
	@test -n "$(TELEGRAM_CHAT_ID)" || (echo "$(RED)Set TELEGRAM_CHAT_ID in .env$(NC)" && exit 1)
	@curl -s "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/sendMessage?chat_id=$(TELEGRAM_CHAT_ID)&text=Market+Discovery+test+message+OK" | python3 -m json.tool

caddy-hash: ## Generate bcrypt hash for Caddy basic auth (usage: make caddy-hash PASSWORD=yourpassword)
	@test -n "$(PASSWORD)" || (echo "$(RED)Usage: make caddy-hash PASSWORD=yourpassword$(NC)" && exit 1)
	@docker run --rm caddy:2 caddy hash-password --plaintext '$(PASSWORD)'
