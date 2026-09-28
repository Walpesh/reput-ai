"""UI option lists sourced directly from the backend enums (single source of truth).

The frontend must never invent values: every option exposed by the dashboard comes
from the existing SQLAlchemy enums that back the FastAPI schemas, so the UI can
only ever offer values the API actually accepts.
"""

from __future__ import annotations

from reput_ai.db.models.branch import PlatformType, ToneOfVoice
from reput_ai.db.models.review import ReviewStatus
from reput_ai.db.models.subscription import SubscriptionStatus

PLATFORM_TYPES: list[str] = [item.value for item in PlatformType]
TONE_OF_VOICE_OPTIONS: list[str] = [item.value for item in ToneOfVoice]
REVIEW_STATUSES: list[str] = [item.value for item in ReviewStatus]
SUBSCRIPTION_STATUSES: list[str] = [item.value for item in SubscriptionStatus]
