from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_TITLE: str = "AI Calendar Scheduling"
    API_VERSION: str = "1.0.0"

    POLLINATIONS_API_KEY: str = Field(default="")
    POLLINATIONS_MODEL: str = "openai"
    POLLINATIONS_BASE_URL: str = "https://gen.pollinations.ai/v1"

    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    GOOGLE_REDIRECT_URI: str = Field(default="")
    GOOGLE_REFRESH_TOKEN_FILE: str | None = None
    GOOGLE_CALENDAR_ID: str = "primary"
    GOOGLE_CALENDAR_ADMIN_EMAILS: str = ""
    FRONTEND_PUBLIC_URL: str = "http://localhost:3000"
    GOOGLE_APPOINTMENT_SCHEDULE_URL: str = ""
    CORS_ALLOW_ORIGINS: str = Field(
        default="",
        description=(
            "Comma-separated extra browser origins for CORS. "
            "Localhost :3000 and :3001 and FRONTEND_PUBLIC_URL are always included."
        ),
    )

    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    APP_LOGIN_USERNAME: str | None = None
    APP_LOGIN_PASSWORD: str | None = None
    SESSION_EXPIRE_DAYS: int = 7

    LOG_LEVEL: str = "INFO"
    
    TIMEZONE: str = "UTC"
    AVAILABILITY_WINDOW_DAYS: int = 3
    SLOT_DURATION_MINUTES: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

@lru_cache()
def get_settings():
    return Settings()


def cors_allow_origins(settings: Settings) -> list[str]:
    origins: set[str] = {
        "http://localhost:3000",
        "http://localhost:3001",
    }
    pub = (settings.FRONTEND_PUBLIC_URL or "").strip().rstrip("/")
    if pub:
        origins.add(pub)
    for part in (settings.CORS_ALLOW_ORIGINS or "").split(","):
        p = part.strip().rstrip("/")
        if p:
            origins.add(p)
    return sorted(origins)