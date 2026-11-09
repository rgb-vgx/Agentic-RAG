"""Cheap document-type detection used to choose a parser profile.

The legal pipeline relies on article metadata and the scientific pipeline
needs layout-aware extraction. Detection happens before parsing, so it must
only read a few pages and the PDF outline.
"""

from __future__ import annotations

import re

_LEGAL_TERMS = (
    "bộ luật",
    "bo luat",
    "luật số",
    "luat so",
    "điều ",
    "dieu ",
    "khoản ",
    "khoan ",
    "quốc hội",
    "quoc hoi",
    "nghị định",
    "nghi dinh",
    "nghị quyết",
    "nghi quyet",
    "thông tư",
    "thong tu",
)
_SCIENTIFIC_TERMS = (
    "abstract",
    "references",
    "arxiv",
    "doi:",
    "keywords",
)


def detect_document_type(
    file_type: str,
    data: bytes,
    filename: str = "",
) -> str:
    """Return one of: legal | scientific | generic."""
    normalized = file_type.lower().lstrip(".")
    if normalized == "pdf":
        return _detect_pdf(data, filename)
    if normalized in {"docx", "doc"}:
        return "legal" if _looks_legal(filename.lower()) else "generic"
    if normalized in {"txt", "md", "markdown"}:
        sample = data[:12000].decode("utf-8", errors="ignore").lower()
        if any(term in sample for term in _LEGAL_TERMS):
            return "legal"
        if any(term in sample for term in _SCIENTIFIC_TERMS):
            return "scientific"
    return "generic"


def _looks_legal(text: str) -> bool:
    return any(term in text for term in ("luat", "bo-luat", "nghi-quyet", "nghi-dinh"))


def _detect_pdf(data: bytes, filename: str) -> str:
    try:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as document:
            toc = document.get_toc() or []
            sample = "\n".join(
                document[i].get_text()
                for i in range(min(3, document.page_count))
            ).lower()
    except Exception:  # noqa: BLE001 - detection must never block ingestion
        return "generic"

    scientific = 0
    legal = 0
    if len(toc) >= 3:
        scientific += 2
    if "abstract" in sample:
        scientific += 2
    if "references" in sample:
        scientific += 1
    if "arxiv" in sample:
        scientific += 1
    if re.search(r"(?m)^\s*\d+(?:\.\d+)*\.?\s+[A-Z]", sample):
        scientific += 1

    legal += 2 * sum(term in sample for term in _LEGAL_TERMS)
    if _looks_legal(filename.lower()):
        legal += 2

    if scientific >= 3 and scientific > legal:
        return "scientific"
    if legal >= 3:
        return "legal"
    return "generic"
