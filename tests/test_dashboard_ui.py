"""Stage 4 frontend tests: Streamlit dashboard UI workflows via AppTest.

Every API response is mocked **inside these tests only** (httpx.MockTransport);
the production dashboard connects to the existing FastAPI backend and never
contains mock data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from reput_ai.dashboard import client as client_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "src" / "reput_ai" / "dashboard" / "app.py"

TOKEN = "jwt-token"
USER_ID = "99999999-9999-9999-9999-999999999999"
BRANCH_ID = "11111111-1111-1111-1111-111111111111"
REVIEW_APPROVED = "22222222-2222-2222-2222-222222222222"
REVIEW_NEW = "33333333-3333-3333-3333-333333333333"


class MockBackend:
    """Stateful mock of the existing backend endpoints used by the dashboard."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.offline = False
        self.reject_auth = False
        self.branch_validation_error = False
        self.subscription_error: int | None = None
        self.registered: list[dict[str, Any]] = []
        self.branches: list[dict[str, Any]] = [
            {
                "id": BRANCH_ID,
                "user_id": USER_ID,
                "name": "Yandex Moscow",
                "platform_type": "YANDEX",
                "platform_url": "https://yandex.ru/maps/org/example",
                "tone_of_voice": "OFFICIAL",
                "is_active": True,
            }
        ]
        self.reviews: list[dict[str, Any]] = [
            {
                "id": REVIEW_APPROVED,
                "branch_id": BRANCH_ID,
                "external_id": "r1",
                "author_name": "Alice",
                "rating": 5,
                "text": "Great place",
                "generated_reply": "Спасибо, Alice!",
                "final_reply": None,
                "status": "APPROVED",
                "created_at": "2026-01-01T10:00:00",
            },
            {
                "id": REVIEW_NEW,
                "branch_id": BRANCH_ID,
                "external_id": "r2",
                "author_name": "Bob",
                "rating": 2,
                "text": "Too slow",
                "generated_reply": None,
                "final_reply": None,
                "status": "NEW",
                "created_at": "2026-01-02T10:00:00",
            },
        ]
        self.subscription: dict[str, Any] = {
            "id": "44444444-4444-4444-4444-444444444444",
            "user_id": USER_ID,
            "status": "TRIAL",
            "trial_ends_at": "2026-10-12T00:00:00+00:00",
            "paid_until": "2026-11-12T00:00:00+00:00",
            "payment_provider_id": "pay-12345",
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)

        self.requests.append(request)
        path = request.url.path
        auth = request.headers.get("Authorization", "")

        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "app": "ReputationAI"})

        if path == "/api/v1/auth/login":
            body = dict(httpx.QueryParams(request.content.decode()))
            if body.get("password") != "correct-horse":
                return httpx.Response(
                    401, json={"detail": "Incorrect email or password"}
                )
            return httpx.Response(
                200, json={"access_token": TOKEN, "token_type": "bearer"}
            )

        if path == "/api/v1/auth/register":
            payload = json.loads(request.content)
            self.registered.append(payload)
            return httpx.Response(
                201,
                json={
                    "id": "88888888-8888-8888-8888-888888888888",
                    "email": payload["email"],
                    "telegram_id": payload.get("telegram_id"),
                    "created_at": "2026-01-01T00:00:00",
                },
            )

        if self.reject_auth or auth != f"Bearer {TOKEN}":
            return httpx.Response(
                401, json={"detail": "Could not validate credentials"}
            )

        if path == "/api/v1/auth/me":
            return httpx.Response(
                200,
                json={
                    "id": USER_ID,
                    "email": "owner@example.com",
                    "telegram_id": None,
                    "created_at": "2026-01-01T00:00:00",
                },
            )

        if path == "/api/v1/branches/" and request.method == "POST":
            if self.branch_validation_error:
                return httpx.Response(
                    422,
                    json={
                        "detail": [{"loc": ["body", "name"], "msg": "Field required"}]
                    },
                )
            payload = json.loads(request.content)
            branch = {
                "id": "55555555-5555-5555-5555-555555555555",
                "user_id": USER_ID,
                **payload,
            }
            self.branches.append(branch)
            return httpx.Response(201, json=branch)

        if path.startswith("/api/v1/branches/") and request.method == "PATCH":
            branch_id = path.rstrip("/").rsplit("/", 1)[1]
            branch = next((b for b in self.branches if b["id"] == branch_id), None)
            if branch is None:
                return httpx.Response(404, json={"detail": "Branch not found"})
            branch.update(json.loads(request.content))
            return httpx.Response(200, json=branch)

        if path == "/api/v1/branches/":
            return httpx.Response(200, json=self.branches)

        if path.startswith("/api/v1/reviews/") and path.endswith("/status"):
            review_id = path.rstrip("/").split("/")[-2]
            review = next((r for r in self.reviews if r["id"] == review_id), None)
            if review is None:
                return httpx.Response(404, json={"detail": "Review not found"})
            payload = json.loads(request.content)
            review["status"] = payload["status"]
            if payload.get("final_reply") is not None:
                review["final_reply"] = payload["final_reply"]
            return httpx.Response(200, json=review)

        if path == "/api/v1/reviews/":
            branch_id = request.url.params.get("branch_id")
            reviews = self.reviews
            if branch_id:
                reviews = [r for r in reviews if r["branch_id"] == branch_id]
            return httpx.Response(200, json=reviews)

        if path == "/api/v1/subscriptions/current":
            if self.subscription_error is not None:
                return httpx.Response(
                    self.subscription_error, json={"detail": "Internal Server Error"}
                )
            return httpx.Response(200, json=self.subscription)

        if path.startswith("/api/v1/funnel/short-link/"):
            return httpx.Response(200, json={"short_url": "https://short.example/abc"})

        return httpx.Response(404, json={"detail": "Not Found"})



