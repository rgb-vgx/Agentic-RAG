"""Download headers — UTF-8 filenames must survive RFC 6266 encoding."""

from app.api.v1.documents import content_disposition


def test_ascii_filename():
    header = content_disposition("policy.pdf")
    assert header == 'attachment; filename="policy.pdf"; filename*=UTF-8\'\'policy.pdf'


def test_vietnamese_filename_encoded_and_ascii_fallback():
    header = content_disposition("Nghị-quyết-45-2026-NQ-CP.docx")
    assert header.isascii()
    assert "filename*=UTF-8''Ngh%E1%BB%8B-quy%E1%BA%BFt-45-2026-NQ-CP.docx" in header
    # Diacritics dropped from the fallback so legacy clients still get a name
    assert 'filename="Ngh-quyt-45-2026-NQ-CP.docx"' in header


def test_empty_filename_falls_back():
    assert 'filename="document"' in content_disposition("")
