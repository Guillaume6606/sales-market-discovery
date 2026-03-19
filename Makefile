SHELL := /bin/bash
DC := docker-compose

.PHONY: fmt lint test up down logs sh audit

fmt:
	uv run ruff check --fix . && uv run ruff format .

lint:
	uv run ruff check .

test:
	uv run pytest -q

up:
	$(DC) up -d --build

down:
	$(DC) down -v

logs:
	$(DC) logs -f --tail=100

sh:
	$(DC) exec ingestion bash

audit:
	$(DC) run --rm -T -e UV_CACHE_DIR=/tmp/uv-cache ingestion \
		xvfb-run -a uv run python -m ingestion.audit_cli \
		--connectors vinted \
		--products-per-connector 2 \
		--listings-per-product 5 \
		--html-only
