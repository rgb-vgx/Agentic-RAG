"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

try:  # FastAPI >= 0.162 keeps included routers lazy.
    from fastapi.routing import RouteContext, _IncludedRouter
except ImportError:  # older FastAPI exposes included routes directly
    RouteContext = None  # type: ignore[assignment]
    _IncludedRouter = ()  # type: ignore[assignment,misc]

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
from app.services.chat_service import build_node_context


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


class _FlatRoutesFastAPI(FastAPI):
    """Expose included routers as flattened routes.

    FastAPI 0.162 keeps ``include_router`` lazy, so ``app.router.routes``
    only contains an ``_IncludedRouter`` placeholder. Route introspection
    (and the public ``app.routes`` contract) expects the effective paths.
    Routing itself still uses ``app.router.routes``.
    """

    @property
    def routes(self):
        flat = []
        for route in self.router.routes:
            if isinstance(route, _IncludedRouter):
                for context in route.effective_route_contexts():
                    flat.append(RouteContext(context.original_route, context))
            else:
                flat.append(route)
        return flat


def create_app(
    *,
    auth: AuthSettings | None = None,
    google_verifier: GoogleVerifier | None = None,
    active_checker: ActiveChecker | None = None,
    engine: AsyncEngine | None = None,
    auth_disabled: bool | None = None,
) -> FastAPI:
    """App factory.

    Tests inject a test engine, forged AuthSettings and a fake Google
    verifier; production uses the defaults derived from settings.
    """
    if engine is not None:
        set_engine(engine)

    disabled = settings.auth_disabled if auth_disabled is None else auth_disabled
    if disabled and settings.app_env != "development":
        raise RuntimeError("AUTH_DISABLED is only allowed when APP_ENV=development")

    app = _FlatRoutesFastAPI(
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
    app.state.auth_disabled = disabled
    app.state.dev_principal = Principal(
        user_id=settings.dev_user_id,
        tenant_id=settings.dev_tenant_id,
        roles=frozenset({"member", "admin"}),
    )
    if disabled:
        app.state.active_checker = None
        logging.getLogger(__name__).warning(
            "AUTH_DISABLED=true: Bearer authentication is bypassed "
            "(development only)"
        )
    else:
        app.state.active_checker = active_checker or cached_active_checker(
            _db_is_active, ttl_seconds=settings.auth_revocation_ttl_seconds
        )
    # Tests override this to inject deterministic LLM/retriever fakes.
    app.state.chat_context_factory = build_node_context

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
