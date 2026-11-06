"""Evidence evaluation (Section 7.3).

LLM-based sufficiency judgment with deterministic guardrails: the evaluator
only sees retrieved evidence, never its own prior knowledge, and its output
is validated against the allowed status vocabulary.
"""

import json

from app.agentic_rag import prompts
from app.domain.agents.state import EvidenceEvaluationResult
from app.domain.retrieval.types import EvidenceGroup, EvidenceStatus
from app.infrastructure.llm.base import BaseLLMClient, LLMMessage

_ALLOWED_STATUSES = {status.value for status in EvidenceStatus}


class EvidenceEvaluator:
    def __init__(self, llm: BaseLLMClient):
        self.llm = llm

    async def evaluate(self, group: EvidenceGroup) -> EvidenceEvaluationResult:
        if not group.evidence:
            return EvidenceEvaluationResult(
                status=EvidenceStatus.INSUFFICIENT,
                missing_information=["No evidence retrieved for this sub-query"],
                rationale="Empty evidence group",
            )

        evidence_text = self._render_evidence(group)
        try:
            data = await self.llm.complete_json(
                [
                    LLMMessage(
                        role="system",
                        content=prompts.EVALUATE_EVIDENCE.template.format(
                            query=group.query, evidence=evidence_text
                        ),
                    )
                ],
                max_tokens=800,
            )
            status = data.get("status", "INSUFFICIENT")
            if status not in _ALLOWED_STATUSES:
                status = "INSUFFICIENT"
            return EvidenceEvaluationResult(
                status=EvidenceStatus(status),
                missing_information=list(data.get("missing_information", [])),
                contradictions=list(data.get("contradictions", [])),
                rationale=str(data.get("rationale", ""))[:500],
            )
        except (ValueError, json.JSONDecodeError):
            # Deterministic fallback: non-empty evidence → partially sufficient
            return EvidenceEvaluationResult(
                status=EvidenceStatus.PARTIALLY_SUFFICIENT,
                rationale="LLM evaluation unavailable; heuristic applied",
            )

    @staticmethod
    def _render_evidence(group: EvidenceGroup) -> str:
        parts = []
        for evidence in group.evidence:
            location = ""
            if evidence.page_start:
                location = f" (p.{evidence.page_start}"
                if evidence.page_end and evidence.page_end != evidence.page_start:
                    location += f"-{evidence.page_end}"
                location += ")"
            heading = " > ".join(evidence.heading_path)
            parts.append(
                f"[{evidence.reference_id}] {heading}{location}\n{evidence.content}"
            )
        return "\n\n".join(parts)
