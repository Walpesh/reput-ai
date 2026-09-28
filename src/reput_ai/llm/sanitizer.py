import re
import html

MAX_REVIEW_LENGTH = 1500


def sanitize_review_text(raw_text: str) -> str:
    """Sanitize review text according to technical specification:

    1. Truncate user review text to maximum 1500 characters.
    2. Strip raw control characters and escape XML/HTML brackets (<, >).
    3. Return sanitized text safe for insertion into LLM prompt templates.
    """
    if not raw_text:
        return ""

    # Step 1: Truncate to maximum 1500 characters
    truncated = raw_text[:MAX_REVIEW_LENGTH]

    # Step 2: Strip raw control characters (except newline, carriage return, tab)
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", truncated)

    # Escape HTML/XML brackets
    escaped = html.escape(cleaned, quote=False)

    return escaped


def format_user_review_payload(review_text: str) -> str:
    """Wrap sanitized review inside <user_review> XML tags."""
    sanitized = sanitize_review_text(review_text)
    return f"<user_review>\n{sanitized}\n</user_review>"
