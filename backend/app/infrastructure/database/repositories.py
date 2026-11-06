"""Repository layer — data access for API/services.

Repositories take an AsyncSession and expose domain-friendly methods.
They never leak SQLAlchemy models outside the infrastructure layer.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import (
    AgentRunRecord,
    ConversationRecord,
    DocumentRecord,
    MessageRecord,
)


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, record: DocumentRecord) -> DocumentRecord:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, document_id: UUID, tenant_id: str) -> DocumentRecord | None:
        stmt = select(DocumentRecord).where(
            DocumentRecord.id == document_id,
            DocumentRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_kb(
        self, tenant_id: str, knowledge_base_id: str | None = None, limit: int = 100
    ) -> list[DocumentRecord]:
        stmt = select(DocumentRecord).where(DocumentRecord.tenant_id == tenant_id)
        if knowledge_base_id:
            stmt = stmt.where(DocumentRecord.knowledge_base_id == knowledge_base_id)
        stmt = stmt.order_by(DocumentRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        document_id: UUID,
        status: str,
        error_message: str | None = None,
        quality_report: dict | None = None,
        run_metadata: dict | None = None,
    ) -> None:
        record = await self.get(document_id, tenant_id="")  # internal path
        if record is None:
            record = await self.session.get(DocumentRecord, document_id)
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

    async def delete(self, document_id: UUID) -> None:
        record = await self.session.get(DocumentRecord, document_id)
        if record is not None:
            await self.session.delete(record)
            await self.session.commit()


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, record: ConversationRecord) -> ConversationRecord:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, conversation_id: UUID, tenant_id: str) -> ConversationRecord | None:
        stmt = select(ConversationRecord).where(
            ConversationRecord.id == conversation_id,
            ConversationRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self, tenant_id: str, user_id: UUID, limit: int = 100
    ) -> list[ConversationRecord]:
        stmt = (
            select(ConversationRecord)
            .where(
                ConversationRecord.tenant_id == tenant_id,
                ConversationRecord.user_id == user_id,
            )
            .order_by(ConversationRecord.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, conversation_id: UUID) -> None:
        record = await self.session.get(ConversationRecord, conversation_id)
        if record is not None:
            await self.session.delete(record)
            await self.session.commit()


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, record: MessageRecord) -> MessageRecord:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def list_for_conversation(
        self, conversation_id: UUID, limit: int = 200
    ) -> list[MessageRecord]:
        stmt = (
            select(MessageRecord)
            .where(MessageRecord.conversation_id == conversation_id)
            .order_by(MessageRecord.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AgentRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, record: AgentRunRecord) -> AgentRunRecord:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

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
        record = await self.session.get(AgentRunRecord, run_id)
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