# ---------------------------------------------------------------------------
# Fixtures: mock transport injected into the production API client (tests only)
# ---------------------------------------------------------------------------
@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def app(backend: MockBackend, monkeypatch: pytest.MonkeyPatch):
    streamlit.cache_data.clear()
    real_client = client_module.ReputAPIClient

    def _factory(*args: Any, **kwargs: Any):
        kwargs["transport"] = backend.transport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module, "ReputAPIClient", _factory)
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    yield at
    streamlit.cache_data.clear()


def _login(at: AppTest) -> AppTest:
    at.run()
    at.text_input(key="login_email").input("owner@example.com")
    at.text_input(key="login_password").input("correct-horse")
    at.button(key="login_submit").click().run()
    return at


def _open_page(at: AppTest, page: str) -> AppTest:
    at.radio(key="nav_page").set_value(page).run()
    return at


# ---------------------------------------------------------------------------
# Authentication workflows
# ---------------------------------------------------------------------------
def test_public_page_offers_login_and_registration(app: AppTest) -> None:
    app.run()
    assert not app.exception
    keys = {w.key for w in app.text_input}
    assert {"login_email", "login_password", "reg_email", "reg_password"} <= keys
    assert any("Backend API доступен" in s.value for s in app.success)


def test_login_workflow_signs_in_and_shows_overview(
    app: AppTest, backend: MockBackend
) -> None:
    _login(app)
    assert not app.exception
    assert app.session_state["access_token"] == TOKEN
    assert app.radio(key="nav_page").value == "Обзор"
    metrics = {(m.label, m.value) for m in app.metric}
    assert ("Отзывов всего", "2") in metrics
    assert ("Средний рейтинг", "3.5") in metrics
    assert any("owner@example.com" in c.value for c in app.caption)


def test_login_with_wrong_password_shows_explicit_error(app: AppTest) -> None:
    app.run()
    app.text_input(key="login_email").input("owner@example.com")
    app.text_input(key="login_password").input("wrong-password")
    app.button(key="login_submit").click().run()
    assert any("Ошибка входа" in e.value for e in app.error)
    assert "access_token" not in app.session_state


def test_register_workflow_creates_account_and_signs_in(
    app: AppTest, backend: MockBackend
) -> None:
    app.run()
    app.text_input(key="reg_email").input("fresh@example.com")
    app.text_input(key="reg_password").input("correct-horse")
    app.text_input(key="reg_telegram").input("12345")
    app.button(key="reg_submit").click().run()
    assert not app.exception
    assert app.session_state["access_token"] == TOKEN
    assert backend.registered == [
        {
            "email": "fresh@example.com",
            "password": "correct-horse",
            "telegram_id": 12345,
        }
    ]


def test_register_validates_telegram_id_before_calling_api(
    app: AppTest, backend: MockBackend
) -> None:
    app.run()
    app.text_input(key="reg_email").input("fresh@example.com")
    app.text_input(key="reg_password").input("correct-horse")
    app.text_input(key="reg_telegram").input("not-a-number")
    app.button(key="reg_submit").click().run()
    assert any("Telegram ID должен быть числом" in e.value for e in app.error)
    assert backend.registered == []


def test_logout_clears_session(app: AppTest) -> None:
    _login(app)
    app.button(key="logout_button").click().run()
    assert "access_token" not in app.session_state
    assert any("Backend API доступен" in s.value for s in app.success)


