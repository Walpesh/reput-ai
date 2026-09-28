from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from reput_ai.db.models.subscription import SubscriptionStatus


class SubscriptionBase(BaseModel):
    status: SubscriptionStatus
    trial_ends_at: datetime | None = None
    paid_until: datetime | None = None


class SubscriptionRead(SubscriptionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    payment_provider_id: str | None = None


class SubscriptionCheckout(BaseModel):
    """Payment created by YooKassa for the checkout redirect."""

    payment_id: str
    confirmation_url: str
    amount: Decimal
    currency: str
    status: str
