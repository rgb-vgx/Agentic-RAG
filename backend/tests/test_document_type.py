"""Document-type detection and parser selection."""

import fitz
import pytest

from app.ingestion.docling_parser import _markdown_table_rows
from app.ingestion.document_type import detect_document_type
from app.ingestion.parsers import get_parser


def _pdf_bytes(lines: list[str], toc: list[list] | None = None) -> bytes:
    document = fitz.open()
    page = document.new_page()
    y = 72
    fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    for line in lines:
        page.insert_text((72, y), line, fontfile=fontfile)
        y += 16
    if toc:
        document.set_toc(toc)
    return document.tobytes()


def test_detect_scientific_pdf_from_outline_and_abstract():
    data = _pdf_bytes(
        ["Abstract. We study long contexts.", "arXiv:2611.05306v1"],
        toc=[[1, "Introduction", 1], [1, "Method", 1], [1, "Conclusion", 1]],
    )
    assert detect_document_type("pdf", data, "paper.pdf") == "scientific"


def test_detect_legal_pdf():
    data = _pdf_bytes(
        [
            "QUOC HOI",
            "BO LUAT HINH SU",
            "Dieu 1. Pham vi dieu chinh",
            "Dieu 2. Co so cua trach nhiem hinh su",
        ]
    )
    assert detect_document_type("pdf", data, "bo-luat.pdf") == "legal"


def test_parser_selection_falls_back_without_docling():
    parser = get_parser("pdf", document_type="scientific")
    assert parser.name in {"docling", "pymupdf"}


def test_markdown_table_rows_ignores_separator():
    rows = _markdown_table_rows(
        "| A | B |\n|---|---|\n| 1 | 2 |\n"
    )
    assert rows == [["A", "B"], ["1", "2"]]


@pytest.mark.parametrize(
    ("file_type", "text", "expected"),
    [
        ("txt", "BỘ LUẬT HÌNH SỰ\nĐiều 1. Phạm vi", "legal"),
        ("txt", "Abstract\nReferences\n1. Introduction", "scientific"),
        ("txt", "hello world", "generic"),
    ],
)
def test_detect_text_documents(file_type, text, expected):
    assert detect_document_type(file_type, text.encode(), "sample.txt") == expected
