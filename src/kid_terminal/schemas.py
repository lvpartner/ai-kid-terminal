from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .qwen_capabilities import QWEN35_BUILTIN_VOICES


class EnrollmentRequest(BaseModel):
    enrollment_token: str = Field(min_length=20, max_length=200)
    device_name: str = Field(min_length=1, max_length=100)
    app_version: str = Field(default="unknown", max_length=50)
    os_version: str = Field(default="unknown", max_length=100)
    manufacturer: str = Field(default="unknown", max_length=50)
    device_model: str = Field(default="unknown", max_length=100)
    security_patch: str = Field(default="unknown", max_length=20)


class EnrollmentCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    expires_minutes: int = Field(default=30, ge=1, le=1440)


class HeartbeatRequest(BaseModel):
    app_version: str = Field(max_length=50)
    os_version: str = Field(max_length=100)
    manufacturer: str = Field(default="unknown", max_length=50)
    device_model: str = Field(default="unknown", max_length=100)
    security_patch: str = Field(default="unknown", max_length=20)
    network_type: Literal["wifi", "cellular", "ethernet", "offline", "unknown"]
    battery_percent: int = Field(ge=0, le=100)
    charging: bool
    ws_state: str = Field(max_length=30)


class TelemetryRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=36)
    event_type: Literal[
        "heartbeat",
        "microphone",
        "audio_playback",
        "ai_connection",
        "latency",
        "disconnect",
        "reconnect",
        "crash",
        "diagnostic",
    ]
    severity: Literal["info", "warning", "error"] = "info"
    first_packet_ms: int | None = Field(default=None, ge=0, le=600_000)
    first_audio_ms: int | None = Field(default=None, ge=0, le=600_000)
    turn_total_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    reconnect_count: int | None = Field(default=None, ge=0, le=100_000)
    crash_stack: str | None = Field(default=None, max_length=16_000)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class ConfigUpdate(BaseModel):
    provider: Literal["mock", "hybrid", "qwen_realtime"] = "mock"
    model: str = Field(default="deepseek-v4-flash", max_length=100)
    voice: str = Field(default="longanyang", max_length=50)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    answer_length: Literal["short", "medium"] = "medium"
    grade_min: int = Field(default=1, ge=1, le=6)
    grade_max: int = Field(default=6, ge=1, le=6)
    web_search: bool = True
    heartbeat_seconds: int = Field(default=15, ge=5, le=120)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    features: dict[str, bool] = Field(default_factory=dict)
    min_app_version: str = Field(default="0.1.0", max_length=50)
    force_upgrade: bool = False

    @model_validator(mode="after")
    def validate_model_voice_pair(self):
        if self.model.startswith("qwen3.5-") and self.voice not in QWEN35_BUILTIN_VOICES:
            raise ValueError("voice is not supported by the configured Qwen3.5 Omni model")
        return self


class ReleaseMetadata(BaseModel):
    version_code: int = Field(gt=0)
    version_name: str = Field(min_length=1, max_length=50)
    min_android: int = Field(default=26, ge=21, le=100)
    forced: bool = False
    rollout_percent: float = Field(default=100, ge=0, le=100)
    release_notes: str = Field(default="", max_length=4000)
    rollback_version_code: int | None = Field(default=None, gt=0)
