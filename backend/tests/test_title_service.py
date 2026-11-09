"""Conversation title sanitisation."""

import pytest

from app.services.title_service import sanitize_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Nhiệm vụ Bộ luật hình sự"', "Nhiệm vụ Bộ luật hình sự"),
        ("Tiêu đề: Trốn thuế và kê khai thuế.", "Trốn thuế và kê khai thuế"),
        ("```\nĐiều 19 BLTTDS\n```", "Điều 19 BLTTDS"),
        ("  Nhiệm vụ\nBộ luật  ", "Nhiệm vụ"),
        ("", ""),
    ],
)
def test_sanitize_title(raw, expected):
    assert sanitize_title(raw) == expected


def test_sanitize_title_truncates_at_word_boundary():
    title = sanitize_title("một " * 60)
    assert len(title) <= 80
    assert not title.endswith(" ")
