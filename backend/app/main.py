"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.v1.router import api_router
from app.auth import (
    ActiveChecker,
    AuthSettings,
    GoogleVerifier,
    Principal,
    cached_active_checker,
    make_google_verifier,
)
from app.config import settings
from app.infrastructure.database.session import set_engine
from app.infrastructure.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Startup: external connections (DB, Qdrant, Redis) are established lazily
    # by their owning modules so the app can boot without infrastructure.
    yield
    # Shutdown: close pooled connections here when wiring is added.


async def _db_is_active(principal: Principal) -> bool:
    """Revocation check against the database (cached by cached_active_checker)."""
    from app.infrastructure.database.repositories import is_member_active
    from app.infrastructure.database.session import get_session_factory

    async with get_session_factory()() as session:
        return await is_member_active(session, principal)


def create_app(
    *,
    auth: AuthSettings | None = None,
    google_verifier: GoogleVerifier | None = None,
    active_checker: ActiveChecker | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    """App factory.

    Tests inject a test engine, forged AuthSettings and a fake Google
    verifier; production uses the defaults derived from settings.
    """
    if engine is not None:
        set_engine(engine)

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

    app.state.auth = auth or AuthSettings(
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    app.state.google_verifier = google_verifier or make_google_verifier(
        settings.google_client_id
    )
    app.state.active_checker = active_checker or cached_active_checker(
        _db_is_active, ttl_seconds=settings.auth_revocation_ttl_seconds
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
