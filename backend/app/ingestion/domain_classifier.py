"""Document domain classification helpers.

Rule-based classification is deterministic and always available. The LLM
path is optional; its output is validated by :func:`parse_classifier_output`
so a malformed or hallucinated answer can never introduce an unknown slug.
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass
from itertools import islice

from app.domain.documents.domains import DomainDefinition


@dataclass(frozen=True)
class DomainSuggestion:
    domain: str
    confidence: float
    source: str
    reason: str = ""


# Phrases are stored without diacritics because both the document text and
# the phrases are folded before matching. Weights bias specific domains over
# broader ones (a contract is also a legal document).
_RULE_KEYWORDS: dict[str, dict[str, int]] = {
    "legal": {
        "bo luat": 4,
        "nghi dinh": 4,
        "thong tu": 4,
        "phap luat": 4,
        "quyet dinh": 2,
        "luat": 2,
        "dieu": 1,
        "legal": 3,
        "regulation": 3,
        "statute": 3,
    },
    "contract": {
        "hop dong": 5,
        "thoa thuan": 3,
        "ben a": 3,
        "ben b": 3,
        "dieu khoan": 3,
        "cam ket": 2,
        "contract": 5,
        "agreement": 4,
    },
    "scientific": {
        "nghien cuu": 4,
        "bai bao khoa hoc": 4,
        "bai bao": 2,
        "phuong phap": 3,
        "ket qua nghien cuu": 3,
        "tai lieu tham khao": 3,
        "hoc thuat": 3,
        "research": 4,
        "abstract": 3,
        "hypothesis": 3,
        "methodology": 3,
    },
    "financial": {
        "bao cao tai chinh": 5,
        "tai chinh": 3,
        "ke toan": 4,
        "doanh thu": 3,
        "loi nhuan": 3,
        "bang can doi": 3,
        "financial": 4,
        "revenue": 3,
        "balance sheet": 4,
        "cash flow": 3,
    },
    "technical": {
        "ky thuat": 4,
        "huong dan": 2,
        "van hanh": 3,
        "thiet ke": 3,
        "api": 3,
        "cai dat": 3,
        "technical": 4,
        "manual": 2,
        "specification": 3,
        "architecture": 2,
    },
    "medical": {
        "y te": 4,
        "benh": 3,
        "trieu chung": 3,
        "dieu tri": 4,
        "thuoc": 3,
        "benh nhan": 3,
        "medical": 4,
        "clinical": 4,
        "diagnosis": 4,
        "patient": 3,
        "treatment": 3,
    },
    "business": {
        "kinh doanh": 4,
        "thi truong": 4,
        "khach hang": 3,
        "chien luoc": 3,
        "marketing": 4,
        "doanh nghiep": 3,
        "business": 4,
        "sales": 3,
        "commerce": 3,
        "swot": 2,
    },
    "general": {
        "tong hop": 3,
        "tai lieu chung": 3,
        "ghi chu": 2,
        "miscellaneous": 3,
        "general document": 3,
        "general": 1,
    },
}

# Specific domains win a tied score over broad ones.
_DOMAIN_PRIORITY = (
    "contract",
    "financial",
    "medical",
    "scientific",
    "technical",
    "business",
    "legal",
    "general",
)


def _fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics for keyword matching."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("đ", "d").replace("Đ", "D").lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _score_domains(filename: str, text: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    haystack = _fold(f"{filename}\n{text[:20000]}")
    scores: dict[str, int] = {}
    matches: dict[str, list[str]] = {}
    for domain, keywords in _RULE_KEYWORDS.items():
        score = 0
        hits: list[str] = []
        for keyword, weight in keywords.items():
            pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
            occurrences = len(re.findall(pattern, haystack))
            if occurrences:
                score += weight * min(occurrences, 3)
                hits.append(keyword)
        if score:
            scores[domain] = score
            matches[domain] = hits
    return scores, matches


def rank_by_rules(filename: str, text: str) -> list[DomainSuggestion]:
    """All positive keyword matches, strongest first."""
    scores, matches = _score_domains(filename, text)
    if not scores:
        return []
    priority = {slug: index for index, slug in enumerate(_DOMAIN_PRIORITY)}
    ordered = sorted(
        scores,
        key=lambda slug: (-scores[slug], priority.get(slug, len(priority))),
    )
    suggestions: list[DomainSuggestion] = []
    for slug in ordered:
        confidence = min(0.95, 0.55 + 0.06 * scores[slug])
        reason = "Matched keywords: " + ", ".join(matches[slug][:5])
        suggestions.append(
            DomainSuggestion(
                domain=slug,
                confidence=round(confidence, 2),
                source="auto_rule",
                reason=reason,
            )
        )
    return suggestions


def classify_by_rules(filename: str, text: str) -> DomainSuggestion:
    """Classify with Vietnamese/English keywords; unknown -> general/fallback."""
    ranked = rank_by_rules(filename, text)
    if ranked:
        return ranked[0]
    return DomainSuggestion(
        domain="general",
        confidence=0.0,
        source="fallback",
        reason="No domain keywords matched",
    )


def _coerce_confidence(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        stripped = value.strip()
        percent = stripped.endswith("%")
        if percent:
            stripped = stripped[:-1].strip()
        try:
            number = float(stripped) / (100.0 if percent else 1.0)
        except ValueError:
            return 0.0
    else:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def _json_object(raw: object) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Tolerate ```json ... ``` and prose surrounding a single JSON object.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_classifier_output(
    raw: object, allowed_domains: set[str] | list[str] | tuple[str, ...]
) -> DomainSuggestion:
    """Validate model output. Never raises, even for completely malformed text."""
    fallback = DomainSuggestion(
        domain="general",
        confidence=0.0,
        source="fallback",
        reason="Invalid classifier output",
    )
    try:
        parsed = _json_object(raw)
        if parsed is None:
            return fallback
        domain_raw = parsed.get("domain")
        if not isinstance(domain_raw, str):
            return fallback
        from app.domain.documents.domains import normalize_domain_slug

        slug = normalize_domain_slug(domain_raw)
        allowed = {
            normalize_domain_slug(str(item)) for item in (allowed_domains or [])
        }
        if not slug or slug not in allowed:
            return fallback
        reason = parsed.get("reason", "")
        reason_text = str(reason).strip()[:500] if reason is not None else ""
        return DomainSuggestion(
            domain=slug,
            confidence=_coerce_confidence(parsed.get("confidence")),
            source="auto_llm",
            reason=reason_text,
        )
    except Exception:  # noqa: BLE001 - model output must never break the endpoint
        return fallback


def extract_text_sample(
    filename: str,
    data: bytes,
    *,
    max_pages: int = 3,
    max_chars: int = 4000,
) -> str:
    """Short, bounded text sample used by the preview classifier."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    text = ""
    if suffix == "pdf":
        import fitz  # PyMuPDF

        with fitz.open(stream=data, filetype="pdf") as document:
            text = "\n".join(
                page.get_text("text") for page in islice(document, max_pages)
            )
    elif suffix == "docx":
        from docx import Document as DocxDocument

        document = DocxDocument(io.BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:
        text = data.decode("utf-8", errors="replace")
    return text[:max_chars]


async def classify_with_llm(
    llm,
    filename: str,
    text: str,
    domains: list[DomainDefinition] | tuple[DomainDefinition, ...],
    *,
    max_chars: int = 3000,
) -> DomainSuggestion:
    """Ask the configured LLM for one allowed domain, as JSON only."""
    from app.infrastructure.llm.base import LLMMessage

    lines = []
    for domain in domains:
        hints = ", ".join(domain.hints[:6])
        line = f"- {domain.slug}: {domain.label} — {domain.description}"
        if hints:
            line += f" (hints: {hints})"
        lines.append(line)
    prompt = (
        "Classify the document into exactly one domain from the list below.\n"
        "Return ONLY a JSON object with keys domain, confidence (0-1) and reason.\n\n"
        "Domains:\n"
        + "\n".join(lines)
        + f"\n\nFilename: {filename}\nContent sample:\n{text[:max_chars]}"
    )
    response = await llm.complete(
        [
            LLMMessage(
                role="system",
                content="You are a document domain classifier. Return JSON only.",
            ),
            LLMMessage(role="user", content=prompt),
        ],
        temperature=0.0,
        max_tokens=200,
        json_mode=True,
    )
    return parse_classifier_output(
        getattr(response, "content", response), {domain.slug for domain in domains}
    )