# ---------------------------------------------------------------------------
# CompanyBranch workflows (list / create / configuration)
# ---------------------------------------------------------------------------
def test_branch_page_lists_existing_branch(app: AppTest) -> None:
    _login(app)
    _open_page(app, "Филиалы")
    assert not app.exception
    table = app.dataframe[0].value
    assert table["Название"].tolist() == ["Yandex Moscow"]
    assert table["Платформа"].tolist() == ["YANDEX"]
    # Edit form is prefilled from the existing branch
    assert app.text_input(key=f"branch_edit_name_{BRANCH_ID}").value == "Yandex Moscow"
    # platform_type/platform_url are read-only (backend PATCH does not accept them)
    platform_input = app.text_input(key=f"branch_edit_platform_{BRANCH_ID}")
    assert platform_input.value == "YANDEX"


def test_branch_creation_sends_spec_fields(app: AppTest, backend: MockBackend) -> None:
    _login(app)
    _open_page(app, "Филиалы")
    app.text_input(key="branch_name").input("Кофейня Центр")
    app.text_input(key="branch_platform_url").input("https://yandex.ru/maps/org/coffee")
    app.selectbox(key="branch_platform_type").select("GIS2")
    app.selectbox(key="branch_tone").select("FRIENDLY")
    app.checkbox(key="branch_is_active").set_value(True)
    app.button(key="branch_create_submit").click().run()

    assert not app.exception
    assert any("создан" in s.value for s in app.success)
    posts = [
        r
        for r in backend.requests
        if r.method == "POST" and r.url.path == "/api/v1/branches/"
    ]
    assert len(posts) == 1
    assert json.loads(posts[0].content) == {
        "name": "Кофейня Центр",
        "platform_type": "GIS2",
        "platform_url": "https://yandex.ru/maps/org/coffee",
        "tone_of_voice": "FRIENDLY",
        "is_active": True,
    }
    table = app.dataframe[0].value
    assert "Кофейня Центр" in table["Название"].tolist()


def test_branch_creation_validates_required_fields(
    app: AppTest, backend: MockBackend
) -> None:
    _login(app)
    _open_page(app, "Филиалы")
    app.button(key="branch_create_submit").click().run()
    assert any("Заполните название" in e.value for e in app.error)
    posts = [
        r
        for r in backend.requests
        if r.method == "POST" and r.url.path == "/api/v1/branches/"
    ]
    assert posts == []


def test_branch_edit_sends_only_backend_supported_fields(
    app: AppTest, backend: MockBackend
) -> None:
    _login(app)
    _open_page(app, "Филиалы")
    app.text_input(key=f"branch_edit_name_{BRANCH_ID}").input("Новое название")
    app.selectbox(key=f"branch_edit_tone_{BRANCH_ID}").select("HUMOROUS")
    app.checkbox(key=f"branch_edit_active_{BRANCH_ID}").set_value(False)
    app.button(key=f"branch_edit_submit_{BRANCH_ID}").click().run()

    assert not app.exception
    assert any("обновлён" in s.value for s in app.success)
    patches = [r for r in backend.requests if r.method == "PATCH"]
    assert len(patches) == 1
    payload = json.loads(patches[0].content)
    assert payload == {
        "name": "Новое название",
        "tone_of_voice": "HUMOROUS",
        "is_active": False,
    }
    assert "platform_type" not in payload
    assert "platform_url" not in payload
    # Mock backend state actually changed
    assert backend.branches[0]["name"] == "Новое название"
    assert backend.branches[0]["tone_of_voice"] == "HUMOROUS"
    assert backend.branches[0]["is_active"] is False


# ---------------------------------------------------------------------------
# Review workflows: list, details, existing state transitions
# ---------------------------------------------------------------------------
def test_review_page_shows_details_of_selected_review(app: AppTest) -> None:
    _login(app)
    _open_page(app, "Отзывы")
    assert not app.exception
    table = app.dataframe[0].value
    assert table["Автор"].tolist() == ["Alice", "Bob"]
    # Default selection is the first review — all spec fields are rendered
    markdown = "\n".join(m.value for m in app.markdown)
    assert "Alice" in markdown
    assert "5 / 5" in markdown
    assert "Great place" in markdown
    assert "Спасибо, Alice!" in markdown  # generated_reply
    assert "2026-01-01T10:00:00" in markdown  # created_at
    assert f"`{REVIEW_APPROVED}`" not in markdown  # ids are not invented into UI


def test_review_status_filter_limits_list(app: AppTest) -> None:
    _login(app)
    _open_page(app, "Отзывы")
    app.selectbox(key="rev_status_filter").select("NEW").run()
    table = app.dataframe[0].value
    assert table["Статус"].tolist() == ["NEW"]
    assert app.selectbox(key="rev_select").value == REVIEW_NEW


