"""Stage 3 end-to-end (headless) test suite: workers, billing engine and the funnel.

The whole system runs for real - FastAPI endpoints, the Celery worker tasks (executed
eagerly in-process), the Yandex Maps scraping pipeline, the AI drafting pipeline and
the Telegram approval flow - against an isolated SQLite database. Only the outer
network edges are stubbed:

* Yandex Maps HTTP    -> ``httpx.MockTransport`` with a canned reviews payload;
* OpenRouter HTTP     -> ``httpx.MockTransport`` with a canned JSON reply;
* YooKassa HTTP       -> fake ``BillingService._post`` recording payment requests;
* Telegram Bot API    -> ``MockedSession`` recording every outgoing Bot API method.
"""

import hashlib
import hmac
import asyncio
import json
import threading
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from reput_ai.api.v1 import funnel as funnel_module
from reput_ai.api.v1 import telegram_webhook
from reput_ai.bot.bot import create_dispatcher
from reput_ai.bot.handlers import review_approval
from reput_ai.config import resolve_scraper_poll_interval_minutes, settings
from reput_ai.core.time import ensure_aware, utcnow
from reput_ai.db.base import Base
from reput_ai.db.models.branch import PlatformType, ToneOfVoice
from reput_ai.db.models.review import Review, ReviewStatus
from reput_ai.db.models.subscription import Subscription, SubscriptionStatus
from reput_ai.db.session import get_async_db
from reput_ai.llm.client import LLMClient
from reput_ai.llm.prompts import build_system_prompt
from reput_ai.main import app
from reput_ai.services.billing import BillingService
from reput_ai.services.scraper import YandexMapsScraper
from reput_ai.workers.celery_app import celery_app
from reput_ai.workers.tasks import ai_tasks, billing_tasks, scraper as scraper_tasks

TEST_BOT_TOKEN = "1234567890:E2E-BOT-TOKEN-FOR-HEADLESS-TESTS"
TEST_TELEGRAM_ID = 777000111
OWNER_PASSWORD = "e2e-strong-password-123"
WEBHOOK_SECRET = "e2e-webhook-secret"
TBANK_PASSWORD = "e2e-tbank-password"
SHOP_ID = "e2e-shop"
SHOP_SECRET = "e2e-shop-secret"
PRICE_RUB = 2990

YANDEX_BRANCH_URL = "https://yandex.ru/maps/org/reput-ai-demo/1234567890123/"
BRANCH_NAME = "Филиал на Ленина"



# ---------------------------------------------------------------------------
# Helpers: payload builders
# ---------------------------------------------------------------------------


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_dt(value: str) -> datetime:
    """Parse an API timestamp into an aware UTC datetime."""
    parsed = ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    assert parsed is not None
    return parsed


def yandex_reviews_payload(*reviews: tuple[str, str, int, str]) -> dict[str, Any]:
    """Canned ``fetchReviews`` response of Yandex Maps accepted by the scraper."""
    return {
        "data": {
            "reviews": [
                {
                    "reviewId": external_id,
                    "author": {"name": author},
                    "rating": rating,
                    "text": text,
                    "updatedTime": "2026-09-20T10:00:00Z",
                }
                for external_id, author, rating, text in reviews
            ]
        }
    }


def yookassa_event(
    event: str,
    *,
    user_id: str,
    payment_id: str = "pay-e2e-1",
    method_id: str | None = "pm-saved-1",
    renewal: bool = False,
    reason: str | None = None,
    captured_at: datetime | None = None,
    amount: str = f"{PRICE_RUB}.00",
) -> dict[str, Any]:
    """YooKassa notification (``payment.succeeded`` / ``payment.canceled`` / refund)."""
    moment = captured_at or utcnow()
    obj: dict[str, Any] = {
        "id": payment_id,
        "status": "succeeded" if event == "payment.succeeded" else "canceled",
        "paid": event == "payment.succeeded",
        "amount": {"value": amount, "currency": "RUB"},
        "created_at": moment.isoformat(),
        "captured_at": moment.isoformat(),
        "metadata": {"user_id": user_id, "renewal": "true" if renewal else "false"},
    }
    if method_id:
        obj["payment_method"] = {"id": method_id, "saved": True}
    if reason:
        obj["cancellation_details"] = {"party": "payment_network", "reason": reason}
    return {"type": "notification", "event": event, "object": obj}


def tbank_payload(
    status: str,
    *,
    user_id: str,
    payment_id: str = "tb-e2e-1",
    renewal: bool = False,
) -> dict[str, Any]:
    """T-Bank acquiring notification signed with a valid ``Token``."""
    payload: dict[str, Any] = {
        "TerminalKey": "E2ETerminal",
        "OrderId": "order-e2e-1",
        "Success": status == "CONFIRMED",
        "Status": status,
        "PaymentId": payment_id,
        "Amount": PRICE_RUB * 100,
        "Currency": "RUB",
        "DATA": {"user_id": user_id, "renewal": "true" if renewal else "false"},
    }
    concatenated = "".join(
        str(value)
        for key, value in sorted(payload.items())
        if key != "Token" and not isinstance(value, (dict, list))
    )
    payload["Token"] = hashlib.sha256(
        f"{concatenated}{TBANK_PASSWORD}".encode("utf-8")
    ).hexdigest()
    return payload


