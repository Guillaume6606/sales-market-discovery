# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Market discovery and arbitrage detection platform for resale marketplaces (eBay, LeBonCoin, Vinted). Monorepo with four components: FastAPI backend, ARQ ingestion workers, Streamlit dashboard, and shared libraries.

## Commands

```bash
make up          # docker-compose up -d --build (all services)
make down        # docker-compose down -v
make logs        # docker-compose logs -f --tail=100
make sh          # shell into ingestion container
make fmt         # uv run ruff check --fix . && uv run ruff format .
make lint        # uv run ruff check .
make test        # uv run pytest -q
make audit       # run Vinted connector audit in Docker (html-only)
```

Direct dev commands (outside Docker):
```bash
uv pip install -e ".[dev]"                  # install dependencies
uv run uvicorn backend.main:app --reload    # run backend on :8000
uv run streamlit run ui/app.py              # run UI on :8501
uv run arq ingestion.worker.WorkerSettings  # run ingestion worker
uv run alembic upgrade head                 # apply migrations
uv run alembic revision --autogenerate -m "msg"  # create migration
uv run pytest tests/unit/ -v                # run unit tests
```

## Architecture

**Services** (docker-compose.yml):
- `db` — PostgreSQL 16 (:5432)
- `redis` — Redis 7 (:6379), message broker for ARQ job queue
- `backend` — FastAPI REST API (:8000), depends on db + redis
- `ingestion` — ARQ worker with Playwright browser (runs as `pwuser`, needs 1GB shm + xvfb)
- `ui` — Streamlit dashboard (:8501), calls backend API

**Key data flow**: Ingestion workers scrape marketplaces → normalize to `Listing` model → store as `listing_observation` → compute PMN (Price of Market Normal) + liquidity + trend scores → store in `product_daily_metrics` and `market_price_normal` → backend serves discovery/analytics → UI displays.

**Shared library** (`libs/common/`): ORM models, DB connection, settings (Pydantic), scraping session with anti-bot bypass (Playwright stealth patches, CloudScraper, UA rotation).

**Marketplace connectors** (`ingestion/connectors/`): Each connector returns standardized `Listing` objects. eBay uses Finding API; LeBonCoin has both scraping and JSON API approaches; Vinted uses scraping.

**Scheduled ingestion** (ARQ cron in `ingestion/worker.py`): eBay 2:00 AM, LeBonCoin 3:00 AM, Vinted 4:00 AM.

## Code Style

- Python 3.11, strict typing enforced (`mypy --disallow-untyped-defs`)
- Ruff rules: E, F, I, B, UP, N, S, C90 — line length 100
- Black for formatting
- All functions must have type annotations

## Environment

Copy `.env.example` to `.env`. Required: Postgres credentials, Redis URL. Optional: `EBAY_APP_ID`, `KEEPA_API_KEY`, Telegram bot config, `USE_PLAYWRIGHT=true` for browser-based scraping.

## Database

PostgreSQL with SQLAlchemy 2.0 ORM. Models in `libs/common/models.py`. Migrations via Alembic (`migrations/` directory, config in `alembic.ini`). Key tables: `category`, `product_template`, `listing_observation`, `product_daily_metrics`, `market_price_normal`, `alert_rule`, `alert_event`, `ingestion_run`, `alert_feedback`.

## Roadmap & Objectives

Full roadmap, KPIs, and milestone definitions in `docs/superpowers/specs/2026-03-14-roadmap-objectives-kpis-design.md`.

**Vision:** Self-maintaining arbitrage detection system surfacing 1-2 high-confidence opportunities/day across liquid, high-value products (electronics, watches, etc.). Target: €1-2K/month within 6 months at <30 min/day operator time.

**Milestones:**
1. **Trust the System** (W1-4) — Observability, PMN validation, test coverage, feedback loop → **[Detailed tasks](docs/milestone-1-todo.md)**
2. **Fast & Precise** (W5-8) — Higher ingestion frequency, composite opportunity scoring, tiered alerting, LLM activation
3. **Get Smarter** (W9-14) — Product discovery engine, trend detection, advanced LLM, seller intelligence
4. **Scale Up** (W15-20) — Portfolio management, new marketplaces, auto-pricing, performance analytics

**Current focus:** Milestone 2. See `docs/milestone-1-todo.md` (completed) and future `docs/milestone-2-todo.md` for task breakdowns.

## Planning Rule

**Before planning or starting work on a new milestone/phase**, always read the current `docs/milestone-*-todo.md` files first to understand what's done, what's pending, and what was deferred. Never assume a milestone is complete without checking the todo against the actual codebase.
