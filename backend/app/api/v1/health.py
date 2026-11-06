"""Health and readiness endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict[str, str]:
    """Readiness probe.

    Infrastructure connections (PostgreSQL, Qdrant, Redis) are checked lazily
    once wiring is in place; until then the service reports its own state.
    """
    return {"status": "ready"}
