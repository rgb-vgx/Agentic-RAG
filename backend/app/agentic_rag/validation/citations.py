"""Citation validation (Section 7.5).

Deterministic — the backend owns citation truth. Every [R<n>] marker in the
answer must map to a reference id actually provided in the evidence;
unmatched markers are stripped and the answer is flagged. This runs after
synthesis and before persistence/streaming of the final answer.
"""

import re

from app.domain.conversations.entities import SourceReference
from app.domain.retrieval.types import EvidenceGroup, RetrievedEvidence

_CITATION_RE = re.compile(r"\[(R\d+)\]")


class CitationValidator:
    def validate(
        self, answer: str, evidence_groups: list[EvidenceGroup]
    ) -> tuple[str, list[SourceReference], list[str]]:
        """Return (cleaned_answer, sources, invalid_reference_ids)."""
        available: dict[str, RetrievedEvidence] = {}
        for group in evidence_groups:
            for evidence in group.evidence:
                if evidence.reference_id:
                    available[evidence.reference_id] = evidence

        used = _CITATION_RE.findall(answer)
        invalid = sorted({ref for ref in used if ref not in available})

        cleaned = answer
        for ref in invalid:
            cleaned = cleaned.replace(f"[{ref}]", "")

        # Collapse artifacts like "  ." left by removed citations
        cleaned = re.sub(r"[ \t]+\.", ".", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        sources = [
            _to_source(reference_id, available[reference_id])
            for reference_id in dict.fromkeys(used)  # dedupe, keep order
            if reference_id in available
        ]
        return cleaned, sources, invalid

    @staticmethod
    def cited_reference_ids(answer: str) -> list[str]:
        return list(dict.fromkeys(_CITATION_RE.findall(answer)))


def _to_source(reference_id: str, evidence: RetrievedEvidence) -> SourceReference:
    return SourceReference(
        reference_id=reference_id,
        chunk_id=evidence.chunk_id,
        document_id=evidence.document_id,
        page_start=evidence.page_start,
        page_end=evidence.page_end,
        heading_path=evidence.heading_path,
        snippet=evidence.content[:300],
    )
