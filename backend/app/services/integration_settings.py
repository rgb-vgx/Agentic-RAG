"""Per-user integration credentials, encrypted at rest.

Only the server ever sees the plaintext key. GET responses expose a masked
suffix so the UI can show whether a key is configured.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.config import settings
from app.infrastructure.database.models import IntegrationSettingRecord

_PROVIDERS = {"tavily"}


def _fernet() -> Fernet:
    raw = settings.integration_encryption_key or settings.jwt_secret
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Stored integration key cannot be decrypted") from exc


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "••••"
    return f"{value[:5]}…{value[-4:]}"


class IntegrationSettingsService:
    def __init__(self, session: AsyncSession, principal: Principal):
        self.session = session
        self.principal = principal

    def _owned(self):
        return (
            IntegrationSettingRecord.tenant_id == str(self.principal.tenant_id),
            IntegrationSettingRecord.user_id == self.principal.user_id,
        )

    async def get(self, provider: str) -> IntegrationSettingRecord | None:
        if provider not in _PROVIDERS:
            raise ValueError("Unsupported integration")
        stmt = select(IntegrationSettingRecord).where(
            *self._owned(),
            IntegrationSettingRecord.provider == provider,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_key(self, provider: str) -> str | None:
        record = await self.get(provider)
        if record is None or not record.enabled:
            return None
        return decrypt_secret(record.encrypted_key)

    async def upsert(
        self,
        provider: str,
        api_key: str | None,
        *,
        enabled: bool = True,
    ) -> IntegrationSettingRecord:
        if provider not in _PROVIDERS:
            raise ValueError("Unsupported integration")
        record = await self.get(provider)
        if record is None:
            if not api_key:
                raise ValueError("API key is required")
            record = IntegrationSettingRecord(
                tenant_id=str(self.principal.tenant_id),
                user_id=self.principal.user_id,
                provider=provider,
                encrypted_key=encrypt_secret(api_key),
                enabled=enabled,
            )
            self.session.add(record)
        else:
            if api_key:
                record.encrypted_key = encrypt_secret(api_key)
            record.enabled = enabled
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def delete(self, provider: str) -> bool:
        record = await self.get(provider)
        if record is None:
            return False
        await self.session.delete(record)
        await self.session.commit()
        return True
