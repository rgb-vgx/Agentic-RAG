"""Citation validation (Section 7.5).

Deterministic — the backend owns citation truth. Two guarantees:

1. Marker validity: every [R<n>] in the answer must map to an evidence item
   in the immutable registry built from the FINAL evidence pack. Unmatched
   markers are stripped.

2. Claim support: stripping an invalid marker does NOT make the sentence
   grounded — "Employees get 30 days [R99]" must not survive as "Employees
   get 30 days". Claim-like sentences without any citation are dropped, and
   if too little supported content remains the whole answer is replaced by
   an explicit insufficiency message.

The registry is built once per validation from evidence_groups as they
exist at synthesis time — never re-derived from list positions after
rerank/reorder, so a reference id always resolves to the same chunk.
"""

import re
from dataclasses import dataclass, field

from app.domain.conversations.entities import SourceReference
from app.domain.retrieval.types import EvidenceGroup, RetrievedEvidence

_CITATION_RE = re.compile(r"\[(R\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Structural/meta sentences that never need a citation
_META_PREFIXES = (
    "dựa trên", "theo tài liệu", "theo các tài liệu", "không tìm thấy",
    "tài liệu không", "câu trả lời", "không đủ", "xin lỗi",
    "không có", "không được", "chưa có",
    "based on", "according to", "the answer", "the available evidence",
    "no evidence", "no relevant", "no document", "no information",
    "i cannot", "i can't", "sorry",
)

_FALLBACK_VI = "Tài liệu không cung cấp đủ bằng chứng để trả lời câu này."
_FALLBACK_EN = "The available evidence does not support an answer to this question."


@dataclass(frozen=True)
class CitationValidationResult:
    answer: str
    sources: list[SourceReference] = field(default_factory=list)
    invalid_references: list[str] = field(default_factory=list)
    dropped_claims: list[str] = field(default_factory=list)
    fallback: bool = False


class CitationValidator:
    def validate(
        self,
        answer: str,
        evidence_groups: list[EvidenceGroup],
        user_query: str = "",
        min_supported_ratio: float = 0.4,
    ) -> CitationValidationResult:
        # Models sometimes group markers as "[R1, R2]"; split them into
        # canonical adjacent markers before the rest of the pipeline.
        answer = re.sub(
            r"\[(R\d+(?:\s*,\s*R\d+)*)\]",
            lambda match: "".join(
                f"[{ref}]" for ref in re.findall(r"R\d+", match.group(1))
            ),
            answer,
        )

        # --- Immutable registry from the final evidence pack -------------
        registry: dict[str, RetrievedEvidence] = {}
        for group in evidence_groups:
            for evidence in group.evidence:
                if evidence.reference_id:
                    registry[evidence.reference_id] = evidence

        # --- 1. Marker validity ------------------------------------------
        used = _CITATION_RE.findall(answer)
        invalid = sorted({ref for ref in used if ref not in registry})

        cleaned = answer
        for ref in invalid:
            cleaned = cleaned.replace(f"[{ref}]", "")
        cleaned = re.sub(r"[ \t]+\.", ".", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # --- 2. Claim support --------------------------------------------
        cleaned, dropped = _drop_uncited_claims(cleaned)

        # Fallback when the answer no longer rests on evidence:
        #  - no citation survived although evidence existed, or
        #  - most claim content had to be dropped as unsupported.
        # With no evidence at all there is nothing to be grounded against,
        # so an honest "no information" answer passes through.
        all_claims = _claim_sentences(answer, include_cited=True)
        total_claim_chars = sum(len(s) for s in all_claims) or 1
        dropped_chars = sum(len(s) for s in dropped)
        lost_ratio = dropped_chars / total_claim_chars

        fallback = False
        if registry and (
            not _CITATION_RE.search(cleaned)
            or lost_ratio > (1.0 - min_supported_ratio)
        ):
            fallback = True

        if fallback:
            cleaned = _fallback_message(user_query)
            dropped = _claim_sentences(answer)
        elif not cleaned.strip():
            # Never return an empty answer — if everything was dropped and
            # there was no evidence to ground against, keep the original
            # (an honest "no information" response).
            cleaned = answer.strip()

        # --- Sources in citation order, deduped --------------------------
        sources = [
            _to_source(reference_id, registry[reference_id])
            for reference_id in dict.fromkeys(used)
            if reference_id in registry
        ]
        return CitationValidationResult(
            answer=cleaned,
            sources=sources,
            invalid_references=invalid,
            dropped_claims=dropped,
            fallback=fallback,
        )

    @staticmethod
    def cited_reference_ids(answer: str) -> list[str]:
        return list(dict.fromkeys(_CITATION_RE.findall(answer)))


# ---------------------------------------------------------------------------
# Claim-support heuristics
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _claim_sentences(text: str, include_cited: bool = False) -> list[str]:
    """Sentences that assert something factual (and thus need evidence)."""
    claims = []
    for sentence in _split_sentences(text):
        lowered = sentence.lower().lstrip()
        if any(lowered.startswith(prefix) for prefix in _META_PREFIXES):
            continue
        if not include_cited and _CITATION_RE.search(sentence):
            continue  # already grounded
        # Claim triggers: a number (quantity/date/percentage) or a full
        # assertion of several words. Short fragments are left alone.
        has_number = bool(re.search(r"\d", sentence))
        word_count = len(sentence.split())
        if has_number or word_count >= 8:
            claims.append(sentence)
    return claims


def _drop_uncited_claims(answer: str) -> tuple[str, list[str]]:
    """Remove claim-like sentences that carry no citation."""
    dropped: list[str] = []
    kept: list[str] = []
    for sentence in _split_sentences(answer):
        lowered = sentence.lower().lstrip()
        is_meta = any(lowered.startswith(prefix) for prefix in _META_PREFIXES)
        is_claim = bool(re.search(r"\d", sentence)) or len(sentence.split()) >= 8
        if is_claim and not is_meta and not _CITATION_RE.search(sentence):
            dropped.append(sentence)
        else:
            kept.append(sentence)
    return "\n\n".join(kept), dropped


def _fallback_message(user_query: str) -> str:
    """Match the query language (Vietnamese has diacritics)."""
    vietnamese = (
        r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộ"
        r"ơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]"
    )
    if re.search(vietnamese, user_query.lower()):
        return _FALLBACK_VI
    return _FALLBACK_EN


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
