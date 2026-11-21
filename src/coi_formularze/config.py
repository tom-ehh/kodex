"""Runtime configuration, read from environment variables prefixed `COI_` (or a .env file)."""

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # hide_input_in_errors: a config error must not print secrets (it dumps .env values otherwise).
    model_config = SettingsConfigDict(
        env_prefix="COI_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    max_file_size_bytes: int = Field(20 * 1024 * 1024, gt=0, description="Per-file upload limit.")
    max_batch_files: int = Field(20, gt=0, description="Max files in one batch request.")
    max_jobs: int = Field(100, gt=0, description="In-memory job store capacity (oldest evicted).")

    jwt_enabled: bool = False
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_audience: str | None = None
    jwt_issuer: str | None = None

    @model_validator(mode="after")
    def _jwt_secret_required(self) -> Settings:
        if self.jwt_enabled and not self.jwt_secret:
            raise ValueError("COI_JWT_SECRET is required when COI_JWT_ENABLED=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
