"""Conversational intent routing."""

from app.agentic_rag.intents import Intent, detect_intent, direct_answer


def test_greeting_is_smalltalk():
    assert detect_intent("Xin chào").intent is Intent.SMALLTALK
    assert detect_intent("hello").intent is Intent.SMALLTALK


def test_capability_question_is_meta():
    assert detect_intent("Bạn có thể làm gì?").intent is Intent.META


def test_document_list_command():
    assert detect_intent("Liệt kê tài liệu đang có").intent is Intent.DOCUMENT_LIST


def test_weather_out_of_scope():
    assert detect_intent("Thời tiết hôm nay thế nào?").intent is Intent.OUT_OF_SCOPE


def test_document_question_stays_qa():
    assert detect_intent("Điều 19 Bộ luật tố tụng dân sự là gì?").intent is Intent.DOCUMENT_QA


def test_direct_answers_are_non_empty():
    for intent in (
        Intent.SMALLTALK,
        Intent.META,
        Intent.OUT_OF_SCOPE,
    ):
        assert direct_answer(intent, [])
    assert "2 tài liệu" in direct_answer(
        Intent.DOCUMENT_LIST,
        [{"title": "A"}, {"filename": "b.pdf"}],
    )