@pytest.mark.parametrize(
    "status",
    ["NEW", "PENDING_APPROVAL", "APPROVED", "REJECTED", "PUBLISHED"],
)
def test_review_state_transition_calls_backend(
    app: AppTest, backend: MockBackend, status: str
) -> None:
    _login(app)
    _open_page(app, "Отзывы")
    app.selectbox(key=f"review_status_{REVIEW_APPROVED}").select(status)
    app.text_area(key=f"review_reply_{REVIEW_APPROVED}").input(
        "Спасибо, что выбрали нас!"
    )
    app.button(key=f"review_submit_{REVIEW_APPROVED}").click().run()

    assert not app.exception
    patches = [
        r
        for r in backend.requests
        if r.method == "PATCH" and r.url.path.endswith("/status")
    ]
    assert len(patches) == 1
    assert patches[0].url.path == f"/api/v1/reviews/{REVIEW_APPROVED}/status"
    assert json.loads(patches[0].content) == {
        "status": status,
        "final_reply": "Спасибо, что выбрали нас!",
    }
    assert any("переведён в статус" in s.value for s in app.success)
    # Details block reflects the new backend state
    markdown = "\n".join(m.value for m in app.markdown)
    assert f"`{status}`" in markdown
    assert backend.reviews[0]["status"] == status
    assert backend.reviews[0]["final_reply"] == "Спасибо, что выбрали нас!"


def test_review_transition_keeps_final_reply_when_field_cleared(
    app: AppTest, backend: MockBackend
) -> None:
    backend.reviews[0]["final_reply"] = "Existing final reply"
    _login(app)
    _open_page(app, "Отзывы")
    app.text_area(key=f"review_reply_{REVIEW_APPROVED}").input("")
    app.button(key=f"review_submit_{REVIEW_APPROVED}").click().run()

    patches = [
        r
        for r in backend.requests
        if r.method == "PATCH" and r.url.path.endswith("/status")
    ]
    assert len(patches) == 1
    payload = json.loads(patches[0].content)
    assert payload == {"status": "APPROVED"}  # final_reply omitted, not overwritten
    assert backend.reviews[0]["final_reply"] == "Existing final reply"


# ---------------------------------------------------------------------------
# Subscription information rendering
# ---------------------------------------------------------------------------
def test_subscription_page_displays_spec_fields(app: AppTest) -> None:
    _login(app)
    _open_page(app, "Подписка")
    assert not app.exception
    metrics = {(m.label, m.value) for m in app.metric}
    assert ("Статус", "TRIAL") in metrics
    assert ("Триал до (trial_ends_at)", "2026-10-12T00:00:00+00:00") in metrics
    assert ("Оплачено до (paid_until)", "2026-11-12T00:00:00+00:00") in metrics
    assert ("ID платежа (payment_provider_id)", "pay-12345") in metrics


def test_subscription_missing_fields_render_placeholder(
    app: AppTest, backend: MockBackend
) -> None:
    backend.subscription["paid_until"] = None
    backend.subscription["payment_provider_id"] = None
    _login(app)
    _open_page(app, "Подписка")
    metrics = {(m.label, m.value) for m in app.metric}
    assert ("Оплачено до (paid_until)", "—") in metrics
    assert ("ID платежа (payment_provider_id)", "—") in metrics


# ---------------------------------------------------------------------------
# Explicit API error handling (auth / validation / server / network)
# ---------------------------------------------------------------------------
def test_expired_session_shows_auth_error(app: AppTest, backend: MockBackend) -> None:
    _login(app)
    backend.reject_auth = True
    streamlit.cache_data.clear()
    app.run()
    assert any("Сессия недействительна" in e.value for e in app.error)
    assert app.button(key="logout_after_error") is not None


def test_server_error_is_displayed(app: AppTest, backend: MockBackend) -> None:
    _login(app)
    backend.subscription_error = 500
    streamlit.cache_data.clear()
    _open_page(app, "Подписка")
    assert any("Ошибка сервера (500)" in e.value for e in app.error)


def test_validation_error_is_displayed(app: AppTest, backend: MockBackend) -> None:
    _login(app)
    _open_page(app, "Филиалы")
    backend.branch_validation_error = True
    app.text_input(key="branch_name").input("Coffee")
    app.text_input(key="branch_platform_url").input("https://example.com/map")
    app.button(key="branch_create_submit").click().run()
    assert any("Ошибка валидации" in e.value for e in app.error)


def test_offline_backend_shows_connection_error(
    app: AppTest, backend: MockBackend
) -> None:
    backend.offline = True
    app.run()
    assert any("Backend API недоступен" in e.value for e in app.error)
    assert app.button(key="retry_after_error") is not None




