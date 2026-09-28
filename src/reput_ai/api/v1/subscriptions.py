import json
import logging
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.api.deps import get_current_user
from reput_ai.db.models.subscription import Subscription
from reput_ai.db.models.user import User
from reput_ai.db.session import get_async_db
from reput_ai.schemas.subscription import SubscriptionCheckout, SubscriptionRead
from reput_ai.services.billing import (
    BillingConfigurationError,
    BillingProviderError,
    BillingService,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
logger = logging.getLogger("reput_ai.api.subscriptions")


@router.get("/current", response_model=SubscriptionRead)
async def get_current_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Subscription:
    """Current subscription; the 14-day trial is started on first access."""
    billing = BillingService()
    return await billing.ensure_trial_subscription(db, current_user.id)


@router.post("/checkout", response_model=SubscriptionCheckout)
async def create_checkout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionCheckout:
    """Create a YooKassa payment (card is saved for recurring auto-renewal)."""
    billing = BillingService()
    try:
        payment = await billing.create_checkout_payment(user=current_user)
    except BillingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except BillingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return SubscriptionCheckout(
        payment_id=payment.payment_id,
        confirmation_url=payment.confirmation_url,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
    )


@router.post("/cancel", response_model=SubscriptionRead)
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Subscription:
    """Cancel the subscription (TRIAL/ACTIVE/PAST_DUE -> CANCELED)."""
    billing = BillingService()
    subscription = await billing.get_subscription(db, current_user.id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    await billing.cancel(db, subscription)
    await db.commit()
    await db.refresh(subscription)
    return subscription


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def billing_webhook(
    request: Request,
    signature: str | None = Header(None, alias="X-Signature"),
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str | None]:
    """YooKassa / T-Bank payment notification.

    The signature is validated against the *raw* body (mandatory): notifications
    without a valid HMAC ``X-Signature`` or T-Bank ``Token`` are rejected with 401 so
    that nobody can flip subscription states from the outside.
    """
    payload_body = await request.body()
    try:
        payload = json.loads(payload_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload"
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload")

    billing = BillingService()
    token = payload.get("Token")
    signature_header = signature
    if not signature_header and authorization:
        signature_header = authorization.removeprefix("Bearer ").strip()

    if not billing.verify_request(
        payload_body,
        payload,
        signature_header,
        token if isinstance(token, str) else None,
    ):
        logger.warning("Rejected billing webhook: invalid or missing signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
        )

    subscription = await billing.apply_payment_event(db, payload)
    return {
        "status": "ok",
        "subscription_status": subscription.status.value if subscription else None,
    }
