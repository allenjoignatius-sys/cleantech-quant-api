"""Application configuration using Pydantic settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import secrets


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Cleantech Quant API"
    DEBUG: bool = False
    SECRET_KEY: str = secrets.token_urlsafe(32)
    API_KEY_PREFIX: str = "ctq_"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/cleantech_api"
    REDIS_URL: str = "redis://localhost:6379"

    # JWT
    JWT_SECRET: str = secrets.token_urlsafe(32)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS & Hosts
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "https://app.cleantechquant.io",
        "https://cleantechquant.io",
    ]
    ALLOWED_HOSTS: List[str] = ["*"]

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Plans
    PLAN_PRICES: dict = {
        "free": 0,
        "analyst": 49900,      # $499/month in cents
        "enterprise": 150000,  # $1500/month in cents
    }
    PLAN_RATE_LIMITS: dict = {
        "free": {"requests_per_day": 100, "requests_per_minute": 10},
        "analyst": {"requests_per_day": 10000, "requests_per_minute": 100},
        "enterprise": {"requests_per_day": -1, "requests_per_minute": -1},
    }

    # Scraper
    SCRAPER_INTERVAL_HOURS: int = 6
    USER_AGENT: str = "CleanTechQuantResearch/1.0 (research@cleantechquant.io)"

    # Email (SendGrid)
    SENDGRID_API_KEY: str = ""
    FROM_EMAIL: str = "no-reply@cleantechquant.io"

    # Alerts
    ALERT_CHECK_INTERVAL_MINUTES: int = 15

    # S3 (for report storage)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = "cleantech-quant-reports"
    AWS_REGION: str = "us-east-1"

    # Pydantic v2 Config (replaces class Config)
    model_config = SettingsConfigDict(
        env_file=".env", 
        case_sensitive=True, 
        extra="ignore"
    )


settings = Settings()