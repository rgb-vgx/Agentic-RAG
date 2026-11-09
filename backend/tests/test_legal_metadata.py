"""Deterministic legal reference parsing and document resolution."""

import pytest

from app.ingestion.legal_metadata import (
    document_aliases,
    extract_article,
    has_explicit_document_hint,
    is_pure_reference,
    parse_legal_reference,
    resolve_document,
    resolve_document_from_context,
)


def _documents() -> list[dict]:
    docs = [
        {
            "id": "hinh-su",
            "filename": "Bộ-luật-100-2015-QH13.docx",
            "title": "Bộ luật hình sự",
            "document_number": "100/2015/QH13",
            "document_year": 2015,
        },
        {
            "id": "to-tung-dan-su",
            "filename": "92_2015_QH13_296861.docx",
            "title": "Bộ luật tố tụng dân sự",
            "document_number": "92/2015/QH13",
            "document_year": 2015,
        },
    ]
    for doc in docs:
        doc["aliases"] = document_aliases(
            doc["title"], doc["filename"], doc["document_number"]
        )
    return docs


@pytest.mark.parametrize(
    ("query", "article", "clause", "point"),
    [
        ("Điều 19", "19", None, None),
        ("điều 19a", "19a", None, None),
        ("khoản 2 điều 19", "19", "2", None),
        ("điểm a khoản 1 điều 19", "19", "1", "a"),
        ("Điều số 19 bộ luật tố tụng dân sự", "19", None, None),
    ],
)
def test_parse_structural_reference(query, article, clause, point):
    ref = parse_legal_reference(query)
    assert ref is not None
    assert ref.article_number == article
    assert ref.clause_number == clause
    assert ref.point == point


def test_parse_document_number_and_year():
    ref = parse_legal_reference("Điều 19 92/2015/QH13")
    assert ref is not None
    assert ref.document_number == "92/2015/QH13"
    assert ref.document_year == 2015


@pytest.mark.parametrize(
    ("query", "expected_id"),
    [
        ("Điều 19 bộ luật tố tụng dân sự", "to-tung-dan-su"),
        ("Điều 19 bộ luật hình sự", "hinh-su"),
        ("Điều 19 BLTTDS", "to-tung-dan-su"),
        ("Điều 19 BLHS 2015", "hinh-su"),
        ("Điều 19 92/2015/QH13", "to-tung-dan-su"),
    ],
)
def test_resolve_document(query, expected_id):
    ref = parse_legal_reference(query)
    assert ref is not None
    document, ambiguous = resolve_document(ref, _documents())
    assert not ambiguous
    assert document is not None
    assert document["id"] == expected_id


@pytest.mark.parametrize(
    ("query", "explicit"),
    [
        ("Điều 19 bộ luật dân sự", True),
        ("Điều 19 92/2015/QH13", True),
        ("Điều 19 quy định gì về hiệu lực bản án", False),
    ],
)
def test_has_explicit_document_hint(query, explicit):
    ref = parse_legal_reference(query)
    assert ref is not None
    assert has_explicit_document_hint(ref) is explicit


def test_resolve_document_from_recent_user_turn():
    context = [
        {"role": "user", "content": "Điều 19 bộ luật hình sự"},
        {"role": "assistant", "content": "Điều 19 quy định về không tố giác tội phạm"},
    ]
    document = resolve_document_from_context(_documents(), context)
    assert document is not None
    assert document["id"] == "hinh-su"


def test_resolve_document_from_context_ignores_assistant_only_mentions():
    context = [
        {"role": "assistant", "content": "Có thể xem Bộ luật hình sự hoặc Bộ luật tố tụng dân sự"},
    ]
    assert resolve_document_from_context(_documents(), context) is None


def test_resolve_document_is_ambiguous_without_identity():
    ref = parse_legal_reference("Điều 19")
    assert ref is not None
    document, ambiguous = resolve_document(ref, _documents())
    assert document is None
    assert ambiguous == []


@pytest.mark.parametrize(
    ("query", "pure"),
    [
        ("hãy trích dẫn điều 19 bộ luật tố tụng dân sự 2015", True),
        ("Điều 19 của bộ luật tố tụng dân sự", True),
        ("Điều 19 bộ luật tố tụng dân sự quy định gì về hiệu lực bản án", False),
        ("Điều 19 quy định gì về hiệu lực bản án", False),
    ],
)
def test_is_pure_reference(query, pure):
    ref = parse_legal_reference(query)
    assert ref is not None
    document, _ = resolve_document(ref, _documents())
    assert is_pure_reference(query, ref, document) is pure


@pytest.mark.parametrize(
    ("heading_path", "number", "title"),
    [
        (
            ["Chương II", "Điều 19. Bảo đảm hiệu lực của bản án, quyết định của Tòa án"],
            "19",
            "Bảo đảm hiệu lực của bản án, quyết định của Tòa án",
        ),
        (["Chương I"], None, None),
        (["Chương II", "Điều 19a. Điều khoản bổ sung"], "19a", "Điều khoản bổ sung"),
    ],
)
def test_extract_article_from_heading(heading_path, number, title):
    assert extract_article(heading_path) == (number, title)
