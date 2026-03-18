from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = Field(default="local")
    secret_key: str = Field(default="dev")
    use_playwright: bool = Field(default=False)

    # Logging
    log_level: str = Field(default="INFO")

    # DB
    postgres_user: str = "app"
    postgres_password: str = "app"
    postgres_db: str = "app"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # APIs
    ebay_app_id: str | None = None

    # LLM Configuration (Gemini via Vertex AI)
    gemini_api_key: str | None = None
    gemini_model: str = Field(default="gemini-2.5-flash")
    llm_enabled: bool = Field(default=False)
    gcp_project_id: str | None = None
    gcp_location: str = Field(default="europe-west1")

    # Telegram Configuration
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_webhook_secret: str | None = None

    # Screenshot Configuration
    screenshot_storage_path: str = Field(default="/data/screenshots")
    screenshot_enabled: bool = Field(default=False)

    # Observability & Staleness
    stale_product_hours: int = Field(default=24)
    stale_listing_days: int = Field(default=7)
    connector_failure_threshold: int = Field(default=3)
    min_pmn_confidence: float = Field(default=0.3)

    # Connector audit
    audit_enabled: bool = False
    audit_sample_size: int = 3
    audit_accuracy_green: float = 0.90
    audit_accuracy_yellow: float = 0.80
    audit_daily_token_budget: int = 100000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
DATABASE_URL = f"postgresql+psycopg2://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