async def post_signed_webhook(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    secret: str = WEBHOOK_SECRET,
) -> httpx.Response:
    """POST a notification signed with the YooKassa HMAC secret."""
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return await client.post(
        f"{settings.API_V1_STR}/subscriptions/webhook",
        content=body,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )


def callback_payload(callback_data: str, update_id: int = 1) -> dict[str, Any]:
    """Telegram callback update used to drive the approval handlers."""
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{update_id}",
            "from": {"id": TEST_TELEGRAM_ID, "is_bot": False, "first_name": "Owner"},
            "chat_instance": "e2e-chat-instance",
            "data": callback_data,
            "message": {
                "message_id": 100 + update_id,
                "date": int(utcnow().timestamp()),
                "chat": {"id": TEST_TELEGRAM_ID, "type": "private"},
                "from": {"id": 42, "is_bot": True, "first_name": "ReputationAI"},
                "text": "Новый отзыв",
            },
        },
    }


# ---------------------------------------------------------------------------
# Stubs for the outer network edges
# ---------------------------------------------------------------------------


class MockedSession(BaseSession):
    """Offline stand-in for the aiohttp Telegram session recording outgoing calls."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("File streaming is not supported in the headless tests")

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.requests.append(method)
        return True

    def calls(self, method_name: str) -> list[TelegramMethod[Any]]:
        return [request for request in self.requests if type(request).__name__ == method_name]


class YandexStub:
    """Serves canned Yandex Maps payloads and counts the scraping requests."""

    def __init__(self) -> None:
        self.responses: list[dict[str, Any]] = []
        self.status_codes: list[int] = []
        self.request_urls: list[str] = []

    def respond(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.responses.append(payload)
        self.status_codes.append(status_code)
        return None

    @property
    def call_count(self) -> int:
        return len(self.request_urls)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.request_urls.append(str(request.url))
        if not self.responses:
            return httpx.Response(200, json={"data": {"reviews": []}}, request=request)
        index = min(len(self.request_urls) - 1, len(self.responses) - 1)
        return httpx.Response(
            self.status_codes[index], json=self.responses[index], request=request
        )


class OpenRouterStub:
    """Replays queued OpenRouter replies and records the request payloads."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.payloads: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content.decode("utf-8")))
        index = min(len(self.payloads) - 1, len(self.replies) - 1)
        content = json.dumps({"reply": self.replies[index]}, ensure_ascii=False)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
            request=request,
        )


class StubLLMClient(LLMClient):
    """Real LLM client logic (prompts, sanitizing, JSON parsing) over a stub transport."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        super().__init__(
            api_key="e2e-openrouter-key",
            base_url="https://openrouter.test/api/v1",
            primary_model="e2e/primary",
            fallback_model="e2e/fallback",
            max_retries=1,
            retry_min_wait=0.0,
            retry_max_wait=0.0,
        )
        self._http_client = http_client

    async def generate_reply(
        self,
        review_text: str,
        tone_of_voice: ToneOfVoice = ToneOfVoice.OFFICIAL,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        return await super().generate_reply(review_text, tone_of_voice, client=self._http_client)


# ---------------------------------------------------------------------------
# Fixtures: isolated database, app client, worker wiring and network stubs
# ---------------------------------------------------------------------------


class BackgroundLoop:
    """Dedicated event loop in a daemon thread.

    Celery task bodies are synchronous and call ``run_async`` (``asyncio.run`` in
    production). Inside a pytest-asyncio test a loop is already running, so the E2E
    suite swaps the bridge for this blocking helper: the coroutine runs on its own
    loop and the calling (synchronous) task code waits for the result.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._serve, name="e2e-celery-loop", daemon=True
        )
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Blocking bridge used instead of ``reput_ai.workers.runtime.run_async``."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def shutdown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


@pytest.fixture
async def e2e_engine(tmp_path):
    """Dedicated file-backed SQLite database so workers and the API share one DB."""
    database_path = (tmp_path / "e2e.db").as_posix()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}", echo=False, poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def e2e_session_factory(e2e_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(e2e_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def e2e_client(e2e_session_factory):
    """Async HTTP client speaking to the real FastAPI app on the E2E database."""

    async def override_get_async_db():
        async with e2e_session_factory() as session:
            yield session

    app.dependency_overrides[get_async_db] = override_get_async_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://e2e.test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def billing_settings(monkeypatch):
    """YooKassa / T-Bank credentials used by the signed-notification tests."""
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", SHOP_ID)
    monkeypatch.setattr(settings, "YOOKASSA_SECRET_KEY", SHOP_SECRET)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "TBANK_PASSWORD", TBANK_PASSWORD)
    monkeypatch.setattr(settings, "BILLING_PRICE_RUB", PRICE_RUB)
    return settings


