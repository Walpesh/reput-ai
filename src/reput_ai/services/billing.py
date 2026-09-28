import hmac
import hashlib
import logging
from typing import Any
from reput_ai.config import settings

logger = logging.getLogger("reput_ai.services.billing")


class BillingService:
    def __init__(self, shop_id: str | None = None, secret_key: str | None = None):
        self.shop_id = shop_id or settings.YOOKASSA_SHOP_ID
        self.secret_key = secret_key or settings.YOOKASSA_SECRET_KEY

    def verify_webhook_signature(self, payload_body: bytes, signature_header: str) -> bool:
        """Validate HMAC signature for incoming Yookassa / T-Bank payment webhooks."""
        if not self.secret_key:
            logger.warning("Billing secret key not set, skipping verification in development")
            return True

        expected_signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_signature, signature_header)

    async def handle_payment_event(self, event_data: dict[str, Any]) -> dict[str, str]:
        event_type = event_data.get("event")
        logger.info(f"Processing billing event: {event_type}")
        return {"status": "processed", "event": str(event_type)}
