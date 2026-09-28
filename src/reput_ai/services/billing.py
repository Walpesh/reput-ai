"""Billing engine: YooKassa / T-Bank integration, trials, recurring subscriptions.

Lifecycle implemented here (mirrors ``SubscriptionStatus`` of the DB model)::

    TRIAL --payment--> ACTIVE --payment failure--> PAST_DUE --grace period--> CANCELED
      ^                                   |
      |                                   +-- successful payment -> ACTIVE (resumed)

Webhook signature validation is mandatory: a notification without a valid
``X-Signature`` HMAC (YooKassa) or ``Token`` (T-Bank) is rejected.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reput_ai.config import settings
from reput_ai.core.parsing import parse_iso_datetime
from reput_ai.core.time import ensure_aware, utcnow
from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.models.subscription import Subscription, SubscriptionStatus
from reput_ai.db.models.user import User

logger = logging.getLogger("reput_ai.services.billing")


class BillingConfigurationError(RuntimeError):
    """Raised when the payment provider credentials are not configured."""


class BillingProviderError(RuntimeError):
    """Raised when the payment provider answers with an error."""


class PaymentEventType(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    IGNORED = "IGNORED"


#: YooKassa notification names -> internal event types.
YOOKASSA_EVENT_TYPES: dict[str, PaymentEventType] = {
    "payment.succeeded": PaymentEventType.SUCCEEDED,
    "payment.canceled": PaymentEventType.CANCELED,
    "refund.succeeded": PaymentEventType.CANCELED,
}

#: Cancellation reasons that mean "the charge failed" (delinquency) instead of a
#: deliberate cancel/refund.
DELINQUENCY_REASONS = frozenset(
    {"insufficient_funds", "payment_timeout", "card_expired", "expired_on_confirmation", "fail"}
)

#: T-Bank acquiring statuses -> internal event types.
TBANK_STATUS_TYPES: dict[str, PaymentEventType] = {
    "CONFIRMED": PaymentEventType.SUCCEEDED,
    "AUTHORIZED": PaymentEventType.SUCCEEDED,
    "REJECTED": PaymentEventType.FAILED,
    "CANCELED": PaymentEventType.FAILED,
    "DEADLINE_EXPIRED": PaymentEventType.FAILED,
    "REFUNDED": PaymentEventType.CANCELED,
    "REVERSED": PaymentEventType.CANCELED,
    "PARTIAL_REFUNDED": PaymentEventType.CANCELED,
}


def parse_uuid(value: Any) -> UUID | None:
    """Best-effort parsing of an arbitrary metadata value into a UUID."""
    if isinstance(value, UUID):
        return value
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


def parse_amount(value: Any) -> Decimal | None:
    """Parse ``{"value": "1990.00"}`` / ``"1990.00"`` / ``1990`` into a Decimal."""
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class PaymentEvent:
    """Provider agnostic representation of a payment notification."""

    event_type: PaymentEventType
    provider: str
    payment_id: str | None = None
    user_id: UUID | None = None
    amount: Decimal | None = None
    currency: str = "RUB"
    captured_at: datetime = field(default_factory=utcnow)
    payment_method_id: str | None = None
    is_renewal: bool = False
    raw_event: str = ""


@dataclass(frozen=True)
class CheckoutPayment:
    """Payment created by the payment provider for the checkout redirect."""

    payment_id: str
    confirmation_url: str
    amount: Decimal
    currency: str
    status: str


class BillingService:
    """YooKassa / T-Bank billing engine (trials, subscriptions, webhooks, charges)."""

    def __init__(
        self,
        shop_id: str | None = None,
        secret_key: str | None = None,
        webhook_secret: str | None = None,
        tbank_password: str | None = None,
        api_url: str | None = None,
        price_rub: int | None = None,
        currency: str | None = None,
    ) -> None:
        self.shop_id = shop_id or settings.YOOKASSA_SHOP_ID
        self.secret_key = secret_key or settings.YOOKASSA_SECRET_KEY
        self.webhook_secret = webhook_secret or settings.YOOKASSA_WEBHOOK_SECRET
        self.tbank_password = tbank_password or settings.TBANK_PASSWORD
        self.api_url = (api_url or settings.YOOKASSA_API_URL).rstrip("/")
        self.price_rub = Decimal(price_rub if price_rub is not None else settings.BILLING_PRICE_RUB)
        self.currency = currency or settings.BILLING_CURRENCY

    # ------------------------------------------------------------------
    # Mandatory webhook signature validation
    # ------------------------------------------------------------------
    def verify_webhook_signature(self, payload_body: bytes, signature: str | None) -> bool:
        """Validate the ``X-Signature`` HMAC-SHA256 of a YooKassa notification.

        Validation is mandatory: a missing secret or a missing header means the
        notification cannot be trusted and must be rejected.
        """
        if not signature or not self.webhook_secret:
            return False

        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    def verify_tbank_token(self, payload: dict[str, Any], token: str | None) -> bool:
        """Validate the T-Bank acquiring ``Token`` field.

        T-Bank signs a notification with ``SHA-256`` of the concatenated root scalar
        values (sorted by key, without nested objects and the ``Token`` itself)
        followed by the terminal password.
        """
        if not token or not self.tbank_password:
            return False

        concatenated = "".join(
            str(value)
            for key, value in sorted(payload.items())
            if key != "Token" and not isinstance(value, (dict, list))
        )
        expected = hashlib.sha256(f"{concatenated}{self.tbank_password}".encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected, token)

    def verify_request(
        self,
        payload_body: bytes,
        payload: dict[str, Any],
        signature: str | None,
        token: str | None = None,
    ) -> bool:
        """Accept a notification only if either provider signature is valid."""
        return self.verify_webhook_signature(payload_body, signature) or self.verify_tbank_token(
            payload, token
        )
    # ------------------------------------------------------------------
    # Event normalization
    # ------------------------------------------------------------------
    @classmethod
    def normalize_event(cls, payload: dict[str, Any]) -> PaymentEvent:
        """Normalize a YooKassa / T-Bank notification into a :class:`PaymentEvent`."""
        if isinstance(payload.get("object"), dict) or payload.get("event"):
            return cls._normalize_yookassa(payload)
        if payload.get("Status") or payload.get("PaymentId"):
            return cls._normalize_tbank(payload)
        return PaymentEvent(event_type=PaymentEventType.IGNORED, provider="UNKNOWN")

    @classmethod
    def _normalize_yookassa(cls, payload: dict[str, Any]) -> PaymentEvent:
        event_name = str(payload.get("event") or "").strip().lower()
        obj = payload.get("object") if isinstance(payload.get("object"), dict) else payload
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        cancellation = obj.get("cancellation_details")
        reason = ""
        if isinstance(cancellation, dict):
            reason = str(cancellation.get("reason") or "").lower()
        is_renewal = str(metadata.get("renewal") or "").lower() == "true"

        event_type = YOOKASSA_EVENT_TYPES.get(event_name, PaymentEventType.IGNORED)
        if event_type is PaymentEventType.CANCELED and (is_renewal or reason in DELINQUENCY_REASONS):
            event_type = PaymentEventType.FAILED

        amount = obj.get("amount") if isinstance(obj.get("amount"), dict) else {}
        method = obj.get("payment_method") if isinstance(obj.get("payment_method"), dict) else {}
        captured_at = (
            parse_iso_datetime(obj.get("captured_at"))
            or parse_iso_datetime(obj.get("created_at"))
            or utcnow()
        )

        return PaymentEvent(
            event_type=event_type,
            provider="YOOKASSA",
            payment_id=str(obj["id"]) if obj.get("id") else None,
            user_id=parse_uuid(metadata.get("user_id")),
            amount=parse_amount(amount),
            currency=str(amount.get("currency") or settings.BILLING_CURRENCY),
            captured_at=captured_at,
            payment_method_id=str(method["id"]) if method.get("id") else None,
            is_renewal=is_renewal,
            raw_event=event_name,
        )

    @classmethod
    def _normalize_tbank(cls, payload: dict[str, Any]) -> PaymentEvent:
        status = str(payload.get("Status") or "").strip().upper()
        data = payload.get("DATA") if isinstance(payload.get("DATA"), dict) else {}
        is_renewal = str(data.get("renewal") or payload.get("renewal") or "").lower() == "true"

        amount = parse_amount(payload.get("Amount"))
        if amount is not None:
            amount = amount / 100  # T-Bank transfers the amount in kopecks.

        return PaymentEvent(
            event_type=TBANK_STATUS_TYPES.get(status, PaymentEventType.IGNORED),
            provider="TBANK",
            payment_id=str(payload["PaymentId"]) if payload.get("PaymentId") else None,
            user_id=parse_uuid(data.get("user_id")),
            amount=amount,
            currency=str(payload.get("Currency") or settings.BILLING_CURRENCY),
            captured_at=parse_iso_datetime(payload.get("SuccessAdd")) or utcnow(),
            payment_method_id=str(payload["RebillId"]) if payload.get("RebillId") else None,
            is_renewal=is_renewal,
            raw_event=status,
        )



    # ------------------------------------------------------------------
    # Subscription lifecycle: TRIAL -> ACTIVE -> PAST_DUE -> CANCELED
    # ------------------------------------------------------------------
    @staticmethod
    def is_serviceable(subscription: Subscription, now: datetime | None = None) -> bool:
        """Return ``True`` while the subscription still grants access to the service."""
        current = ensure_aware(now) or utcnow()
        if subscription.status is SubscriptionStatus.TRIAL:
            anchor = ensure_aware(subscription.trial_ends_at)
        elif subscription.status is SubscriptionStatus.ACTIVE:
            anchor = ensure_aware(subscription.paid_until)
        else:
            return False
        return anchor is None or anchor > current

    @staticmethod
    def serviceable_filter(now: datetime | None = None):
        """SQL counterpart of :meth:`is_serviceable` (TRIAL/ACTIVE and not expired)."""
        current = ensure_aware(now) or utcnow()
        return or_(
            and_(
                Subscription.status == SubscriptionStatus.TRIAL,
                or_(Subscription.trial_ends_at.is_(None), Subscription.trial_ends_at > current),
            ),
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE,
                or_(Subscription.paid_until.is_(None), Subscription.paid_until > current),
            ),
        )

    @staticmethod
    async def _set_branch_activity(session: AsyncSession, user_id: UUID, is_active: bool) -> int:
        """Enable/disable every branch of a user (auto-suspension of the service)."""
        result = await session.execute(
            update(CompanyBranch)
            .where(CompanyBranch.user_id == user_id)
            .values(is_active=is_active)
        )
        return result.rowcount or 0

    async def get_subscription(self, session: AsyncSession, user_id: UUID) -> Subscription | None:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        return result.scalars().first()

    async def ensure_trial_subscription(
        self,
        session: AsyncSession,
        user_id: UUID,
        now: datetime | None = None,
    ) -> Subscription:
        """Return the user's subscription, creating the 14-day trial on first call."""
        subscription = await self.get_subscription(session, user_id)
        if subscription is not None:
            return subscription

        current = ensure_aware(now) or utcnow()
        subscription = Subscription(
            user_id=user_id,
            status=SubscriptionStatus.TRIAL,
            trial_ends_at=current + timedelta(days=settings.TRIAL_PERIOD_DAYS),
            paid_until=None,
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)
        logger.info(
            "Trial subscription started for user %s until %s", user_id, subscription.trial_ends_at
        )
        return subscription

    async def activate(
        self,
        session: AsyncSession,
        subscription: Subscription,
        event: PaymentEvent,
        now: datetime | None = None,
    ) -> Subscription:
        """Apply a successful payment: extend ``paid_until`` and resume the service."""
        was_suspended = subscription.status is SubscriptionStatus.PAST_DUE
        current = ensure_aware(now) or utcnow()
        period_end = (ensure_aware(event.captured_at) or current) + timedelta(
            days=settings.BILLING_PERIOD_DAYS
        )

        existing_paid_until = ensure_aware(subscription.paid_until)
        if existing_paid_until is None or period_end > existing_paid_until:
            # Idempotent: repeated webhook deliveries for the same payment do not
            # extend the paid period twice.
            subscription.paid_until = period_end

        subscription.status = SubscriptionStatus.ACTIVE
        if event.payment_method_id:
            # Saved payment method id reused for recurring (auto-renewal) charges.
            subscription.payment_provider_id = event.payment_method_id
        if was_suspended:
            await self._set_branch_activity(session, subscription.user_id, True)

        logger.info(
            "Subscription %s activated until %s (payment %s)",
            subscription.id,
            subscription.paid_until,
            event.payment_id,
        )
        return subscription

    async def mark_past_due(self, session: AsyncSession, subscription: Subscription) -> Subscription:
        """Auto-suspend the service after a failed payment or an expired trial."""
        subscription.status = SubscriptionStatus.PAST_DUE
        await self._set_branch_activity(session, subscription.user_id, False)
        logger.warning("Subscription %s moved to PAST_DUE (service suspended)", subscription.id)
        return subscription

    async def cancel(self, session: AsyncSession, subscription: Subscription) -> Subscription:
        """Final state of the lifecycle: the subscription is no longer billable."""
        subscription.status = SubscriptionStatus.CANCELED
        await self._set_branch_activity(session, subscription.user_id, False)
        logger.info("Subscription %s canceled", subscription.id)
        return subscription

    # ------------------------------------------------------------------
    # Webhook processing and periodic lifecycle jobs
    # ------------------------------------------------------------------
    async def _find_subscription(
        self, session: AsyncSession, event: PaymentEvent
    ) -> Subscription | None:
        if event.user_id is not None:
            subscription = await self.get_subscription(session, event.user_id)
            if subscription is not None:
                return subscription
        if event.payment_id:
            result = await session.execute(
                select(Subscription).where(Subscription.payment_provider_id == event.payment_id)
            )
            return result.scalars().first()
        return None

    async def apply_payment_event(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> Subscription | None:
        """Apply a validated provider notification to the subscription lifecycle."""
        event = self.normalize_event(payload)
        if event.event_type is PaymentEventType.IGNORED:
            logger.info("Ignoring unsupported billing event '%s'", event.raw_event)
            return None

        subscription = await self._find_subscription(session, event)
        if subscription is None:
            logger.warning(
                "No subscription found for billing event '%s' (payment %s, user %s)",
                event.raw_event,
                event.payment_id,
                event.user_id,
            )
            return None

        if event.event_type is PaymentEventType.SUCCEEDED:
            await self.activate(session, subscription, event, now=now)
        elif event.event_type is PaymentEventType.FAILED:
            await self.mark_past_due(session, subscription)
        else:
            await self.cancel(session, subscription)

        await session.commit()
        await session.refresh(subscription)
        return subscription

    async def expire_trials(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        now: datetime | None = None,
    ) -> int:
        """TRIAL -> PAST_DUE for every trial that ran out (service auto-suspended)."""
        current = ensure_aware(now) or utcnow()
        async with session_factory() as session:
            result = await session.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.trial_ends_at.is_not(None),
                    Subscription.trial_ends_at <= current,
                )
            )
            expiring = list(result.scalars().all())
            for subscription in expiring:
                await self.mark_past_due(session, subscription)
            await session.commit()
        if expiring:
            logger.info("Expired %s trial subscription(s)", len(expiring))
        return len(expiring)

    async def suspend_delinquent_subscriptions(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        now: datetime | None = None,
    ) -> int:
        """PAST_DUE -> CANCELED once the grace period after the failed payment is over."""
        current = ensure_aware(now) or utcnow()
        grace_cutoff = current - timedelta(days=settings.BILLING_GRACE_PERIOD_DAYS)

        async with session_factory() as session:
            result = await session.execute(
                select(Subscription).where(Subscription.status == SubscriptionStatus.PAST_DUE)
            )
            delinquent = list(result.scalars().all())
            canceled = 0
            for subscription in delinquent:
                anchor = ensure_aware(subscription.paid_until or subscription.trial_ends_at) or current
                if anchor <= grace_cutoff:
                    await self.cancel(session, subscription)
                    canceled += 1
            await session.commit()
        if canceled:
            logger.info("Canceled %s delinquent subscription(s) after the grace period", canceled)
        return canceled

    # ------------------------------------------------------------------
    # Payment provider API (checkout + recurring charges)
    # ------------------------------------------------------------------
    def amount_payload(self, amount: Decimal | None = None) -> dict[str, str]:
        value = amount if amount is not None else self.price_rub
        return {"value": f"{value:.2f}", "currency": self.currency}

    @staticmethod
    def renewal_idempotence_key(subscription: Subscription, now: datetime | None = None) -> str:
        """Deterministic idempotence key: exactly one renewal charge per paid period."""
        anchor = ensure_aware(subscription.paid_until) or ensure_aware(now) or utcnow()
        return f"renewal-{subscription.id}-{anchor.strftime('%Y%m%d')}"

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        if not (self.shop_id and self.secret_key):
            raise BillingConfigurationError("YooKassa shop_id / secret_key are not configured")

        url = f"{self.api_url}{path}"
        auth = httpx.BasicAuth(self.shop_id, self.secret_key)
        if http_client is not None:
            response = await http_client.post(
                url, json=payload, headers=headers, auth=auth, timeout=30.0
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, json=payload, headers=headers, auth=auth, timeout=30.0
                )

        if response.status_code >= 400:
            raise BillingProviderError(
                f"YooKassa rejected {path} with status {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise BillingProviderError(f"YooKassa returned a non-JSON response for {path}") from exc
        if not isinstance(data, dict):
            raise BillingProviderError(f"Unexpected YooKassa response for {path}")
        return data

    async def create_checkout_payment(
        self,
        user: User,
        amount: Decimal | None = None,
        return_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        idempotence_key: str | None = None,
    ) -> CheckoutPayment:
        """Create a saved-card payment that starts the first paid period."""
        payload: dict[str, Any] = {
            "amount": self.amount_payload(amount),
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url or settings.BILLING_RETURN_URL,
            },
            "save_payment_method": True,
            "description": "ReputationAI: подписка на мониторинг отзывов",
            "metadata": {"user_id": str(user.id), "renewal": "false"},
        }
        headers = {
            "Idempotence-Key": idempotence_key
            or f"checkout-{user.id}-{utcnow().strftime('%Y%m%dT%H')}",
            "Content-Type": "application/json",
        }
        data = await self._post("/payments", payload, headers, http_client)
        confirmation = data.get("confirmation") if isinstance(data.get("confirmation"), dict) else {}
        confirmation_url = str(confirmation.get("confirmation_url") or "")
        if not data.get("id") or not confirmation_url:
            raise BillingProviderError("YooKassa did not return a confirmation URL")

        return CheckoutPayment(
            payment_id=str(data["id"]),
            confirmation_url=confirmation_url,
            amount=parse_amount(data.get("amount")) or self.price_rub,
            currency=self.currency,
            status=str(data.get("status") or "pending"),
        )

    async def create_recurring_charge(
        self,
        subscription: Subscription,
        amount: Decimal | None = None,
        http_client: httpx.AsyncClient | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """Charge the saved payment method (recurring subscription auto-renewal)."""
        if not subscription.payment_provider_id:
            logger.warning(
                "Subscription %s has no saved payment method; renewal skipped", subscription.id
            )
            return None

        payload: dict[str, Any] = {
            "amount": self.amount_payload(amount),
            "capture": True,
            "payment_method_id": subscription.payment_provider_id,
            "description": "ReputationAI: продление подписки",
            "metadata": {
                "user_id": str(subscription.user_id),
                "subscription_id": str(subscription.id),
                "renewal": "true",
            },
        }
        headers = {
            "Idempotence-Key": self.renewal_idempotence_key(subscription, now),
            "Content-Type": "application/json",
        }
        data = await self._post("/payments", payload, headers, http_client)
        payment_id = data.get("id")
        logger.info("Recurring charge %s created for subscription %s", payment_id, subscription.id)
        return str(payment_id) if payment_id else None

    async def charge_due_subscriptions(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        now: datetime | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> int:
        """Auto-renew ACTIVE subscriptions that run out within the renewal lead window."""
        current = ensure_aware(now) or utcnow()
        lead = current + timedelta(hours=settings.BILLING_RENEWAL_LEAD_HOURS)

        async with session_factory() as session:
            result = await session.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.paid_until.is_not(None),
                    Subscription.paid_until <= lead,
                )
            )
            due = list(result.scalars().all())

        charged = 0
        for subscription in due:
            try:
                payment_id = await self.create_recurring_charge(
                    subscription, http_client=http_client, now=current
                )
            except (BillingProviderError, BillingConfigurationError) as exc:
                logger.error("Recurring charge failed for subscription %s: %s", subscription.id, exc)
                continue
            if payment_id:
                charged += 1
        return charged