@pytest.fixture
def yookassa_api(monkeypatch) -> list[dict[str, Any]]:
    """Record YooKassa payment creation instead of talking to api.yookassa.ru."""
    calls: list[dict[str, Any]] = []

    async def fake_post(self, path, payload, headers, http_client=None):
        calls.append({"path": path, "payload": payload, "headers": headers, "client": http_client})
        payment_id = f"pay-e2e-{len(calls)}"
        return {
            "id": payment_id,
            "status": "pending",
            "amount": payload.get("amount", {}),
            "confirmation": {
                "type": "redirect",
                "confirmation_url": f"https://yookassa.test/checkout/{payment_id}",
            },
        }

    monkeypatch.setattr(BillingService, "_post", fake_post)
    return calls


@pytest.fixture
def eager_celery(monkeypatch):
    """Execute Celery tasks in-process (no broker) and wait for their async work."""
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)
    # The task/beat wiring stays exactly as in production, but the transport points at
    # kombu's in-memory broker so the headless suite never needs a running Redis.
    monkeypatch.setattr(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")

    background = BackgroundLoop()
    for module in (scraper_tasks, ai_tasks, billing_tasks):
        monkeypatch.setattr(module, "run_async", background.run)

    yield celery_app
    background.shutdown()


@pytest.fixture
def wired_workers(monkeypatch, e2e_session_factory):
    """Point every worker task and the bot handlers at the isolated E2E database."""
    for module in (scraper_tasks, ai_tasks, billing_tasks, review_approval):
        monkeypatch.setattr(module, "async_session_maker", e2e_session_factory)
    return e2e_session_factory


@pytest.fixture
async def telegram_bot(monkeypatch):
    """Telegram bot wired to an offline session that records outgoing methods."""
    session = MockedSession()
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    dispatcher = create_dispatcher()

    monkeypatch.setattr(ai_tasks, "create_bot", lambda: bot)
    monkeypatch.setattr(funnel_module, "create_bot", lambda: bot)
    monkeypatch.setattr(
        telegram_webhook, "get_webhook_bot_and_dispatcher", lambda: (bot, dispatcher)
    )

    yield bot, session
    await session.close()


@pytest.fixture
async def yandex_stub(monkeypatch):
    """Stub the Yandex Maps HTTP call used by the scraping worker task."""
    stub = YandexStub()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handle))

    def build(platform_type: PlatformType, http_client: httpx.AsyncClient | None = None):
        if platform_type is not PlatformType.YANDEX:
            return None
        return YandexMapsScraper(
            http_client=client, max_attempts=1, retry_min_wait=0.0, retry_max_wait=0.0
        )

    monkeypatch.setattr(scraper_tasks, "build_scraper", build)
    yield stub
    await client.aclose()


@pytest.fixture
async def llm_stub(monkeypatch):
    """Stub the OpenRouter HTTP call: canned replies, real client logic."""
    stub = OpenRouterStub(["Здравствуйте! Спасибо за отзыв, мы всё исправим."])
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handle))
    monkeypatch.setattr(ai_tasks, "build_llm_client", lambda: StubLLMClient(client))
    yield stub
    await client.aclose()


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------


async def register_owner(
    client: httpx.AsyncClient,
    telegram_id: int = TEST_TELEGRAM_ID,
) -> dict[str, str]:
    """Register a fresh owner account and log in (returns user id + access token)."""
    email = f"e2e-owner-{uuid.uuid4().hex[:8]}@example.com"
    register = await client.post(
        f"{settings.API_V1_STR}/auth/register",
        json={"email": email, "password": OWNER_PASSWORD, "telegram_id": telegram_id},
    )
    assert register.status_code == 201, register.text

    login = await client.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": email, "password": OWNER_PASSWORD},
    )
    assert login.status_code == 200, login.text

    return {
        "email": email,
        "user_id": register.json()["id"],
        "token": login.json()["access_token"],
    }


