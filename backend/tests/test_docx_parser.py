"""DOCX parsing and upload validation."""

import io

import pytest

from app.ingestion.ir import BlockType
from app.ingestion.parsers import DocxParser, get_parser
from app.ingestion.upload_validation import UploadValidationError, validate_upload

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def make_docx() -> bytes:
    from docx import Document as DocxDocument

    document = DocxDocument()
    document.add_heading("Chính sách nghỉ phép", level=1)
    document.add_paragraph("Nhân viên chính thức được nghỉ 18 ngày phép năm.")
    document.add_heading("Nghỉ ốm", level=2)
    document.add_paragraph("Nghỉ ốm tối đa 10 ngày mỗi năm.")
    document.add_paragraph("Điều 1. Phạm vi áp dụng")

    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Loại"
    table.cell(0, 1).text = "Số ngày"
    table.cell(1, 0).text = "Phép năm"
    table.cell(1, 1).text = "18"

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_get_parser_for_docx():
    parser = get_parser("docx")
    assert isinstance(parser, DocxParser)
    assert "docx" in parser.supported_types


def test_docx_parser_extracts_headings_paragraphs_and_tables():
    parsed = DocxParser().parse(make_docx(), "doc-1", "policy.docx")

    assert parsed.file_type == "docx"
    assert parsed.parser_name == "python-docx"

    headings = [b for b in parsed.blocks if b.block_type == BlockType.HEADING]
    assert [h.text for h in headings][:3] == [
        "Chính sách nghỉ phép",
        "Nghỉ ốm",
        "Điều 1. Phạm vi áp dụng",
    ]
    assert headings[0].metadata["heading_level"] == 1
    assert headings[1].metadata["heading_level"] == 2

    tables = [b for b in parsed.blocks if b.block_type == BlockType.TABLE]
    assert len(tables) == 1
    assert tables[0].table_data == [["Loại", "Số ngày"], ["Phép năm", "18"]]

    assert any("18 ngày phép" in b.text for b in parsed.blocks)
    assert [b.block_index for b in parsed.blocks] == list(range(len(parsed.blocks)))


def test_valid_docx_upload_accepted():
    result = validate_upload(make_docx(), "policy.docx", DOCX_MIME)
    assert result.file_type == "docx"


def test_renamed_non_docx_rejected():
    with pytest.raises(UploadValidationError):
        validate_upload(b"not a zip archive", "fake.docx", DOCX_MIME)


def test_legacy_doc_rejected_with_clear_message():
    with pytest.raises(UploadValidationError, match="save the file as .docx"):
        validate_upload(b"\xd0\xcf\x11\xe0", "legacy.doc", "application/msword")
