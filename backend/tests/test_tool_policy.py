"""Tool approval policy."""

from app.agentic_rag.tool_policy import (
    ToolRisk,
    can_execute_without_approval,
    requires_approval,
)


def test_read_retrieval_is_allowed():
    assert can_execute_without_approval("search_documents", ToolRisk.READ) is True


def test_writes_and_external_actions_require_approval():
    assert requires_approval("delete_document") is True
    assert requires_approval("send_email") is True
    assert can_execute_without_approval("delete_document", ToolRisk.WRITE) is False
    assert can_execute_without_approval("send_email", ToolRisk.EXTERNAL) is False