async def create_branch(
    client: httpx.AsyncClient,
    token: str,
    *,
    name: str = BRANCH_NAME,
    platform_url: str = YANDEX_BRANCH_URL,
    tone: str = "FRIENDLY",
) -> dict[str, Any]:
    response = await client.post(
        f"{settings.API_V1_STR}/branches/",
        json={
            "name": name,
            "platform_type": "YANDEX",
            "platform_url": platform_url,
            "tone_of_voice": tone,
            "is_active": True,
        },
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def get_subscription(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(
        f"{settings.API_V1_STR}/subscriptions/current", headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def fetch_branches(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(
        f"{settings.API_V1_STR}/branches/", headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def fetch_reviews(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(f"{settings.API_V1_STR}/reviews/", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


async def move_trial_end(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: str,
    *,
    days_ago: int,
) -> None:
    """Push the trial end into the past to simulate an expired trial."""
    async with session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == UUID(user_id))
        )
        subscription = result.scalar_one()
        subscription.trial_ends_at = utcnow() - timedelta(days=days_ago)
        await session.commit()


async def move_paid_until(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: str,
    *,
    hours_ahead: int,
) -> None:
    """Move ``paid_until`` close to now so the renewal window is reached."""
    async with session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == UUID(user_id))
        )
        subscription = result.scalar_one()
        subscription.paid_until = utcnow() + timedelta(hours=hours_ahead)
        await session.commit()


async def load_review(session_factory: async_sessionmaker[AsyncSession], review_id: str) -> Review:
    async with session_factory() as session:
        result = await session.execute(select(Review).where(Review.id == UUID(review_id)))
        return result.scalar_one()


async def load_subscription(
    session_factory: async_sessionmaker[AsyncSession], user_id: str
) -> Subscription:
    async with session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == UUID(user_id))
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# 3.2 Billing engine: TRIAL -> ACTIVE -> PAST_DUE -> CANCELED
# ---------------------------------------------------------------------------


async def test_e2e_trial_subscription_starts_on_registration(e2e_client):
    owner = await register_owner(e2e_client)
    subscription = await get_subscription(e2e_client, owner["token"])

    assert subscription["status"] == SubscriptionStatus.TRIAL.value
    assert subscription["paid_until"] is None
    assert subscription["payment_provider_id"] is None

    trial_ends_at = parse_dt(subscription["trial_ends_at"])
    expected_end = utcnow() + timedelta(days=settings.TRIAL_PERIOD_DAYS)
    assert abs((trial_ends_at - expected_end).total_seconds()) < 120

    # The trial is created exactly once: the second call returns the same record.
    assert (await get_subscription(e2e_client, owner["token"]))["id"] == subscription["id"]


async def test_e2e_billing_webhook_rejects_unsigned_notifications(e2e_client, billing_settings):
    owner = await register_owner(e2e_client)
    event = yookassa_event("payment.succeeded", user_id=owner["user_id"])
    body = json.dumps(event).encode("utf-8")
    url = f"{settings.API_V1_STR}/subscriptions/webhook"

    missing = await e2e_client.post(url, content=body, headers={"Content-Type": "application/json"})
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid webhook signature"

    forged = await e2e_client.post(
        url,
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "deadbeef"},
    )
    assert forged.status_code == 401

    wrong_secret = await post_signed_webhook(e2e_client, event, secret="not-the-webhook-secret")
    assert wrong_secret.status_code == 401

    # Nothing changed: the subscription is still on the trial.
    assert (await get_subscription(e2e_client, owner["token"]))["status"] == (
        SubscriptionStatus.TRIAL.value
    )


async def test_e2e_signed_payment_activates_subscription_idempotently(
    e2e_client, billing_settings, e2e_session_factory
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    captured_at = utcnow()

    response = await post_signed_webhook(
        e2e_client,
        yookassa_event("payment.succeeded", user_id=owner["user_id"], captured_at=captured_at),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "subscription_status": "ACTIVE"}

    subscription = await get_subscription(e2e_client, owner["token"])
    assert subscription["status"] == SubscriptionStatus.ACTIVE.value
    # Saved card id is kept for the recurring auto-renewal charges.
    assert subscription["payment_provider_id"] == "pm-saved-1"

    paid_until = parse_dt(subscription["paid_until"])
    expected_end = captured_at + timedelta(days=settings.BILLING_PERIOD_DAYS)
    assert abs((paid_until - expected_end).total_seconds()) < 120

    # YooKassa retries the notification: the paid period must not be extended twice.
    repeat = await post_signed_webhook(
        e2e_client,
        yookassa_event("payment.succeeded", user_id=owner["user_id"], captured_at=captured_at),
    )
    assert repeat.status_code == 200

    stored = await load_subscription(e2e_session_factory, owner["user_id"])
    assert stored.status == SubscriptionStatus.ACTIVE
    assert abs((ensure_aware(stored.paid_until) - paid_until).total_seconds()) < 1


async def test_e2e_tbank_token_notification_activates_subscription(e2e_client, billing_settings):
    owner = await register_owner(e2e_client)
    payload = tbank_payload("CONFIRMED", user_id=owner["user_id"])
    url = f"{settings.API_V1_STR}/subscriptions/webhook"

    unsigned = {key: value for key, value in payload.items() if key != "Token"}
    rejected = await e2e_client.post(url, json=unsigned)
    assert rejected.status_code == 401

    accepted = await e2e_client.post(url, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["subscription_status"] == SubscriptionStatus.ACTIVE.value

    subscription = await get_subscription(e2e_client, owner["token"])
    assert subscription["status"] == SubscriptionStatus.ACTIVE.value
    assert subscription["paid_until"] is not None


async def test_e2e_failed_payment_suspends_service_and_stops_polling(
    e2e_client,
    billing_settings,
    eager_celery,
    wired_workers,
    yandex_stub,
):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])
    await post_signed_webhook(
        e2e_client, yookassa_event("payment.succeeded", user_id=owner["user_id"])
    )

    # The renewal charge fails (insufficient funds) -> automatic service suspension.
    failed = await post_signed_webhook(
        e2e_client,
        yookassa_event(
            "payment.canceled",
            user_id=owner["user_id"],
            reason="insufficient_funds",
            renewal=True,
        ),
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["subscription_status"] == SubscriptionStatus.PAST_DUE.value

    branches = await fetch_branches(e2e_client, owner["token"])
    assert branches[0]["id"] == branch["id"]
    assert branches[0]["is_active"] is False

    # Suspended accounts are not polled any more by the Celery beat cycle.
    cycle = scraper_tasks.poll_all_branches_reviews.delay()
    assert cycle.get() == {"status": "success", "branches": 0}
    assert yandex_stub.call_count == 0


async def test_e2e_recovery_payment_resumes_the_scraping_cycle(
    e2e_client,
    billing_settings,
    eager_celery,
    wired_workers,
    yandex_stub,
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    await post_signed_webhook(
        e2e_client, yookassa_event("payment.succeeded", user_id=owner["user_id"])
    )
    await post_signed_webhook(
        e2e_client,
        yookassa_event("payment.canceled", user_id=owner["user_id"], reason="insufficient_funds"),
    )

    recovered = await post_signed_webhook(
        e2e_client,
        yookassa_event("payment.succeeded", user_id=owner["user_id"], payment_id="pay-recovery-1"),
    )
    assert recovered.json()["subscription_status"] == SubscriptionStatus.ACTIVE.value
    assert (await fetch_branches(e2e_client, owner["token"]))[0]["is_active"] is True

    cycle = scraper_tasks.poll_all_branches_reviews.delay()
    assert cycle.get() == {"status": "success", "branches": 1}
    assert yandex_stub.call_count == 1


async def test_e2e_trial_expires_to_past_due_then_canceled_after_grace_period(
    e2e_client,
    billing_settings,
    eager_celery,
    wired_workers,
    e2e_session_factory,
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    await move_trial_end(e2e_session_factory, owner["user_id"], days_ago=1)

    expired = billing_tasks.expire_trial_subscriptions.delay()
    assert expired.get() == {"status": "success", "expired": 1}
    assert (await get_subscription(e2e_client, owner["token"]))["status"] == (
        SubscriptionStatus.PAST_DUE.value
    )
    assert (await fetch_branches(e2e_client, owner["token"]))[0]["is_active"] is False

    # Still inside the grace window: the subscription is not canceled yet.
    early = billing_tasks.suspend_delinquent_subscriptions.delay()
    assert early.get() == {"status": "success", "canceled": 0}

    await move_trial_end(
        e2e_session_factory,
        owner["user_id"],
        days_ago=settings.BILLING_GRACE_PERIOD_DAYS + 2,
    )
    late = billing_tasks.suspend_delinquent_subscriptions.delay()
    assert late.get() == {"status": "success", "canceled": 1}
    assert (await get_subscription(e2e_client, owner["token"]))["status"] == (
        SubscriptionStatus.CANCELED.value
    )


async def test_e2e_recurring_renewal_charges_saved_card(
    e2e_client,
    billing_settings,
    yookassa_api,
    eager_celery,
    wired_workers,
    e2e_session_factory,
):
    owner = await register_owner(e2e_client)
    await post_signed_webhook(
        e2e_client, yookassa_event("payment.succeeded", user_id=owner["user_id"])
    )
    await move_paid_until(e2e_session_factory, owner["user_id"], hours_ahead=1)

    charged = billing_tasks.charge_due_subscriptions.delay()
    assert charged.get() == {"status": "success", "charged": 1}
    assert len(yookassa_api) == 1

    request = yookassa_api[0]
    assert request["path"] == "/payments"
    assert request["payload"]["payment_method_id"] == "pm-saved-1"
    assert request["payload"]["metadata"]["renewal"] == "true"
    assert request["payload"]["amount"] == {"value": f"{PRICE_RUB}.00", "currency": "RUB"}

    subscription = await load_subscription(e2e_session_factory, owner["user_id"])
    period = ensure_aware(subscription.paid_until).strftime("%Y%m%d")
    expected_key = f"renewal-{subscription.id}-{period}"
    assert request["headers"]["Idempotence-Key"] == expected_key

    # A second sweep within the same period reuses the idempotence key: no double charge.
    billing_tasks.charge_due_subscriptions.delay()
    assert yookassa_api[1]["headers"]["Idempotence-Key"] == expected_key


async def test_e2e_checkout_endpoint_creates_saved_card_payment(
    e2e_client, billing_settings, yookassa_api
):
    owner = await register_owner(e2e_client)

    response = await e2e_client.post(
        f"{settings.API_V1_STR}/subscriptions/checkout", headers=auth_headers(owner["token"])
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["payment_id"] == "pay-e2e-1"
    assert data["confirmation_url"].startswith("https://yookassa.test/checkout/")
    assert float(data["amount"]) == PRICE_RUB
    assert data["currency"] == "RUB"

    payload = yookassa_api[0]["payload"]
    assert payload["save_payment_method"] is True
    assert payload["capture"] is True
    assert payload["metadata"]["user_id"] == owner["user_id"]
    assert payload["confirmation"]["return_url"] == settings.BILLING_RETURN_URL


async def test_e2e_checkout_without_provider_credentials_returns_503(e2e_client, monkeypatch):
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", None)
    monkeypatch.setattr(settings, "YOOKASSA_SECRET_KEY", None)
    owner = await register_owner(e2e_client)

    response = await e2e_client.post(
        f"{settings.API_V1_STR}/subscriptions/checkout", headers=auth_headers(owner["token"])
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


async def test_e2e_owner_cancels_subscription(e2e_client, billing_settings):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])

    response = await e2e_client.post(
        f"{settings.API_V1_STR}/subscriptions/cancel", headers=auth_headers(owner["token"])
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == SubscriptionStatus.CANCELED.value
    assert (await fetch_branches(e2e_client, owner["token"]))[0]["is_active"] is False


# ---------------------------------------------------------------------------
# 3.1 Celery workers: Yandex Maps polling and AI drafting
# ---------------------------------------------------------------------------


async def test_e2e_polling_interval_stays_inside_the_15_30_minute_window():
    assert resolve_scraper_poll_interval_minutes(5) == settings.SCRAPER_MIN_POLL_INTERVAL_MINUTES
    assert resolve_scraper_poll_interval_minutes(45) == settings.SCRAPER_MAX_POLL_INTERVAL_MINUTES
    assert 15 <= resolve_scraper_poll_interval_minutes() <= 30

    poll_interval = celery_app.conf.beat_schedule["poll-branches-reviews-periodically"]["schedule"]
    assert 15 * 60 <= poll_interval <= 30 * 60
    assert celery_app.conf.beat_schedule["process-new-reviews-with-ai"]["task"].endswith(
        "process_new_reviews"
    )


async def test_e2e_scraper_poll_ingests_reviews_and_deduplicates(
    e2e_client, eager_celery, wired_workers, yandex_stub
):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])
    yandex_stub.respond(
        yandex_reviews_payload(
            ("rev-1", "Иван", 2, "Долго ждали заказ"),
            ("rev-2", "Мария", 5, "Всё отлично, спасибо!"),
        )
    )

    cycle = scraper_tasks.poll_all_branches_reviews.delay()
    assert cycle.get() == {"status": "success", "branches": 1}
    assert yandex_stub.call_count == 1
    assert "businessId=1234567890123" in yandex_stub.request_urls[0]

    reviews = await fetch_reviews(e2e_client, owner["token"])
    assert {review["external_id"] for review in reviews} == {"rev-1", "rev-2"}
    assert {review["status"] for review in reviews} == {ReviewStatus.NEW.value}
    assert {review["author_name"] for review in reviews} == {"Иван", "Мария"}

    # Second scraping pass: every review is recognized by its external id.
    again = scraper_tasks.scrape_branch_reviews.delay(branch["id"])
    assert again.get() == {
        "status": "success",
        "branch_id": branch["id"],
        "created": 0,
        "duplicates": 2,
    }
    assert len(await fetch_reviews(e2e_client, owner["token"])) == 2


async def test_e2e_scraper_failure_does_not_break_the_polling_cycle(
    e2e_client, eager_celery, wired_workers, yandex_stub
):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])
    yandex_stub.respond({"error": "too many requests"}, status_code=429)

    result = scraper_tasks.scrape_branch_reviews.delay(branch["id"]).get()
    assert result["status"] == "error"
    assert "429" in result["error"]
    assert yandex_stub.call_count == 1  # max_attempts=1 in this stub
    assert await fetch_reviews(e2e_client, owner["token"]) == []


async def test_e2e_ai_worker_drafts_reply_and_notifies_the_owner(
    e2e_client,
    eager_celery,
    wired_workers,
    yandex_stub,
    llm_stub,
    telegram_bot,
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    yandex_stub.respond(yandex_reviews_payload(("rev-1", "Иван", 2, "Долго ждали заказ")))
    scraper_tasks.poll_all_branches_reviews.delay()

    queued = ai_tasks.process_new_reviews.delay()
    assert queued.get() == {"status": "success", "queued": 1}

    reviews = await fetch_reviews(e2e_client, owner["token"])
    assert reviews[0]["status"] == ReviewStatus.PENDING_APPROVAL.value
    assert reviews[0]["generated_reply"] == "Здравствуйте! Спасибо за отзыв, мы всё исправим."

    _, session = telegram_bot
    notifications = session.calls("SendMessage")
    assert len(notifications) == 1
    assert notifications[0].chat_id == TEST_TELEGRAM_ID
    assert "Долго ждали заказ" in notifications[0].text
    assert "Долго ждали заказ" in llm_stub.payloads[0]["messages"][1]["content"]
    assert llm_stub.payloads[0]["messages"][0]["content"] == build_system_prompt(
        ToneOfVoice.FRIENDLY
    )
    keyboard = notifications[0].reply_markup
    assert f"review:approve:{reviews[0]['id']}" == keyboard.inline_keyboard[0][0].callback_data


async def test_e2e_regeneration_via_telegram_callback_updates_the_draft(
    e2e_client,
    eager_celery,
    wired_workers,
    yandex_stub,
    llm_stub,
    telegram_bot,
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    yandex_stub.respond(yandex_reviews_payload(("rev-1", "Иван", 2, "Долго ждали заказ")))
    scraper_tasks.poll_all_branches_reviews.delay()
    ai_tasks.process_new_reviews.delay()

    _, session = telegram_bot
    review_id = (await fetch_reviews(e2e_client, owner["token"]))[0]["id"]

    llm_stub.replies.append("Извиняемся за ожидание! Исправим ситуацию.")
    response = await e2e_client.post(
        f"{settings.API_V1_STR}/telegram/webhook",
        json=callback_payload(f"review:regenerate:{review_id}"),
    )
    assert response.status_code == 200

    updated = (await fetch_reviews(e2e_client, owner["token"]))[0]
    assert updated["generated_reply"] == "Извиняемся за ожидание! Исправим ситуацию."
    assert updated["status"] == ReviewStatus.PENDING_APPROVAL.value
    assert len(session.calls("SendMessage")) == 2
    assert "перегенерация" in session.calls("AnswerCallbackQuery")[0].text.lower()


async def test_e2e_approval_via_telegram_webhook_publishes_the_reply(
    e2e_client,
    eager_celery,
    wired_workers,
    yandex_stub,
    llm_stub,
    telegram_bot,
    e2e_session_factory,
):
    owner = await register_owner(e2e_client)
    await create_branch(e2e_client, owner["token"])
    yandex_stub.respond(yandex_reviews_payload(("rev-1", "Иван", 2, "Долго ждали заказ")))
    scraper_tasks.poll_all_branches_reviews.delay()
    ai_tasks.process_new_reviews.delay()

    _, session = telegram_bot
    review_id = (await fetch_reviews(e2e_client, owner["token"]))[0]["id"]

    response = await e2e_client.post(
        f"{settings.API_V1_STR}/telegram/webhook",
        json=callback_payload(f"review:approve:{review_id}"),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    stored = await load_review(e2e_session_factory, review_id)
    assert stored.status == ReviewStatus.APPROVED
    assert stored.final_reply == stored.generated_reply == llm_stub.replies[0]
    assert "одобрен" in session.calls("AnswerCallbackQuery")[0].text


# ---------------------------------------------------------------------------
# 3.3 Feedback funnel: QR short links and the negative feedback interceptor
# ---------------------------------------------------------------------------


async def test_e2e_funnel_short_link_routes_ratings_to_public_and_private_targets(
    e2e_client,
):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])

    link = await e2e_client.get(f"{settings.API_V1_STR}/funnel/short-link/{branch['id']}")
    assert link.status_code == 200
    link_data = link.json()
    assert link_data["short_url"].endswith(f"/funnel/s/{link_data['code']}")
    code = link_data["code"]

    public = await e2e_client.get(f"{settings.API_V1_STR}/funnel/s/{code}", params={"rating": 5})
    assert public.status_code == 307
    assert public.headers["location"] == branch["platform_url"]

    internal = await e2e_client.get(f"{settings.API_V1_STR}/funnel/s/{code}", params={"rating": 3})
    assert internal.status_code == 307
    assert settings.FEEDBACK_FORM_PATH in internal.headers["location"]
    assert branch["id"] in internal.headers["location"]

    entry = await e2e_client.get(
        f"{settings.API_V1_STR}/funnel/s/{code}/entry", params={"rating": 4}
    )
    assert entry.status_code == 200
    assert entry.json()["mode"] == "public"
    assert entry.json()["public_url"] == branch["platform_url"]
    assert entry.json()["branch_name"] == BRANCH_NAME

    private_entry = await e2e_client.get(f"{settings.API_V1_STR}/funnel/s/{code}/entry")
    assert private_entry.json()["mode"] == "internal"
    assert private_entry.json()["redirect_url"] == private_entry.json()["feedback_url"]

    unknown = await e2e_client.get(f"{settings.API_V1_STR}/funnel/s/not-a-valid-code")
    assert unknown.status_code == 404


async def test_e2e_negative_feedback_interceptor_alerts_management(e2e_client, telegram_bot):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])
    _, session = telegram_bot

    negative = await e2e_client.post(
        f"{settings.API_V1_STR}/funnel/submit",
        json={
            "branch_id": branch["id"],
            "rating": 2,
            "author_name": "Иван",
            "phone_or_contact": "+7 999 000-00-00",
            "feedback_text": "Грязный зал, долгое ожидание",
        },
    )
    assert negative.status_code == 200
    assert negative.json()["status"] == "success"

    alerts = session.calls("SendMessage")
    assert len(alerts) == 1
    assert alerts[0].chat_id == TEST_TELEGRAM_ID
    assert "ПЕРЕХВАЧЕН НЕГАТИВНЫЙ ОТЗЫВ" in alerts[0].text
    assert BRANCH_NAME in alerts[0].text
    assert "Грязный зал, долгое ожидание" in alerts[0].text
    assert "+7 999 000-00-00" in alerts[0].text
    assert "(2/5)" in alerts[0].text

    positive = await e2e_client.post(
        f"{settings.API_V1_STR}/funnel/submit",
        json={
            "branch_id": branch["id"],
            "rating": 5,
            "author_name": "Мария",
            "feedback_text": "Всё отлично",
        },
    )
    assert positive.status_code == 200
    assert positive.json()["status"] == "redirected"
    assert positive.json()["public_url"] == branch["platform_url"]
    # Positive feedback is never intercepted: no Telegram alert at all.
    assert len(session.calls("SendMessage")) == 1


