import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.api.deps import get_current_user
from reput_ai.db.models.subscription import Subscription, SubscriptionStatus
from reput_ai.db.models.user import User
from reput_ai.db.session import get_async_db
from reput_ai.schemas.subscription import SubscriptionRead
from reput_ai.services.billing import BillingService

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/current", response_model=SubscriptionRead | None)
async def get_current_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Subscription | None:
    stmt = select(Subscription).where(Subscription.user_id == current_user.id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def billing_webhook(
    request: Request,
    signature: str | None = Header(None, alias="X-Signature"),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    payload_body = await request.body()
    billing_service = BillingService()
    if signature and not billing_service.verify_webhook_signature(payload_body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_data = await request.json()
    await billing_service.handle_payment_event(event_data)
    return {"status": "ok"}
