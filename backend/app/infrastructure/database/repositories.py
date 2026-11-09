"""Repository layer — Principal-bound data access (rag_auth port, ORM style).

Hard rules, ported from the reference package and enforced by
tests/integration/test_repos_direct.py:
- No method takes tenant_id/user_id from the caller: they always come from
  the Principal bound at construction.
- Every query — including INSERT/UPDATE/DELETE — carries the ownership
  condition; API-level checks are not the only line of defence.
- Visibility: documents = same tenant AND (scope='org' OR owner=me);
  conversations/messages/runs = same tenant AND same user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.domain.documents.entities import DocumentStatus
from app.infrastructure.database.models import (
    AgentRunRecord,
    ConversationRecord,
    DocumentDomainRecord,
    DocumentRecord,
    MembershipRecord,
    MessageRecord,
    UserRecord,
)

Scope = Literal["org", "personal"]


# ---- identity (before a Principal exists) ------------------------------------------------
async def find_member_by_google_sub(session: AsyncSession, sub: str) -> dict | None:
    """Membership row for a Google subject, or None (invite-only: no auto-provision)."""
    stmt = (
        select(UserRecord, MembershipRecord)
        .join(MembershipRecord, MembershipRecord.user_id == UserRecord.id)
        .where(UserRecord.google_sub == sub)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    user, membership = row
    return {
        "user_id": user.id,
        "disabled_at": user.disabled_at,
        "tenant_id": membership.tenant_id,
        "roles": list(membership.roles),
    }


async def is_member_active(session: AsyncSession, p: Principal) -> bool:
    """Revocation check: user still a member of the token's tenant and not disabled."""
    stmt = (
        select(UserRecord.id)
        .join(MembershipRecord, MembershipRecord.user_id == UserRecord.id)
        .where(
            UserRecord.id == p.user_id,
            MembershipRecord.tenant_id == str(p.tenant_id),
            UserRecord.disabled_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    return result.first() is not None


# ---- documents: org shared in tenant, personal visible to owner only --------------------
class DocumentRepository:
    def __init__(self, session: AsyncSession, principal: Principal):
        self.session = session
        self.principal = principal

    def _visible(self):
        p = self.principal
        return and_(
            DocumentRecord.tenant_id == str(p.tenant_id),
            or_(DocumentRecord.scope == "org", DocumentRecord.user_id == p.user_id),
        )

    async def add(self, record: DocumentRecord) -> DocumentRecord:
        if record.scope not in ("org", "personal"):
            raise ValueError("scope")
        # tenant/owner always come from the Principal, never from the caller
        record.tenant_id = str(self.principal.tenant_id)
        record.user_id = self.principal.user_id
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, document_id: UUID) -> DocumentRecord | None:
        stmt = select(DocumentRecord).where(
            DocumentRecord.id == document_id,
            self._visible(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self, knowledge_base_id: str | None = None, limit: int = 100
    ) -> list[DocumentRecord]:
        stmt = select(DocumentRecord).where(
            self._visible(),
            DocumentRecord.status != DocumentStatus.DELETING.value,
        )
        if knowledge_base_id:
            stmt = stmt.where(DocumentRecord.knowledge_base_id == knowledge_base_id)
        stmt = stmt.order_by(DocumentRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def can_manage(self, doc: DocumentRecord) -> bool:
        """Delete/reprocess: personal -> owner only (admin does NOT outrank it);
        org -> owner or admin."""
        if doc.user_id == self.principal.user_id:
            return True
        return doc.scope == "org" and "admin" in self.principal.roles

    async def delete(self, document_id: UUID) -> Literal["deleted", "not_found", "forbidden"]:
        doc = await self.get(document_id)
        if doc is None:
            return "not_found"
        if not self.can_manage(doc):
            return "forbidden"
        # Soft delete: the background job retires the chunk set and removes files.
        if doc.status != DocumentStatus.DELETING.value:
            doc.status = DocumentStatus.DELETING.value
            await self.session.commit()
        return "deleted"

    async def quarantine(self, document_id: UUID) -> DocumentRecord | None:
        """Org documents only; personal docs of others are not even visible here."""
        doc = await self.get(document_id)
        if doc is None or doc.scope != "org":
            return None
        if doc.quarantined_at is None:
            doc.quarantined_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(doc)
        return doc

    async def active_chunk_sets(
        self, only_doc_ids: list[UUID] | None = None
    ) -> dict[UUID, UUID]:
        """Retrieval snapshot for THIS principal. only_doc_ids can only NARROW
        the snapshot, never widen it."""
        stmt = select(
            DocumentRecord.id, DocumentRecord.active_chunk_set_id
        ).where(
            self._visible(),
            DocumentRecord.active_chunk_set_id.is_not(None),
            DocumentRecord.quarantined_at.is_(None),
            DocumentRecord.status != DocumentStatus.DELETING.value,
        )
        if only_doc_ids is not None:
            stmt = stmt.where(DocumentRecord.id.in_(only_doc_ids))
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def retrieval_metadata(
        self, document_ids: list[UUID] | None = None
    ) -> list[dict]:
        """Identity metadata used to resolve exact legal references.

        Scoped by the same visibility gate as retrieval; never widen it.
        """
        stmt = select(
            DocumentRecord.id,
            DocumentRecord.filename,
            DocumentRecord.title,
            DocumentRecord.title_normalized,
            DocumentRecord.document_number,
            DocumentRecord.document_year,
        ).where(
            self._visible(),
            DocumentRecord.active_chunk_set_id.is_not(None),
            DocumentRecord.quarantined_at.is_(None),
            DocumentRecord.status != DocumentStatus.DELETING.value,
        )
        if document_ids is not None:
            stmt = stmt.where(DocumentRecord.id.in_(document_ids))
        result = await self.session.execute(stmt)
        return [
            {
                "id": str(row[0]),
                "filename": row[1],
                "title": row[2],
                "title_normalized": row[3],
                "document_number": row[4],
                "document_year": row[5],
            }
            for row in result.all()
        ]

    async def update_status(
        self,
        document_id: UUID,
        status: str,
        error_message: str | None = None,
        quality_report: dict | None = None,
        run_metadata: dict | None = None,
    ) -> None:
        """Status transitions go through the same visibility gate — there is no
        tenant-less bypass path anymore."""
        record = await self.get(document_id)
        if record is None:
            return
        record.status = status
        if error_message is not None:
            record.error_message = error_message
        if quality_report is not None:
            record.quality_report = quality_report
        if run_metadata is not None:
            record.run_metadata = run_metadata
        await self.session.commit()


# ---- document domains: built-ins are global, customs are per tenant ----------------------
class DocumentDomainRepository:
    def __init__(self, session: AsyncSession, principal: Principal):
        self.session = session
        self.principal = principal

    def _tenant(self):
        return DocumentDomainRecord.tenant_id == str(self.principal.tenant_id)

    async def list_custom(self) -> list[DocumentDomainRecord]:
        stmt = (
            select(DocumentDomainRecord)
            .where(self._tenant())
            .order_by(
                DocumentDomainRecord.created_at.asc(),
                DocumentDomainRecord.slug.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_custom(self, slug: str) -> DocumentDomainRecord | None:
        stmt = select(DocumentDomainRecord).where(
            self._tenant(),
            DocumentDomainRecord.slug == slug,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_custom(self) -> int:
        stmt = select(func.count()).select_from(DocumentDomainRecord).where(
            self._tenant()
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def add(self, record: DocumentDomainRecord) -> DocumentDomainRecord:
        record.tenant_id = str(self.principal.tenant_id)
        record.created_by = self.principal.user_id
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def save(self, record: DocumentDomainRecord) -> DocumentDomainRecord:
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def delete(self, record: DocumentDomainRecord) -> None:
        # Deleting a domain must not leave documents pointing at a ghost slug.
        await self.session.execute(
            update(DocumentRecord)
            .where(
                DocumentRecord.tenant_id == str(self.principal.tenant_id),
                DocumentRecord.domain == record.slug,
            )
            .values(
                domain="general",
                domain_source="fallback",
                domain_confidence=None,
            )
        )
        await self.session.delete(record)
        await self.session.commit()


# ---- conversations: private per (tenant, user) -------------------------------------------
class ConversationRepository:
    def __init__(self, session: AsyncSession, principal: Principal):
        self.session = session
        self.principal = principal

    def _owned(self):
        p = self.principal
        return and_(
            ConversationRecord.tenant_id == str(p.tenant_id),
            ConversationRecord.user_id == p.user_id,
        )

    async def create(self, title: str) -> ConversationRecord:
        record = ConversationRecord(
            tenant_id=str(self.principal.tenant_id),
            user_id=self.principal.user_id,
            title=title,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, conversation_id: UUID) -> ConversationRecord | None:
        stmt = select(ConversationRecord).where(
            ConversationRecord.id == conversation_id,
            self._owned(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def rename(
        self, conversation_id: UUID, title: str
    ) -> ConversationRecord | None:
        record = await self.get(conversation_id)
        if record is None:
            return None
        record.title = title
        record.title_source = "manual"
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def set_title(
        self,
        conversation_id: UUID,
        title: str,
        *,
        source: str,
        only_if_source: str | None = None,
    ) -> ConversationRecord | None:
        record = await self.get(conversation_id)
        if record is None:
            return None
        if only_if_source is not None and record.title_source != only_if_source:
            return record
        record.title = title
        record.title_source = source
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def list(self, limit: int = 100) -> list[ConversationRecord]:
        stmt = (
            select(ConversationRecord)
            .where(self._owned())
            .order_by(ConversationRecord.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def messages(self, conversation_id: UUID) -> list[MessageRecord] | None:
        if await self.get(conversation_id) is None:
            return None
        stmt = (
            select(MessageRecord)
            .where(
                MessageRecord.conversation_id == conversation_id,
                MessageRecord.tenant_id == str(self.principal.tenant_id),
                MessageRecord.user_id == self.principal.user_id,
            )
            .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        *,
        sources: list | None = None,
        model_name: str | None = None,
        token_usage: dict | None = None,
    ) -> MessageRecord | None:
        """Ownership is resolved BEFORE the insert and the composite FK
        (conversation_id, tenant_id, user_id) makes the DB reject any drift."""
        conv = await self.get(conversation_id)
        if conv is None:
            return None
        if role == "user" and conv.title in {"New conversation", ""}:
            existing = await self.session.execute(
                select(MessageRecord.id)
                .where(
                    MessageRecord.conversation_id == conv.id,
                    MessageRecord.role == "user",
                )
                .limit(1)
            )
            if existing.first() is None:
                conv.title = " ".join(content.split())[:80] or conv.title
                conv.title_source = "first_message"
        record = MessageRecord(
            conversation_id=conv.id,
            tenant_id=conv.tenant_id,
            user_id=conv.user_id,
            role=role,
            content=content,
            sources=sources or [],
            model_name=model_name,
            token_usage=token_usage or {},
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def delete(self, conversation_id: UUID) -> bool:
        record = await self.get(conversation_id)
        if record is None:
            return False
        await self.session.delete(record)
        await self.session.commit()
        return True


# ---- agent runs: snapshot per (user, run) --------------------------------------------------
class AgentRunRepository:
    def __init__(self, session: AsyncSession, principal: Principal):
        self.session = session
        self.principal = principal

    def _owned(self):
        p = self.principal
        return and_(
            AgentRunRecord.tenant_id == str(p.tenant_id),
            AgentRunRecord.user_id == p.user_id,
        )

    async def create(
        self,
        conversation_id: UUID,
        *,
        query: str,
        snapshot: dict[UUID, UUID] | None = None,
        model_provider: str = "openai",
        model_name: str = "",
    ) -> AgentRunRecord | None:
        # Ownership check happens first: never create a run for someone else's
        # conversation, and never spend tokens on a request that will be denied.
        conv_repo = ConversationRepository(self.session, self.principal)
        if await conv_repo.get(conversation_id) is None:
            return None
        record = AgentRunRecord(
            conversation_id=conversation_id,
            tenant_id=str(self.principal.tenant_id),
            user_id=self.principal.user_id,
            query=query,
            snapshot={str(k): str(v) for k, v in (snapshot or {}).items()},
            model_provider=model_provider,
            model_name=model_name,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, run_id: UUID) -> AgentRunRecord | None:
        stmt = select(AgentRunRecord).where(
            AgentRunRecord.id == run_id,
            self._owned(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self,
        run_id: UUID,
        *,
        status: str | None = None,
        final_answer: str | None = None,
        citations: list | None = None,
        routing_path: str | None = None,
        retrieval_iterations: int | None = None,
        token_usage: dict | None = None,
        latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> None:
        record = await self.get(run_id)   # ownership inside the SELECT
        if record is None:
            return
        if status is not None:
            record.status = status
        if final_answer is not None:
            record.final_answer = final_answer
        if citations is not None:
            record.citations = citations
        if routing_path is not None:
            record.routing_path = routing_path
        if retrieval_iterations is not None:
            record.retrieval_iterations = retrieval_iterations
        if token_usage is not None:
            record.token_usage = token_usage
        if latency_ms is not None:
            record.latency_ms = latency_ms
        if error_message is not None:
            record.error_message = error_message
        await self.session.commit()
