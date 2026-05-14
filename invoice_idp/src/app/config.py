"""Application settings — env-driven via pydantic-settings.

Reads .env in the project root, with environment variables taking
precedence over file values. All settings are required-on-use, not
required-on-import, so dev workflows (running pytest, alembic) don't
need every var populated.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://invoice_idp:invoice_idp@localhost:5432/invoice_idp"
    )

    # Web / auth secrets — generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`
    session_secret: str = Field(default="")
    csrf_secret: str = Field(default="")

    # Extraction provider creds (Phase 1: Anthropic direct; Phase 3+: AWS Bedrock)
    anthropic_api_key: str = Field(default="")
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_region: str = Field(default="eu-central-1")

    # Email (Phase 2 — Postmark or stub)
    postmark_api_token: str = Field(default="")
    postmark_from_email: str = Field(default="")

    # Object storage (S3-compatible). Defaults target local MinIO from
    # docker-compose; production switches to Hetzner Object Storage.
    s3_endpoint_url: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin")
    s3_bucket: str = Field(default="invoice-idp")
    s3_region: str = Field(default="us-east-1")  # MinIO ignores; Hetzner uses eu-central

    # Job queue (arq + Redis)
    redis_url: str = Field(default="redis://localhost:6379")

    # Stripe (Phase 6 — billing). Empty = dev console mode.
    stripe_secret_key: str = Field(default="")
    stripe_publishable_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    # Cost of one successful extraction, in PLN grosze (50 = 0,50 PLN).
    invoice_price_grosze: int = Field(default=50)

    # Twilio Verify (Phase 6 — phone gate before upload). Empty = dev console.
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_verify_service_sid: str = Field(default="")

    # Ops alerting
    bigga: str = Field(default="")  # Discord webhook

    # App
    debug: bool = Field(default=False)
    app_base_url: str = Field(default="http://localhost:8000")
    max_upload_mb: int = Field(default=20)  # Per §6: 20 MB PDF cap

    # When True the session cookie is marked Secure (sent only over HTTPS).
    # Dev default = False so http://localhost works; prod must override to True.
    session_cookie_secure: bool = Field(default=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
