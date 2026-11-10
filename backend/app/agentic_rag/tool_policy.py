"""Tool approval policy (foundation for human-in-the-loop actions).

Read-only retrieval is always allowed. Any future action that mutates data or
calls an external side effect must be explicitly approved by the user; role
checks are enforced separately by the API/repository layer.
"""

from __future__ import annotations

from enum import StrEnum


class ToolRisk(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


_APPROVAL_REQUIRED = {
    "delete_document",
    "rename_document",
    "upload_document",
    "send_email",
    "create_event",
    "execute_shell",
}


def requires_approval(tool_name: str) -> bool:
    return tool_name in _APPROVAL_REQUIRED


def can_execute_without_approval(tool_name: str, risk: ToolRisk) -> bool:
    if risk is ToolRisk.READ:
        return not requires_approval(tool_name)
    return False
