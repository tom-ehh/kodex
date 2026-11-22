"""Runtime configuration, read from environment variables prefixed `COI_` (or a .env file)."""

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # hide_input_in_errors: a config error must not print secrets (it dumps .env values otherwise).
    model_config = SettingsConfigDict(
        env_prefix="COI_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    max_file_size_bytes: int = Field(20 * 1024 * 1024, gt=0, description="Per-file upload limit.")
    max_batch_files: int = Field(20, gt=0, description="Max files in one batch request.")
    max_jobs: int = Field(100, gt=0, description="In-memory job store capacity (oldest evicted).")

    # OCR: an open-weights vision model on EdenAI v3 (no API-only models at runtime).
    edenai_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("COI_EDENAI_API_KEY", "EDENAI_API_KEY")
    )
    ocr_model: str = "deepinfra/allenai/olmOCR-7B-0725-FP8"
    ocr_timeout_s: float = Field(120.0, gt=0)

    # Complexity router (basal-serve running Remek/basal-omni-pico); heuristic fallback.
    router_enabled: bool = False
    router_url: str = "http://127.0.0.1:8001"
    router_threshold: float = Field(0.7, ge=0, le=1)
    router_timeout_s: float = Field(5.0, gt=0)
    # ~4k tokens of Polish text; the model's prompt limit is ~6k tokens.
    router_max_state_chars: int = Field(12000, gt=200)
    # Extractor model per tier: open-weights, EdenAI ids pinned to a provider with
    # JSON-schema output and reasoning off (picked 2026-11-22, docs/notes/llm-drafts.md).
    tier_simple_model: str = "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731"
    tier_medium_model: str = "deepinfra/Qwen/Qwen3.6-35B-A3B"
    tier_hard_model: str = "qwen/deepseek-v4-pro-0813"
    llm_timeout_s: float = Field(120.0, gt=0, description="Per LLM call.")

    # Morpheus skill store (SQLite on the morpheus-data volume); never opened when disabled.
    morpheus_enabled: bool = False
    morpheus_db: str = "/data/morpheus.sqlite"
    skills_snapshot: int | None = Field(None, ge=1, description="Pin a skillset version.")
    episodic_ttl_days: int = Field(14, gt=0)
    episodic_max: int = Field(20, gt=0)
    general_max: int = Field(50, gt=0)
    skills_max_per_section: int = Field(5, gt=0)
    morpheus_doc_ttl_s: float = Field(86400.0, gt=0, description="In-memory Document lifetime.")
    # Morpheus wake (P2): flag gate (design.md decision 7, revised), writer, light gate.
    morpheus_model: str | None = Field(None, description="Skill writer; default tier_hard_model.")
    morpheus_replay_model: str | None = Field(
        None, description="Light-gate section replay; default tier_medium_model.")
    flag_hist_min: int = Field(2, ge=1, description="Same family+validator episodes needed.")
    flag_pattern_min: float = Field(0.5, ge=0, le=1, description="basal P(pattern) to confirm.")
    flag_consistent_min: float = Field(0.5, ge=0, le=1, description="basal consistency veto.")
    morpheus_basal_retries: int = Field(3, ge=0, description="Ticks to wait for basal, then rules only.")
    morpheus_gate_md_dir: str | None = Field(
        None, description="Gate forms' markdown for the wake smoke test; unset = skipped.")
    morpheus_wake_interval_s: float = Field(30.0, gt=0, description="Background wake_tick period.")

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
