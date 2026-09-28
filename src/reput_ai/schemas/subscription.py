from datetime import datetime
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
