"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.infrastructure.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Startup: external connections (DB, Qdrant, Redis) are established lazily
    # by their owning modules so the app can boot without infrastructure.
    yield
    # Shutdown: close pooled connections here when wiring is added.


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic RAG API",
        version="0.1.0",
        description="Adaptive Document Intelligence & Question Answering System",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
