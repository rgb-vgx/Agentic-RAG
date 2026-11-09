"""Cheap conversational intent routing.

Document questions keep the full RAG graph. Greetings, capability questions,
out-of-scope questions and document-list commands are answered directly so
they never pay retrieval/rerank cost or receive a false "insufficient
evidence" answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class Intent(StrEnum):
    DOCUMENT_QA = "document_qa"
    SMALLTALK = "smalltalk"
    META = "meta"
    OUT_OF_SCOPE = "out_of_scope"
    DOCUMENT_LIST = "document_list"


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    reason: str = ""


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("đ", "d").replace("Đ", "D").lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_SMALLTALK = {
    "xin chao",
    "chao",
    "chao ban",
    "hello",
    "hi",
    "hey",
    "cam on",
    "thanks",
    "thank you",
    "tam biet",
    "bye",
    "goodbye",
    "ok",
    "oke",
    "duoc",
}
_META_PATTERNS = (
    "ban la ai",
    "ban co the lam gi",
    "ban lam duoc gi",
    "huong dan",
    "tro giup",
    "help",
    "chuc nang",
    "co the giup gi",
)
_OUT_OF_SCOPE_PATTERNS = (
    "thoi tiet",
    "tin tuc",
    "gia vang",
    "ty gia",
    "bong da",
    "ke chuyen",
    "boi",
    "dich sang",
    "gio hom nay",
    "ngay mai the nao",
)
_DOCUMENT_LIST_PATTERNS = (
    "liet ke tai lieu",
    "danh sach tai lieu",
    "co bao nhieu tai lieu",
    "tai lieu nao",
    "dang co tai lieu",
    "xem tai lieu",
    "kho tai lieu",
)


def detect_intent(query: str) -> IntentResult:
    folded = _fold(query)
    if not folded:
        return IntentResult(Intent.DOCUMENT_QA)
    if folded in _SMALLTALK or (
        len(folded.split()) <= 3
        and any(folded.startswith(prefix) for prefix in ("xin chao", "chao ", "hello", "hi "))
    ):
        return IntentResult(Intent.SMALLTALK, "greeting")
    if any(pattern in folded for pattern in _META_PATTERNS):
        return IntentResult(Intent.META, "capability/help")
    if any(pattern in folded for pattern in _DOCUMENT_LIST_PATTERNS):
        return IntentResult(Intent.DOCUMENT_LIST, "document inventory")
    if any(pattern in folded for pattern in _OUT_OF_SCOPE_PATTERNS):
        return IntentResult(Intent.OUT_OF_SCOPE, "outside document scope")
    return IntentResult(Intent.DOCUMENT_QA)


def direct_answer(intent: Intent, documents: list[dict]) -> str:
    if intent is Intent.SMALLTALK:
        return (
            "Xin chào! Mình là trợ lý tài liệu Agentic RAG. Bạn có thể hỏi về "
            "nội dung tài liệu, trích dẫn điều khoản, hoặc yêu cầu tóm tắt."
        )
    if intent is Intent.META:
        return (
            "Mình trả lời câu hỏi dựa trên tài liệu đã index, trích dẫn đúng "
            "điều khoản/trang, tóm tắt và so sánh nội dung. Bạn có thể bật Web "
            "để tìm thông tin bên ngoài, hoặc hỏi mình danh sách tài liệu hiện có."
        )
    if intent is Intent.OUT_OF_SCOPE:
        return (
            "Câu này nằm ngoài phạm vi tài liệu hiện có. Mình chỉ trả lời dựa "
            "trên kho tài liệu; nếu muốn tìm thông tin bên ngoài, hãy bật nút Web."
        )
    if intent is Intent.DOCUMENT_LIST:
        if not documents:
            return "Hiện chưa có tài liệu nào đã index trong kho của bạn."
        names = [
            str(item.get("title") or item.get("filename") or item.get("id"))
            for item in documents[:10]
        ]
        lines = [f"Kho hiện có {len(documents)} tài liệu:"]
        lines.extend(f"- {name}" for name in names)
        if len(documents) > 10:
            lines.append(f"- … và {len(documents) - 10} tài liệu khác")
        return "\n".join(lines)
    return ""
