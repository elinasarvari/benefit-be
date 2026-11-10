from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    dev = "dev"
    prod = "prod"
    test = "test"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    json = "json"
    console = "console"


class LlmProvider(StrEnum):
    anthropic = "anthropic"
    bedrock = "bedrock"


class LlmMode(StrEnum):
    live = "live"
    recorded = "recorded"
    fake = "fake"


class Embedder(StrEnum):
    qwen = "qwen"
    fake = "fake"


class Reranker(StrEnum):
    bge = "bge"
    fake = "fake"


MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Every setting the benefit-be processes read. Values come only from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
        env_ignore_empty=True,
        case_sensitive=True,
    )

    # Runtime and stores
    APP_ENV: AppEnv = AppEnv.dev
    DEMO_PROFILE: bool = True
    LOG_LEVEL: LogLevel = LogLevel.INFO
    LOG_FORMAT: LogFormat = LogFormat.json
    API_PORT: int = Field(default=8000, ge=1, le=65535)
    WORKER_HEALTH_PORT: int = Field(default=8001, ge=1, le=65535)
    WEB_ORIGIN: str = "http://localhost:5173"
    BENEFIT_FE_DIR: str = "../benefit-fe"
    MONGO_URL_APP: SecretStr
    MONGO_URL_OWNER: SecretStr
    MONGO_DB: str = "benefit"
    MONGO_AUDIT_DB: str = "benefit_audit"
    MONGO_DATA_DIR: str = ".data/mongo"
    DUCKDB_PATH: str = ".data/reporting.duckdb"
    TEMPORAL_ADDRESS: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"
    TEMPORAL_TASK_QUEUE: str = "benefit-case"
    HTTP_TIMEOUT_SECONDS: float = Field(default=10, gt=0)
    HTTP_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=5)

    # Identity and sessions
    IDENTITY_PROVIDER: Literal["local"] = "local"
    SESSION_SECRET: SecretStr
    SESSION_TTL_HOURS: int = Field(default=8, ge=1, le=24)
    SYSTEM_CREDENTIAL_SECRET: SecretStr
    DEMO_PASSWORD: SecretStr = SecretStr("")
    SEED_ANCHOR_DATE: date = date(2026, 11, 10)

    # AI provider and modes
    LLM_PROVIDER: LlmProvider = LlmProvider.anthropic
    LLM_MODE: LlmMode = LlmMode.fake
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_MODEL_ID: str = "claude-opus-5-5"
    AWS_REGION: str = ""
    AWS_PROFILE: str = ""
    BEDROCK_MODEL_ID: str = ""
    LLM_TIMEOUT_SECONDS: float = Field(default=60, gt=0)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=3)
    LLM_MAX_TOKENS: int = Field(default=4096, gt=0)
    LLM_RECORDINGS_DIR: str = "tests/fixtures/llm"
    LLM_PRICE_INPUT_PER_MTOK: Decimal | None = None
    LLM_PRICE_OUTPUT_PER_MTOK: Decimal | None = None
    MCP_TOOL_TIMEOUT_SECONDS: float = Field(default=15, gt=0)

    # Retrieval models
    EMBEDDER: Embedder | None = None
    RERANKER: Reranker | None = None

    # Tracing
    TRACING_ENABLED: bool | None = None
    TRACING_OTLP_ENDPOINT: str = "http://localhost:6006/v1/traces"
    TRACING_PROJECT: str = "benefit"

    @model_validator(mode="before")
    @classmethod
    def _environment_defaults(cls, data: dict[str, Any]) -> dict[str, Any]:
        is_test = str(data.get("APP_ENV", AppEnv.dev)) == AppEnv.test
        data.setdefault("EMBEDDER", Embedder.fake if is_test else Embedder.qwen)
        data.setdefault("RERANKER", Reranker.fake if is_test else Reranker.bge)
        data.setdefault("TRACING_ENABLED", not is_test)
        return data

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Settings:
        for name in ("SESSION_SECRET", "SYSTEM_CREDENTIAL_SECRET"):
            if len(getattr(self, name).get_secret_value().encode()) < MIN_SECRET_LENGTH:
                raise ValueError(f"{name}: must be at least {MIN_SECRET_LENGTH} bytes")
        if (
            self.SESSION_SECRET.get_secret_value()
            == self.SYSTEM_CREDENTIAL_SECRET.get_secret_value()
        ):
            raise ValueError("SYSTEM_CREDENTIAL_SECRET: must differ from SESSION_SECRET")
        demo = self.DEMO_PASSWORD.get_secret_value()
        if demo and len(demo) < 12:
            raise ValueError("DEMO_PASSWORD: must be at least 12 characters when set")
        if self.APP_ENV is AppEnv.test:
            if self.LLM_MODE is not LlmMode.fake:
                raise ValueError("LLM_MODE: must be fake when APP_ENV=test")
            if self.EMBEDDER is not Embedder.fake or self.RERANKER is not Reranker.fake:
                raise ValueError("EMBEDDER: embedder and reranker must be fake when APP_ENV=test")
            if self.TRACING_ENABLED:
                raise ValueError("TRACING_ENABLED: must be false when APP_ENV=test")
        if self.LLM_MODE is LlmMode.live:
            if self.LLM_PROVIDER is LlmProvider.anthropic:
                if not self.ANTHROPIC_API_KEY.get_secret_value():
                    raise ValueError("ANTHROPIC_API_KEY: required when LLM_MODE=live and anthropic")
            elif not self.AWS_REGION or not self.BEDROCK_MODEL_ID:
                name = "AWS_REGION" if not self.AWS_REGION else "BEDROCK_MODEL_ID"
                raise ValueError(f"{name}: required when LLM_MODE=live and bedrock")
        return self


def _describe(error: dict[str, Any]) -> str:
    """One line naming the setting and the reason. Never includes the offending value."""
    location = ".".join(str(part) for part in error["loc"])
    kind = error["type"]
    if kind == "missing":
        return f"setting {location}: missing; required"
    if kind in ("enum", "literal_error"):
        expected = str(error.get("ctx", {}).get("expected", "")).replace("'", "")
        allowed = expected.replace(" or ", ", ")
        return f"setting {location}: invalid value; allowed: {allowed}"
    if kind == "value_error":
        message = str(error["msg"]).removeprefix("Value error, ")
        return f"setting {message}"
    return f"setting {location}: invalid value ({error['msg']})"


def load_settings(**overrides: Any) -> Settings:
    """Build settings, or exit with code 2 after naming every missing or invalid setting."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            print(_describe(dict(error)), file=sys.stderr)
        raise SystemExit(2) from None
