"""QR code / short-link feedback funnel (negative feedback interceptor).

4-5 star customers are forwarded to the public review boards (Yandex Maps, 2GIS,
Google, Avito) so that positive reviews are published publicly, while 1-3 star
feedback stays inside the internal funnel and is delivered straight to management
via Telegram.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from reput_ai.config import settings
from reput_ai.db.models.branch import CompanyBranch

logger = logging.getLogger("reput_ai.services.funnel")

PUBLIC_RATING_THRESHOLD = 4
FunnelMode = Literal["public", "internal"]


def encode_branch_code(branch_id: UUID) -> str:
    """Encode a branch id into a compact URL-safe short-link code."""
    return base64.urlsafe_b64encode(branch_id.bytes).decode("ascii").rstrip("=")


def decode_branch_code(code: str) -> UUID | None:
    """Decode a short-link code back into the branch id (``None`` for invalid codes)."""
    try:
        padded = code + "=" * (-len(code) % 4)
        return UUID(bytes=base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError):
        return None


def build_short_link(branch_id: UUID) -> str:
    """Public short link that is embedded into the printed QR code."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{settings.API_V1_STR}/funnel/s/{encode_branch_code(branch_id)}"


def build_feedback_url(branch_id: UUID) -> str:
    """Internal form URL used for negative ratings."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return (
        f"{base}{settings.FEEDBACK_FORM_PATH}"
        f"?branch_id={branch_id}&ref={encode_branch_code(branch_id)}"
    )


@dataclass(frozen=True)
class FunnelRoute:
    """Where a customer should be sent after choosing a rating."""

    mode: FunnelMode
    redirect_url: str
    public_url: str
    feedback_url: str


def route_feedback(branch: CompanyBranch, rating: int | None = None) -> FunnelRoute:
    """Route a rating to the public boards (>= 4 stars) or to the internal form (1-3)."""
    public_url = branch.platform_url
    feedback_url = build_feedback_url(branch.id)

    if rating is not None and rating >= PUBLIC_RATING_THRESHOLD:
        return FunnelRoute(
            mode="public",
            redirect_url=public_url,
            public_url=public_url,
            feedback_url=feedback_url,
        )

    return FunnelRoute(
        mode="internal",
        redirect_url=feedback_url,
        public_url=public_url,
        feedback_url=feedback_url,
    )
