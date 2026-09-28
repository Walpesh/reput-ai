from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Core Application
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "insecure-secret-key-please-change-in-production"
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "ReputationAI"

    # PostgreSQL Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/reput_ai_db"

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_URL: Optional[str] = None

    # LLM (OpenRouter)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    PRIMARY_LLM_MODEL: str = "deepseek/deepseek-chat"
    FALLBACK_LLM_MODEL: str = "google/gemini-flash-1.5"

    # Billing (YooKassa / T-Bank)
    YOOKASSA_SHOP_ID: Optional[str] = None
    YOOKASSA_SECRET_KEY: Optional[str] = None
    YOOKASSA_WEBHOOK_SECRET: Optional[str] = None
    YOOKASSA_API_URL: str = "https://api.yookassa.ru/v3"
    TBANK_PASSWORD: Optional[str] = None
    TRIAL_PERIOD_DAYS: int = 14
    BILLING_PERIOD_DAYS: int = 30
    BILLING_GRACE_PERIOD_DAYS: int = 3
    BILLING_PRICE_RUB: int = 2990
    BILLING_CURRENCY: str = "RUB"
    BILLING_RENEWAL_LEAD_HOURS: int = 24
    BILLING_RETURN_URL: str = "https://reputation-ai.local/billing/success"

    # Public funnel (QR codes / short links)
    PUBLIC_BASE_URL: str = "https://reputation-ai.local"
    FEEDBACK_FORM_PATH: str = "/feedback/form"

    # Polling & Scraper (supported polling window is 15-30 minutes)
    SCRAPER_POLL_INTERVAL_MINUTES: int = 15
    SCRAPER_MIN_POLL_INTERVAL_MINUTES: int = 15
    SCRAPER_MAX_POLL_INTERVAL_MINUTES: int = 30
    SCRAPER_TIMEOUT_SECONDS: float = 20.0
    SCRAPER_MAX_ATTEMPTS: int = 3
    SCRAPER_MAX_REVIEWS_PER_BRANCH: int = 50
    SCRAPER_USER_AGENT: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36 ReputationAI/0.1"
    )

    # AI processing of freshly collected reviews
    AI_PROCESSING_INTERVAL_MINUTES: int = 5
    AI_BATCH_SIZE: int = 10

    # Web Dashboard (Streamlit) -> backend API
    DASHBOARD_API_BASE_URL: str = "http://127.0.0.1:8000"
    DASHBOARD_API_TIMEOUT_SECONDS: float = 15.0


settings = Settings()


def resolve_scraper_poll_interval_minutes(value: int | None = None) -> int:
    """Clamp the configured polling interval into the required 15-30 minute window."""
    candidate = settings.SCRAPER_POLL_INTERVAL_MINUTES if value is None else value
    return max(
        settings.SCRAPER_MIN_POLL_INTERVAL_MINUTES,
        min(settings.SCRAPER_MAX_POLL_INTERVAL_MINUTES, candidate),
    )