# ---------------------------------------------------------------------------
# 3.4 Headline scenario: payment -> scraping -> AI -> approval -> funnel
# ---------------------------------------------------------------------------


async def test_e2e_full_closed_loop_from_payment_to_publication(
    e2e_client,
    billing_settings,
    yookassa_api,
    eager_celery,
    wired_workers,
    yandex_stub,
    llm_stub,
    telegram_bot,
    e2e_session_factory,
):
    owner = await register_owner(e2e_client)
    branch = await create_branch(e2e_client, owner["token"])
    _, session = telegram_bot

    # 1. The account starts on the trial, the owner checks out and pays.
    assert (await get_subscription(e2e_client, owner["token"]))["status"] == (
        SubscriptionStatus.TRIAL.value
    )
    checkout = await e2e_client.post(
        f"{settings.API_V1_STR}/subscriptions/checkout", headers=auth_headers(owner["token"])
    )
    assert checkout.status_code == 200, checkout.text
    assert yookassa_api[0]["payload"]["save_payment_method"] is True

    paid = await post_signed_webhook(
        e2e_client, yookassa_event("payment.succeeded", user_id=owner["user_id"])
    )
    assert paid.json()["subscription_status"] == SubscriptionStatus.ACTIVE.value

    # 2. The polling worker collects two fresh reviews from Yandex Maps.
    yandex_stub.respond(
        yandex_reviews_payload(
            ("rev-neg", "Иван", 1, "Испортили заказ"),
            ("rev-pos", "Мария", 5, "Отличный сервис"),
        )
    )
    cycle = scraper_tasks.poll_all_branches_reviews.delay()
    assert cycle.get() == {"status": "success", "branches": 1}

    reviews = await fetch_reviews(e2e_client, owner["token"])
    assert len(reviews) == 2
    assert {review["status"] for review in reviews} == {ReviewStatus.NEW.value}

    # 3. The AI worker drafts replies and pings the owner in Telegram.
    ai_tasks.process_new_reviews.delay()
    assert len(session.calls("SendMessage")) == 2

    # 4. The owner approves the negative review right from the notification.
    negative = next(review for review in reviews if review["rating"] == 1)
    approved = await e2e_client.post(
        f"{settings.API_V1_STR}/telegram/webhook",
        json=callback_payload(f"review:approve:{negative['id']}"),
    )
    assert approved.status_code == 200

    stored = await load_review(e2e_session_factory, negative["id"])
    assert stored.status == ReviewStatus.APPROVED
    assert stored.final_reply == llm_stub.replies[0]

    # 5. Funnel: a 5-star guest goes to the public board, a 1-star complaint is
    #    intercepted and delivered to management only.
    link = (
        await e2e_client.get(f"{settings.API_V1_STR}/funnel/short-link/{branch['id']}")
    ).json()
    public = await e2e_client.get(
        f"{settings.API_V1_STR}/funnel/s/{link['code']}", params={"rating": 5}
    )
    assert public.headers["location"] == branch["platform_url"]

    alerts_before = len(session.calls("SendMessage"))
    complaint = await e2e_client.post(
        f"{settings.API_V1_STR}/funnel/submit",
        json={
            "branch_id": branch["id"],
            "rating": 1,
            "author_name": "Пётр",
            "phone_or_contact": "+7 000 000-00-00",
            "feedback_text": "Верните деньги",
        },
    )
    assert complaint.json()["status"] == "success"
    assert len(session.calls("SendMessage")) == alerts_before + 1
    assert "ПЕРЕХВАЧЕН" in session.calls("SendMessage")[-1].text

    # 6. The subscription is still ACTIVE and the branch keeps being polled.
    assert (await get_subscription(e2e_client, owner["token"]))["status"] == (
        SubscriptionStatus.ACTIVE.value
    )
    assert (await fetch_branches(e2e_client, owner["token"]))[0]["is_active"] is True
    assert scraper_tasks.poll_all_branches_reviews.delay().get() == {
        "status": "success",
        "branches": 1,
    }
    assert len(await fetch_reviews(e2e_client, owner["token"])) == 2








