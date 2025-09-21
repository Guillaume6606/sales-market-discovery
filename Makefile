SHELL := /bin/bash

.PHONY: fmt lint test up down logs sh

fmt:
	ruff check --select I --fix .
	black .

lint:
	ruff check .

test:
	pytest -q

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

sh:
	docker compose exec backend bash
