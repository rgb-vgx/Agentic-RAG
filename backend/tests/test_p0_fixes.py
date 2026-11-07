"""Regression tests for P0 fixes from the production review.

Covers:
  1. Bounded agent loop — iteration_count increments once per evaluation
     round and plan_node must not reset it (previously: infinite loop).
  2. Server-side max_iterations clamp — model output can't extend the loop.
  3. Upload validation — size, magic bytes, MIME before storage/parsing.
"""

from app.agentic_rag.graph.nodes import should_continue
from app.agentic_rag.planning import route_path
from app.config import settings
from app.domain.agents.state import QueryAnalysis
from app.domain.retrieval.types import EvidenceStatus
from app.ingestion.upload_validation import (
    MAX_UPLOAD_BYTES,
    UploadValidationError,
    validate_upload,
)

# ---------------------------------------------------------------------------
# 1. Loop boundedness
# ---------------------------------------------------------------------------


def _state(status: str, iteration: int, plan_max: int = 3) -> dict:
    return {
        "evidence_status": status,
        "iteration_count": iteration,
        "retrieval_plan": {"max_iterations": plan_max},
    }


def test_insufficient_still_bounded_at_max_iterations():
    # Regression: iteration_count used to be reset by plan_node, so this
    # always returned "plan" → infinite loop when the evaluator kept
    # reporting INSUFFICIENT.
    assert should_continue(
        _state(EvidenceStatus.INSUFFICIENT.value, iteration=3, plan_max=3)
    ) == "synthesize"


def test_insufficient_below_max_replans():
    assert should_continue(
        _state(EvidenceStatus.INSUFFICIENT.value, iteration=1, plan_max=3)
    ) == "plan"


def test_model_supplied_max_iterations_is_clamped():
    # A plan asking for 999 rounds must not override the server config
    assert settings.agent_max_retrieval_iterations <= 10
    decision = should_continue(
        _state(
            EvidenceStatus.INSUFFICIENT.value,
            iteration=settings.agent_max_retrieval_iterations,
            plan_max=999,
        )
    )
    assert decision == "synthesize"


def test_malformed_plan_max_falls_back_to_default():
    decision = should_continue(
        {
            "evidence_status": EvidenceStatus.INSUFFICIENT.value,
            "iteration_count": 0,
            "retrieval_plan": {"max_iterations": "not-a-number"},
        }
    )
    assert decision == "plan"  # no crash, default cap applies


def test_sufficient_and_conflicting_go_straight_to_synthesis():
    for status in (
        EvidenceStatus.SUFFICIENT.value,
        EvidenceStatus.CONFLICTING.value,
        EvidenceStatus.PARTIALLY_SUFFICIENT.value,
    ):
        assert should_continue(_state(status, iteration=0)) == "synthesize"


# ---------------------------------------------------------------------------
# 3. Upload validation
# ---------------------------------------------------------------------------

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<</Type /Page>>\nendobj\n%%EOF"
TXT_BYTES = b"Ordinary text file content."


def test_valid_pdf_accepted():
    result = validate_upload(PDF_BYTES, "policy.pdf", "application/pdf")
    assert result.file_type == "pdf"


def test_renamed_non_pdf_rejected_by_magic_bytes():
    try:
        validate_upload(b"GIF89a not a pdf", "fake.pdf", "application/pdf")
        raise AssertionError("should have raised")
    except UploadValidationError as exc:
        assert exc.status_code == 415


def test_oversized_pdf_rejected():
    big = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES + 1)
    try:
        validate_upload(big, "big.pdf", "application/pdf")
        raise AssertionError("should have raised")
    except UploadValidationError as exc:
        assert exc.status_code == 413


def test_disallowed_extension_rejected():
    try:
        validate_upload(b"anything", "malware.exe", "application/octet-stream")
        raise AssertionError("should have raised")
    except UploadValidationError as exc:
        assert exc.status_code == 415


def test_disallowed_mime_rejected():
    try:
        validate_upload(PDF_BYTES, "policy.pdf", "text/html")
        raise AssertionError("should have raised")
    except UploadValidationError as exc:
        assert exc.status_code == 415


def test_binary_content_with_txt_name_rejected():
    try:
        validate_upload(b"\x00\x01\x02\x00binary", "notes.txt", "text/plain")
        raise AssertionError("should have raised")
    except UploadValidationError as exc:
        assert exc.status_code == 415


def test_valid_txt_accepted():
    result = validate_upload(TXT_BYTES, "notes.txt", "text/plain")
    assert result.file_type == "txt"


def test_routing_still_intact_after_planning_change():
    assert route_path(QueryAnalysis(query_type="simple_fact", complexity="low")) == "fast"
