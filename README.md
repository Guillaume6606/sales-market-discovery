# Market Discovery & PMN — Skeleton

Mono‑repo skeleton for a Flip/Resell Market Discovery app with **FastAPI**, **Streamlit**, **PostgreSQL**, **Redis**, **Celery (or Arq)**, and **Alembic**.

## Components
- `backend/` — FastAPI app exposing product discovery and alert rules APIs.
- `ingestion/` — Connectors (eBay first), normalization, PMN engine, scheduled via workers.
- `ui/` — Streamlit MVP dashboard (Discovery board, Product page, Rules).
- `infra/` — Docker & compose for local dev, Alembic migrations, pre-commit, Makefile.

### Quick start (local)
```bash
# 1) Prepare env
cp .env.example .env

# 2) Start stack
docker compose up --build -d

# 3) Initialize DB
docker compose exec backend alembic upgrade head

# 4) Open apps
# FastAPI docs: http://localhost:8000/docs
# Streamlit:    http://localhost:8501
```

### Dev commands
```bash
make fmt        # format with ruff/black
make lint       # lint
make test       # run pytest
make up         # docker compose up -d
make down       # docker compose down -v
```

See `/docs/ARCHITECTURE.md` for design & roadmap.
