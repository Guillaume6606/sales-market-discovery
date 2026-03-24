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

    # Enrichment pipeline
    enrichment_enabled: bool = True
    enrichment_batch_size: int = 50
    enrichment_re_enrichment_batch_size: int = 20
    enrichment_re_enrichment_age_days: int = 7
    enrichment_llm_model: str = "gemini-2.0-flash"
    enrichment_max_tokens_per_day: int = 500_000
    enrichment_budget_cap_eur_per_month: float = 120.0

    # Detail fetch
    detail_fetch_enabled: bool = True
    detail_fetch_pmn_threshold: float = 1.1
    detail_fetch_rate_limit_ebay: float = 0.5
    detail_fetch_rate_limit_lbc: float = 1.0
    detail_fetch_rate_limit_vinted: float = 2.0

    # Scoring
    scoring_confidence_threshold: float = 80.0
    scoring_sell_shipping_electronics: float = 8.0
    scoring_sell_shipping_watches: float = 6.0
    scoring_sell_shipping_clothing: float = 5.0
    scoring_sell_shipping_default: float = 7.0
    scoring_vinted_buyer_fee_pct: float = 0.05

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
DATABASE_URL = f"postgresql+psycopg2://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
