"""Answer synthesis (Section 7.4).

Produces a grounded answer with [R<n>] citations. Streaming mode yields
deltas for SSE; non-streaming mode is used by internal nodes and tests.
"""

from app.agentic_rag import prompts
from app.config import settings
from app.domain.retrieval.types import EvidenceGroup, RetrievedEvidence
from app.infrastructure.llm.base import BaseLLMClient, LLMMessage


class AnswerSynthesizer:
    def __init__(self, llm: BaseLLMClient):
        self.llm = llm

    def build_messages(
        self, query: str, evidence_groups: list[EvidenceGroup]
    ) -> list[LLMMessage]:
        evidence_text = self._render(evidence_groups)
        return [
            LLMMessage(
                role="system",
                content=prompts.SYNTHESIZE_ANSWER.template.format(
                    query=query, evidence=evidence_text
                ),
            )
        ]

    async def synthesize(
        self, query: str, evidence_groups: list[EvidenceGroup]
    ) -> str:
        response = await self.llm.complete(
            self.build_messages(query, evidence_groups),
            temperature=0.2,
            max_tokens=settings.synthesis_max_tokens,
        )
        return response.content

    async def stream(
        self, query: str, evidence_groups: list[EvidenceGroup]
    ):
        """Yield answer deltas for the SSE token event."""
        async for delta in self.llm.stream(
            self.build_messages(query, evidence_groups),
            temperature=0.2,
            max_tokens=settings.synthesis_max_tokens,
        ):
            yield delta

    @staticmethod
    def _render(groups: list[EvidenceGroup]) -> str:
        parts: list[str] = []
        for group in groups:
            if group.evidence:
                parts.append(f"## Sub-query: {group.query}")
            for evidence in group.evidence:
                parts.append(_format_evidence(evidence))
        return "\n\n".join(parts) or "(no evidence)"


def _format_evidence(evidence: RetrievedEvidence) -> str:
    location = ""
    if evidence.page_start:
        location = f" (page {evidence.page_start}"
        if evidence.page_end and evidence.page_end != evidence.page_start:
            location += f"-{evidence.page_end}"
        location += ")"
    heading = " > ".join(evidence.heading_path)
    source = f"{heading}{location}" if heading else location.strip()
    return f"[{evidence.reference_id}] {source}\n{evidence.content}"
