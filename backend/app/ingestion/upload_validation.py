"""Upload validation — security boundary before anything touches storage.

Quality assessment (4.4) judges extraction fidelity; this module judges
whether the file is allowed in at all. Parsing never happens in the request
handler — it runs in the worker with resource limits.
"""

from dataclasses import dataclass

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_PDF_PAGES = 500
MAX_TXT_BYTES = 5 * 1024 * 1024

ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",  # browsers sometimes send this for .txt/.docx
}
ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "markdown", "docx"}


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    file_type: str
    content_type: str


def validate_upload(data: bytes, filename: str, content_type: str | None) -> ValidatedUpload:
    """Reject oversized, mis-typed or malformed uploads before S3."""
    if not filename or "." not in filename:
        raise UploadValidationError("Filename must have an extension", 400)

    file_type = filename.rsplit(".", 1)[-1].lower()
    if file_type == "doc":
        raise UploadValidationError(
            "Legacy .doc is not supported — save the file as .docx", 415
        )
    if file_type not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            f"Unsupported file type '{file_type}' — allowed: {sorted(ALLOWED_EXTENSIONS)}",
            415,
        )

    if not data:
        raise UploadValidationError("Empty file", 400)

    mime = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise UploadValidationError(f"Unsupported content type '{mime}'", 415)

    if file_type == "pdf":
        # Magic bytes — a renamed file must not reach the PDF parser
        if not data.startswith(b"%PDF-"):
            raise UploadValidationError(
                "File content does not match PDF format", 415
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadValidationError(
                f"PDF exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit", 413
            )
        pages = _estimate_pdf_pages(data)
        if pages is not None and pages > MAX_PDF_PAGES:
            raise UploadValidationError(
                f"PDF has {pages} pages (limit {MAX_PDF_PAGES})", 413
            )
    elif file_type == "docx":
        # .docx is a ZIP container — refuse renamed non-ZIP files
        if not data.startswith(b"PK\x03\x04"):
            raise UploadValidationError(
                "File content does not match DOCX format", 415
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadValidationError(
                f"DOCX exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit", 413
            )
    else:
        if len(data) > MAX_TXT_BYTES:
            raise UploadValidationError("Text file too large", 413)
        # Text files must not be binary blobs with a .txt name
        if b"\x00" in data[:8192]:
            raise UploadValidationError("File is not valid text", 415)

    return ValidatedUpload(filename=filename, file_type=file_type, content_type=mime)


def _estimate_pdf_pages(data: bytes) -> int | None:
    """Cheap page-count estimate from the trailer — no parsing involved.

    Falls back to None (skip the check) if the count can't be trusted;
    the worker enforces the real limit after parsing.
    """
    # Counting "/Type /Page" occurrences is approximate but good enough to
    # reject pathological files before they reach PyMuPDF.
    return data.count(b"/Type /Page") or data.count(b"/Type/Page") or None
