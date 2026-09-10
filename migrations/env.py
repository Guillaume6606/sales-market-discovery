from __future__ import annotations

import importlib
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from libs.common.models import Base

config = context.config
fileConfig(config.config_file_name)


def _register_model_modules() -> None:
    """Load standalone model modules without importing the FastAPI application."""
    for module_name in ("libs.common.valuation_models", "libs.common.trade_models"):
        importlib.import_module(module_name)


_register_model_modules()
target_metadata = Base.metadata


def _database_url() -> str:
    """DATABASE_URL if set, else built from POSTGRES_* env vars, else alembic.ini."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("POSTGRES_HOST")
    db = os.environ.get("POSTGRES_DB")
    if user and password and host and db:
        port = os.environ.get("POSTGRES_PORT", "5432")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    url = _database_url()
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    cfg = config.get_section(config.config_ini_section)
    cfg["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
