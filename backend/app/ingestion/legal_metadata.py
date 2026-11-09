"""Legal document identity extraction.

Vietnamese legal questions often reference a document and an article by
number ("Điều 19 Bộ luật tố tụng dân sự"). Embedding similarity is a poor
tool for that lookup, so ingestion records the identity as metadata and
retrieval can filter on it.

The extractor is deliberately conservative: it only uses the document
preamble/title block and the section heading. It never infers an article
number from arbitrary content.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.infrastructure.vectorstores.sparse import strip_diacritics

_NUMBER_RE = re.compile(
    r"Số\s*:\s*(\d{1,3})\s*/\s*(\d{4})\s*/\s*([A-ZĐ0-9][A-ZĐ0-9\-]*)",
    re.IGNORECASE,
)
_FILENAME_NUMBER_RE = re.compile(
    r"(?<!\d)(\d{1,3})[_\-/](\d{4})[_\-/]([A-Za-zĐ0-9][A-Za-zĐ0-9\-]*)"
)
_ARTICLE_RE = re.compile(
    r"^\s*Điều\s+(\d+[a-zđ]?)\s*[.:]?\s*(.*)$",
    re.IGNORECASE,
)
_MULTISPACE_RE = re.compile(r"\s+")

# Words that carry no document identity in a legal query. They are removed
# before document resolution and before deciding whether a query is a pure
# citation lookup.
_QUERY_STOPWORDS = {
    "hay", "trich", "cua", "cho", "toi", "ve", "gi", "nao", "la", "thong",
    "tin", "noi", "toan", "van", "xem", "giup", "the", "nhu", "va", "co",
    "khong", "mot", "cac", "nhung", "trong", "tai", "theo", "duoc", "bi",
    "cach", "de", "nay", "do", "ra", "sao", "bao", "nhieu",
}
_QUERY_PHRASE_RE = re.compile(
    r"\b(?:trich\s+dan|cho\s+toi|hay\s+cho|xem\s+giup|thong\s+tin|"
    r"noi\s+dung|toan\s+van)\b"
)
_DOCUMENT_PREFIX_WORDS = {
    "bo", "luat", "nghi", "quyet", "dinh", "thong", "tu",
}


@dataclass(slots=True)
class LegalDocumentMetadata:
    title: str | None = None
    title_normalized: str | None = None
    document_number: str | None = None
    document_year: int | None = None


@dataclass(slots=True)
class LegalReference:
    """Parsed structural reference from a user query."""

    article_number: str | None = None
    clause_number: str | None = None
    point: str | None = None
    document_number: str | None = None
    document_year: int | None = None
    document_hint: str = ""
    matched_spans: list[tuple[int, int]] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """NFC + casefold + accent stripping + punctuation folding."""
    value = unicodedata.normalize("NFC", text or "").casefold()
    value = strip_diacritics(value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return _MULTISPACE_RE.sub(" ", value).strip()


def normalize_document_number(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = unicodedata.normalize("NFC", value).upper()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned or None


def _strip_preamble_headers(text: str) -> str:
    """Remove letterhead/date lines that precede the document title."""
    value = re.sub(
        r"\bLuật\s+số\s*:?\s*\d+/\d+/[A-Za-zĐ0-9\-]+",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bSố\s*:\s*\d+/\d+/[A-Za-zĐ0-9\-]+",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:Hà\s+Nội|TP\.?\s*Hồ\s+Chí\s+Minh)\s*,\s*ngày\s+"
        r"\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:QUỐC\s+HỘI|CỘNG\s+HÒA\s+XÃ\s+HỘI\s+CHỦ\s+NGHĨA\s+VIỆT\s+NAM|"
        r"Độc\s+lập\s*-\s*Tự\s+do\s*-\s*Hạnh\s+phúc)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return _MULTISPACE_RE.sub(" ", value).strip()


def _clean_title(raw: str) -> str:
    value = _MULTISPACE_RE.sub(" ", raw).strip(" .,:;-")
    # Stop at the legal preamble; the title block precedes it.
    value = re.split(r"\bCăn\s+cứ\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = value.strip(" .,:;-")
    if len(value) > 160:
        value = value[:160].rsplit(" ", 1)[0]
    if not value:
        return value
    return value[0].upper() + value[1:].lower()


def extract_document_metadata(
    filename: str,
    ir,
) -> LegalDocumentMetadata:
    """Extract title / number / year from the parsed preamble and filename."""
    preamble = " ".join(
        block.text for block in (getattr(ir, "blocks", None) or [])[:60] if block.text
    )
    preamble = _MULTISPACE_RE.sub(" ", preamble)[:8000]
    title_source = _strip_preamble_headers(preamble)

    number = None
    year = None
    number_match = _NUMBER_RE.search(preamble)
    if number_match:
        number = normalize_document_number(
            f"{number_match.group(1)}/{number_match.group(2)}/{number_match.group(3)}"
        )
        year = int(number_match.group(2))
    else:
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "")
        fallback = _FILENAME_NUMBER_RE.search(stem)
        if fallback:
            number = normalize_document_number(
                f"{fallback.group(1)}/{fallback.group(2)}/{fallback.group(3)}"
            )
            year = int(fallback.group(2))

    title = None
    number_upper = (number or "").upper()
    if "NQ" in number_upper:
        patterns = (r"\bNGHỊ\s+QUYẾT\b",)
    elif "NĐ-CP" in number_upper or "ND-CP" in number_upper:
        patterns = (r"\bNGHỊ\s+ĐỊNH\b",)
    elif number_upper.startswith("TT") or "/TT" in number_upper:
        patterns = (r"\bTHÔNG\s+TƯ\b",)
    elif "QH" in number_upper:
        patterns = (r"\bBỘ\s+LUẬT\b", r"\bLUẬT\b")
    else:
        patterns = (
            r"\bBỘ\s+LUẬT\b",
            r"\bLUẬT\b",
            r"\bNGHỊ\s+QUYẾT\b",
            r"\bNGHỊ\s+ĐỊNH\b",
            r"\bTHÔNG\s+TƯ\b",
            r"\bQUYẾT\s+ĐỊNH\b",
        )
    type_match = None
    for type_pattern in patterns:
        type_match = re.search(type_pattern, title_source, re.IGNORECASE)
        if type_match:
            break
    if type_match:
        rest = title_source[type_match.start() :]
        title = _clean_title(rest)
    if not title:
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "")
        stem = re.sub(r"[_\-]+", " ", stem).strip()
        title = _clean_title(stem) or None

    if year is None:
        year_match = re.search(r"\b(19|20)\d{2}\b", preamble)
        if year_match:
            year = int(year_match.group(0))

    return LegalDocumentMetadata(
        title=title,
        title_normalized=normalize_text(title) if title else None,
        document_number=number,
        document_year=year,
    )


def extract_article(
    heading_path: list[str] | None,
) -> tuple[str | None, str | None]:
    """Return (article_number, article_title) from a verified heading path.

    Only the heading is used; inferring from arbitrary chunk content would
    mislabel chunks that span more than one article.
    """
    for heading in reversed(heading_path or []):
        match = _ARTICLE_RE.match(heading or "")
        if match:
            return match.group(1), (match.group(2).strip() or None)
    return None, None


def document_aliases(
    title: str | None,
    filename: str | None = None,
    document_number: str | None = None,
) -> list[str]:
    """Aliases used to resolve a document mention without embeddings."""
    aliases: set[str] = set()
    if title:
        normalized = normalize_text(title)
        aliases.add(normalized)
        words = normalized.split()
        if words and words[0] in {"bo", "luat", "nghi", "quyet", "dinh", "thong", "tu"}:
            aliases.add(" ".join(words[1:]))
        # BLTTDS, BLHS, ...: BL + initials of the substantive words.
        initials = "".join(
            word[0]
            for word in words
            if word and word not in _DOCUMENT_PREFIX_WORDS
        )
        if initials:
            prefix = "BL" if normalized.startswith("bo luat") else ""
            aliases.add((prefix + initials).upper())
    if filename:
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)
        stem = re.sub(r"[_\-]+", " ", stem)
        aliases.add(normalize_text(stem))
    if document_number:
        aliases.add(normalize_document_number(document_number) or "")
    return sorted(alias for alias in aliases if len(alias) >= 3)


def parse_legal_reference(query: str) -> LegalReference | None:
    """Deterministically parse Điều/Khoản/Điểm and document identity."""
    if not query or not query.strip():
        return None
    normalized = normalize_text(query)
    ref = LegalReference()

    # Document numbers contain "/", which normalize_text folds to spaces.
    raw_normalized = unicodedata.normalize("NFC", query).casefold()
    number_match = re.search(
        r"\b(\d{1,3})\s*/\s*(\d{4})\s*/\s*([a-z0-9][a-z0-9\-]*)",
        raw_normalized,
    )
    if number_match:
        ref.document_number = normalize_document_number(
            f"{number_match.group(1)}/{number_match.group(2)}/{number_match.group(3)}"
        )
        ref.document_year = int(number_match.group(2))

    patterns = (
        (r"\bdiem\s+([a-z])\s+khoan\s+(\d+)\s+dieu\s+(\d+[a-z]?)\b", "point"),
        (r"\bkhoan\s+(\d+)\s+dieu\s+(\d+[a-z]?)\b", "clause"),
        (r"\bdieu\s+(?:so\s+|thu\s+)?(\d+[a-z]?)\b", "article"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        ref.matched_spans.append(match.span())
        if kind == "point":
            ref.point = match.group(1)
            ref.clause_number = match.group(2)
            ref.article_number = match.group(3)
        elif kind == "clause":
            ref.clause_number = match.group(1)
            ref.article_number = match.group(2)
        else:
            ref.article_number = match.group(1)
        break

    if not ref.document_year:
        year_match = re.search(r"\b(19|20)\d{2}\b", normalized)
        if year_match:
            ref.document_year = int(year_match.group(0))
            ref.matched_spans.append(year_match.span())

    residual = list(normalized)
    for start, end in ref.matched_spans:
        for index in range(start, end):
            residual[index] = " "
    residual_text = _QUERY_PHRASE_RE.sub(" ", "".join(residual))
    hint_tokens = [
        token
        for token in residual_text.split()
        if token not in _QUERY_STOPWORDS and not token.isdigit()
    ]
    ref.document_hint = " ".join(hint_tokens)

    if not ref.article_number:
        return None
    return ref


def is_pure_reference(
    query: str,
    ref: LegalReference,
    document: dict | None = None,
) -> bool:
    """True when only the citation and document identity remain.

    "hãy trích dẫn điều 19 bộ luật tố tụng dân sự" is pure; "điều 19 quy
    định gì về hiệu lực bản án" is mixed and still needs semantic retrieval.
    Only the resolved document's aliases are treated as identity words —
    not every word in the document hint.
    """
    normalized = normalize_text(query)
    residual = list(normalized)
    spans = list(ref.matched_spans)
    if ref.document_number:
        for token in normalize_text(ref.document_number).split():
            if token.isdigit():
                continue
            for match in re.finditer(rf"\b{re.escape(token)}\b", normalized):
                spans.append(match.span())
    for match in _QUERY_PHRASE_RE.finditer(normalized):
        spans.append(match.span())
    if document:
        aliases = document.get("aliases") or document_aliases(
            document.get("title"),
            document.get("filename"),
            document.get("document_number"),
        )
        for alias in aliases:
            alias_text = normalize_text(alias)
            if len(alias_text) < 3:
                continue
            for match in re.finditer(
                rf"\b{re.escape(alias_text)}\b", normalized
            ):
                spans.append(match.span())
    for start, end in spans:
        for index in range(start, end):
            residual[index] = " "
    tokens = [
        token
        for token in "".join(residual).split()
        if token not in _QUERY_STOPWORDS and not token.isdigit()
    ]
    return not tokens


def resolve_document_from_context(
    documents: list[dict],
    conversation_context: list[dict] | None,
    lookback: int = 6,
) -> dict | None:
    """Resolve a bare article follow-up from recent user turns.

    "Điều 19" after "Điều 19 Bộ luật hình sự" should stay on the Penal
    Code. Only user messages are considered, and only a unique match is
    accepted; assistant answers often mention several documents.
    """
    if not documents or not conversation_context:
        return None
    for message in reversed(conversation_context[-lookback:]):
        if message.get("role") != "user":
            continue
        text = normalize_text(message.get("content", ""))
        if not text:
            continue
        scored: list[tuple[int, dict]] = []
        for doc in documents:
            aliases = doc.get("aliases") or document_aliases(
                doc.get("title"),
                doc.get("filename"),
                doc.get("document_number"),
            )
            score = 0
            for alias in aliases:
                alias_text = normalize_text(alias)
                if len(alias_text) >= 4 and alias_text in text:
                    score = max(score, len(alias_text))
            if score:
                scored.append((score, doc))
        if not scored:
            continue
        top_score = max(score for score, _ in scored)
        winners = [doc for score, doc in scored if score == top_score]
        return winners[0] if len(winners) == 1 else None
    return None


def has_explicit_document_hint(ref: LegalReference) -> bool:
    """True when the query appears to name a legal document.

    "Điều 19 quy định gì" is a semantic question about an article; "Điều 19
    bộ luật dân sự" names a document that may not exist in the KB. The
    distinction prevents silently answering from a different law.
    """
    if ref.document_number:
        return True
    return bool(
        re.search(
            r"\b(?:bo\s+luat|luat|nghi\s+quyet|nghi\s+dinh|thong\s+tu|"
            r"blhs|blttds|bltths|blds)\b",
            ref.document_hint,
        )
    )


def document_metadata_payload(documents: list[dict]) -> list[dict]:
    """Normalise repository rows into the shape the resolver expects."""
    result = []
    for doc in documents:
        item = dict(doc)
        item["aliases"] = document_aliases(
            item.get("title"),
            item.get("filename"),
            item.get("document_number"),
        )
        result.append(item)
    return result


def resolve_document(
    ref: LegalReference,
    documents: list[dict],
) -> tuple[dict | None, list[dict]]:
    """Resolve the referenced document. Returns (document, ambiguous candidates).

    Semantic scores never participate in this decision.
    """
    if not documents:
        return None, []

    if ref.document_number:
        wanted = normalize_document_number(ref.document_number)
        matches = [
            doc
            for doc in documents
            if normalize_document_number(doc.get("document_number")) == wanted
        ]
        if len(matches) == 1:
            return matches[0], []
        if len(matches) > 1:
            return None, matches

    hint = normalize_text(ref.document_hint)
    if not hint:
        return None, []

    scored: list[tuple[int, dict]] = []
    for doc in documents:
        aliases = doc.get("aliases") or document_aliases(
            doc.get("title"), doc.get("filename"), doc.get("document_number")
        )
        score = 0
        for alias in aliases:
            alias_text = normalize_text(alias)
            if len(alias_text) >= 3 and alias_text in hint:
                score = max(score, len(alias_text))
        if score and ref.document_year and doc.get("document_year") == ref.document_year:
            score += 10
        if score:
            scored.append((score, doc))

    if not scored:
        return None, []
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score = scored[0][0]
    winners = [doc for score, doc in scored if score == top_score]
    if len(winners) == 1:
        return winners[0], []
    return None, winners
