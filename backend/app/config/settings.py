"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    # Object storage
    s3_endpoint_url: str | None = None
    s3_bucket: str = "agentic-rag"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # LLM
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_chat_model: str = "claude-sonnet-4-20250514"

    # Embeddings
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Reranking
    rerank_provider: str = "cohere"
    rerank_model: str = "rerank-multilingual-v3.0"
    rerank_api_key: str = ""

    # Auth
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret: str = "change-me"
    jwt_issuer: str = "rag-api"
    jwt_audience: str = "rag-web"
    jwt_access_ttl_seconds: int = 900
    auth_revocation_ttl_seconds: int = 60

    # Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3001"

    # Agent limits
    agent_max_retrieval_iterations: int = 3
    agent_max_decomposition_tasks: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
