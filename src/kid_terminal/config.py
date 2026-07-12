from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./data/kid-terminal.db"
    admin_api_key: str = ""
    token_pepper: str = ""
    ai_provider: Literal["mock", "hybrid", "qwen_realtime"] = "mock"
    dashscope_api_key: str = ""
    qwen_workspace_id: str = ""
    qwen_base_url: str = ""
    qwen_region: Literal["cn-beijing", "ap-southeast-1"] = "cn-beijing"
    qwen_model: str = "qwen3.5-omni-plus-realtime"
    grounded_model: str = "qwen3.5-plus"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    cosyvoice_model: str = "cosyvoice-v3-flash"
    cosyvoice_voice: str = "longanyang"
    strict_grounding: bool = True
    qwen_event_timeout_seconds: int = Field(default=30, ge=5, le=120)
    knowledge_db_path: Path = Path("knowledge/curriculum.db")
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    release_dir: Path = Path("releases")
    access_token_days: int = Field(default=365, ge=1, le=3650)
    enrollment_minutes: int = Field(default=30, ge=1, le=1440)
    ws_idle_seconds: int = Field(default=45, ge=10, le=300)
    ws_max_message_bytes: int = Field(default=1_048_576, ge=4096, le=8_388_608)
    ws_events_per_second: int = Field(default=50, ge=5, le=200)
    enable_long_term_memory: bool = False
    memory_retention_days: int = Field(default=1, ge=1, le=30)
    conversation_retention_hours: int = Field(default=24, ge=1, le=168)
    context_turns: int = Field(default=8, ge=5, le=10)
    telemetry_retention_days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        if self.environment == "production":
            if len(self.admin_api_key) < 32 or len(self.token_pepper) < 32:
                raise ValueError(
                    "production requires independent secrets of at least 32 characters"
                )
            if self.ai_provider in {"hybrid", "qwen_realtime"} and not self.dashscope_api_key:
                raise ValueError("Qwen ASR/TTS requires an API key")
            if self.ai_provider == "hybrid" and not self.deepseek_api_key:
                raise ValueError("Hybrid provider requires a DeepSeek API key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
