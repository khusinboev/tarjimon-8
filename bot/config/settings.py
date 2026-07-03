from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional, List


class Settings(BaseSettings):
    """Application settings"""

    # Bot Configuration
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    ADMIN_USER_ID: int = Field(..., description="Main Admin User ID")
    ADMIN_USER_IDS_RAW: Optional[str] = Field(default=None, alias="ADMIN_USER_IDS")

    @property
    def ADMIN_USER_IDS(self) -> List[int]:
        # Supports comma-separated ADMIN_USER_IDS, falls back to ADMIN_USER_ID.
        if self.ADMIN_USER_IDS_RAW:
            parsed: List[int] = []
            for part in self.ADMIN_USER_IDS_RAW.split(","):
                value = part.strip()
                if not value:
                    continue
                parsed.append(int(value))
            if parsed:
                return parsed
        return [self.ADMIN_USER_ID]

    # Database
    POSTGRES_USER: str = Field(default="botuser")
    POSTGRES_PASSWORD: str = Field(default="botpassword")
    POSTGRES_DB: str = Field(default="tarjimon8")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    DATABASE_URL_RAW: Optional[str] = Field(default=None, alias="DATABASE_URL")

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_RAW:
            return self.DATABASE_URL_RAW
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Redis
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)

    # Environment
    ENVIRONMENT: str = Field(default="production")
    AUTO_CREATE_SCHEMA: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
