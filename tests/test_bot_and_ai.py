import pytest
from reput_ai.llm.sanitizer import sanitize_review_text, format_user_review_payload
from reput_ai.bot.keyboards.inline import get_review_approval_keyboard
import uuid


def test_sanitize_review_text():
    # Length bound test (1500 chars limit)
    long_text = "a" * 2000
    sanitized = sanitize_review_text(long_text)
    assert len(sanitized) == 1500

    # Strip control characters & escape brackets
    malicious = "Hello <script>alert('xss')</script>\x00\x08World"
    cleaned = sanitize_review_text(malicious)
    assert "<script>" not in cleaned
    assert "&lt;script&gt;" in cleaned
    assert "\x00" not in cleaned
    assert "\x08" not in cleaned


def test_format_user_review_payload():
    text = "Great service!"
    payload = format_user_review_payload(text)
    assert "<user_review>" in payload
    assert "</user_review>" in payload
    assert "Great service!" in payload


def test_inline_keyboard_generation():
    test_id = uuid.uuid4()
    keyboard = get_review_approval_keyboard(test_id)
    assert len(keyboard.inline_keyboard) == 1
    buttons = keyboard.inline_keyboard[0]
    assert len(buttons) == 3
    assert f"review:approve:{test_id}" == buttons[0].callback_data
    assert f"review:edit:{test_id}" == buttons[1].callback_data
    assert f"review:regenerate:{test_id}" == buttons[2].callback_data
