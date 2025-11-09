from pydantic_settings import BaseSettings
from pydantic import Field

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
DATABASE_URL = f"postgresql+psycopg2://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
SUPPORTED_PROVIDERS = ["ebay", "leboncoin", "vinted", "fnac", "cdiscount", "backmarket", "rakuten"]