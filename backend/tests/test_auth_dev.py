"""Development-only auth bypass."""

from types import SimpleNamespace
from uuid import uuid4

from app.auth import Principal, get_principal


async def test_auth_disabled_returns_dev_principal():
    principal = Principal(uuid4(), uuid4(), frozenset({"member"}))
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(auth_disabled=True, dev_principal=principal)
        )
    )
    assert await get_principal(request) is principal  # type: ignore[arg-type]
