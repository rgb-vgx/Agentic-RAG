"""Application settings loaded from conf.json and environment variables.

Precedence (highest first): constructor args, environment variables,
`.env`, `conf/conf.json`, field defaults. Secrets (API keys, JWT secret,
S3 credentials) belong in the environment, never in conf.json.
"""

import os
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DEFAULT_CONF_FILE = Path(__file__).resolve().parents[2] / "conf" / "conf.json"
CONF_FILE = Path(os.environ.get("APP_CONFIG_FILE") or _DEFAULT_CONF_FILE)

_LLM_PROVIDERS = {"openai", "anthropic"}


class LLMModelOption(BaseModel):
    """One model the client is allowed to select."""

    id: str
    label: str = ""
    provider: str = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        json_file=str(CONF_FILE),
        json_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep env overrides above conf.json; check secrets last."""
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        if CONF_FILE.exists():
            sources.append(JsonConfigSettingsSource(settings_cls))
        sources.append(file_secret_settings)
        return tuple(sources)

    # Application
    app_env: str = "development"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Data layer
    database_url: str = "postgresql+asyncpg://agentic:agentic@localhost:5432/agentic_rag"

    # Redis / RQ
    redis_url: str = "redis://localhost:6379/0"
    ingestion_queue: str = "ingestion"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "chunks"
    qdrant_api_key: str | None = None
    # Sparse vectors are still indexed, but the benchmark showed the hash
    # BM25 channel hurting dense-only retrieval on Vietnamese legal text
    # (7/20 vs 18/20 top-1), so it is off by default.
    retrieval_enable_sparse: bool = False
    # Qdrant HTTP timeout; large 4096-dim batches need more than the default.
    qdrant_timeout_seconds: int = 120

    # Object storage
    s3_endpoint_url: str | None = None
    s3_bucket: str = "agentic-rag"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # LLM
    llm_provider: str = "openai"
    openai_api_key: str = ""
    # Optional OpenAI-compatible endpoint (e.g. a LiteLLM proxy).
    openai_base_url: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    # Optional Anthropic-compatible endpoint (e.g. a LiteLLM proxy).
    anthropic_base_url: str | None = None
    # Bearer auth for proxies that expect Authorization: Bearer instead of
    # the SDK's default x-api-key header (e.g. OpenCode inference).
    anthropic_auth_token: str = ""
    # Extra request headers some proxies require, as a JSON object:
    # ANTHROPIC_EXTRA_HEADERS='{"x-opencode-session":"agentic-rag"}'
    anthropic_extra_headers: dict[str, str] = Field(default_factory=dict)
    # Optional extended-thinking fields forwarded to the provider.
    anthropic_thinking_type: str | None = None   # e.g. "adaptive"
    anthropic_output_effort: str | None = None   # e.g. "high"
    # Ollama/Qwen "thinking" models spend the token budget on reasoning and
    # return empty content unless this is "none".
    openai_reasoning_effort: str | None = None
    anthropic_chat_model: str = "claude-sonnet-4-20250514"
    # Allowlist exposed to the client. Empty -> only the configured
    # provider/model above is selectable.
    llm_models: list[LLMModelOption] = Field(default_factory=list)
    llm_default_model: str = ""

    # Embeddings
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    # Optional dedicated endpoint (e.g. a TEI container). Falls back to
    # openai_base_url when unset, so one Ollama/LiteLLM URL can serve both.
    embedding_base_url: str | None = None
    embedding_api_key: str = ""
    # Instruction prepended to QUERIES only (not documents). Qwen3-Embedding
    # gains ~.03 MRR with it on the Vietnamese legal benchmark; bge-m3 does
    # not need one, so this is empty by default.
    embedding_query_instruction: str = ""
    # Large embedding models (qwen3-embedding:8b) are much slower per request;
    # keep batches small and allow long-running batches before the SDK times out.
    embedding_batch_size: int = 16
    embedding_timeout_seconds: float = 900.0

    # Reranking
    rerank_provider: str = "service"
    rerank_model: str = "Qwen3-Reranker-4B"
    rerank_api_key: str = ""
    rerank_enabled: bool = False
    rerank_url: str | None = None
    rerank_path: str = "/rerank"
    rerank_timeout_seconds: float = 300.0
    rerank_candidates: int = 50
    rerank_top_n: int = 10
    # Instruction shown to Qwen3-Reranker (English, per the model card).
    rerank_instruction: str = (
        "Given a Vietnamese question, retrieve passages that answer the question"
    )

    # Auth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "https://rag.thuyetmt.io.vn/auth/callback"
    jwt_secret: str = "change-me"
    # Encrypts per-user integration credentials (Tavily API keys) at rest.
    # Falls back to a key derived from JWT_SECRET when unset.
    integration_encryption_key: str = ""
    jwt_issuer: str = "rag-api"
    jwt_audience: str = "rag-web"
    jwt_access_ttl_seconds: int = 900
    auth_revocation_ttl_seconds: int = 60
    # Development-only: skip Bearer authentication. Refused when
    # APP_ENV != development (see create_app).
    auth_disabled: bool = False
    # Comma-separated verified Google emails allowed to bootstrap a membership
    # on first login (invite-only stays the default for everyone else).
    auth_bootstrap_emails: str = ""
    auth_bootstrap_tenant_id: str = ""
    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    dev_tenant_id: UUID = UUID("00000000-0000-0000-0000-000000000002")

    # Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3001"

    # Agent limits
    agent_max_retrieval_iterations: int = 3
    agent_max_decomposition_tasks: int = 3
    # Evidence chunks passed to synthesis per retrieval task. Ollama runs
    # qwen3.5:9b with a 4096-token context; 20 chunks overflow it and the
    # prompt is truncated from the start (losing the system prompt and the
    # best-ranked evidence), so cap the prompt instead.
    synthesis_max_evidence_per_group: int = 8

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_models")
    @classmethod
    def _validate_models(cls, models: list[LLMModelOption]) -> list[LLMModelOption]:
        seen: set[str] = set()
        for model in models:
            if model.provider not in _LLM_PROVIDERS:
                raise ValueError(
                    f"unsupported provider for model {model.id!r}: {model.provider}"
                )
            if model.id in seen:
                raise ValueError(f"duplicate model id: {model.id}")
            seen.add(model.id)
        return models

    def available_models(self) -> list[LLMModelOption]:
        """Selectable models: the conf allowlist, or the configured default."""
        if self.llm_models:
            return list(self.llm_models)
        configured = (
            self.anthropic_chat_model
            if self.llm_provider == "anthropic"
            else self.openai_chat_model
        )
        return [
            LLMModelOption(
                id=configured, label=configured, provider=self.llm_provider
            )
        ]

    def default_model_option(self) -> LLMModelOption:
        """The configured default, falling back to the first allowed model."""
        options = self.available_models()
        for option in options:
            if option.id == self.llm_default_model:
                return option
        return options[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
