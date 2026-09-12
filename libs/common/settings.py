from decimal import Decimal

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
    ebay_cert_id: str | None = None
    # eBay marketplace account deletion compliance (32-80 char token you invent,
    # and the exact public URL registered in the eBay developer portal)
    ebay_verification_token: str | None = None
    ebay_deletion_endpoint_url: str | None = None

    # Scraping proxy (rotating residential, format: http://user:pass@host:port)
    scraping_proxy_url: str | None = None

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
    working_capital_eur: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    alert_freshness_minutes: int = Field(default=60, ge=1, le=1440)
    alert_delivery_batch_size: int = Field(default=20, ge=1, le=100)
    alert_max_attempts: int = Field(default=5, ge=1, le=10)

    # Connector audit
    audit_enabled: bool = False
    audit_sample_size: int = 3
    audit_accuracy_green: float = 0.90
    audit_accuracy_yellow: float = 0.80
    audit_daily_token_budget: int = 100000

    # Enrichment pipeline
    vision_shadow_mode: bool = True
    vision_batch_size: int = Field(default=50, ge=1, le=200)
    enrichment_enabled: bool = False
    enrichment_batch_size: int = 50
    enrichment_re_enrichment_batch_size: int = 20
    enrichment_re_enrichment_age_days: int = 7
    enrichment_llm_model: str = "gemini-2.5-flash-lite"
    enrichment_max_tokens_per_day: int = 500_000
    enrichment_budget_cap_eur_per_month: float = 120.0

    # Listing vision (shadow extraction; currencies are budgeted independently).
    vision_enabled: bool = False
    vision_provider: str = "gemini"
    vision_model: str = "gemini-3.1-flash-lite"
    vision_local_model_revision: str | None = None
    vision_local_base_url: str = "http://localhost:8080/v1"
    scaleway_api_key: str | None = None
    vision_monthly_budget_usd: Decimal = Field(default=Decimal("10"), ge=0)
    vision_monthly_budget_eur: Decimal = Field(default=Decimal("10"), ge=0)
    vision_max_output_tokens: int = Field(default=1200, ge=128, le=4096)
    vision_image_hosts: str = (
        "ebayimg.com,lbcpics.com,leboncoin.fr,vinted.net,vinted.com,cashconverters.fr"
    )

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
