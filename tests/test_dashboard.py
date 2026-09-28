"""Stage 4 tests: Streamlit dashboard client/metrics (part 1: client)."""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

from reput_ai.dashboard.client import DashboardAPIError, ReputAPIClient
from reput_ai.dashboard.metrics import compute_kpis, filter_reviews, rating_distribution

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "src" / "reput_ai" / "dashboard" / "app.py"


def make_transport() -> httpx.MockTransport:
    """Mock backend API implementing the endpoints used by the dashboard."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("Authorization", "")

        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "app": "ReputationAI"})

        if path == "/api/v1/auth/login":
            body = dict(httpx.QueryParams(request.content.decode()))
            if body.get("password") != "correct-horse":
                return httpx.Response(401, json={"detail": "Incorrect email or password"})
            return httpx.Response(200, json={"access_token": "jwt-token", "token_type": "bearer"})

        if path == "/api/v1/auth/register":
            payload = json.loads(request.content)
            if payload.get("email") == "owner@example.com":
                return httpx.Response(400, json={"detail": "Email already registered"})
            return httpx.Response(
                201,
                json={
                    "id": "88888888-8888-8888-8888-888888888888",
                    "email": payload.get("email"),
                    "telegram_id": payload.get("telegram_id"),
                    "created_at": "2026-01-01T00:00:00",
                },
            )

        if auth != "Bearer jwt-token":
            return httpx.Response(401, json={"detail": "Could not validate credentials"})

        if path == "/api/v1/auth/me":
            return httpx.Response(200, json={"email": "owner@example.com"})
        if path == "/api/v1/branches/" and request.method == "POST":
            payload = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "id": "55555555-5555-5555-5555-555555555555",
                    "user_id": "99999999-9999-9999-9999-999999999999",
                    **payload,
                },
            )
        if path.startswith("/api/v1/branches/") and request.method == "PATCH":
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": path.rstrip("/").rsplit("/", 1)[1],
                    "user_id": "99999999-9999-9999-9999-999999999999",
                    "name": "Yandex Moscow",
                    "platform_type": "YANDEX",
                    "platform_url": "https://yandex.ru/maps/org/example",
                    "tone_of_voice": "OFFICIAL",
                    "is_active": True,
                    **payload,
                },
            )
        if path == "/api/v1/branches/":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "name": "Yandex Moscow",
                        "platform_type": "YANDEX",
                        "platform_url": "https://yandex.ru/maps/org/example",
                        "tone_of_voice": "OFFICIAL",
                        "is_active": True,
                        "user_id": "99999999-9999-9999-9999-999999999999",
                    }
                ],
            )
        if path.startswith("/api/v1/reviews/") and path.endswith("/status"):
            payload = json.loads(request.content)
            if payload.get("status") not in {
                "NEW",
                "PENDING_APPROVAL",
                "APPROVED",
                "REJECTED",
                "PUBLISHED",
            }:
                return httpx.Response(
                    422, json={"detail": [{"msg": "Input should be a valid status"}]}
                )
            review = {
                "branch_id": "11111111-1111-1111-1111-111111111111",
                "external_id": "r1",
                "author_name": "Alice",
                "rating": 5,
                "text": "Great place",
                "id": path.rstrip("/").split("/")[-2],
                "generated_reply": None,
                "final_reply": None,
                "status": payload["status"],
                "created_at": "2026-01-01T10:00:00",
            }
            if "final_reply" in payload:
                review["final_reply"] = payload["final_reply"]
            return httpx.Response(200, json=review)

        if path == "/api/v1/reviews/":
            return httpx.Response(
                200,
                json=[
                    {
                        "branch_id": "11111111-1111-1111-1111-111111111111",
                        "external_id": "r1",
                        "author_name": "Alice",
                        "rating": 5,
                        "text": "Great place",
                        "id": "22222222-2222-2222-2222-222222222222",
                        "status": "APPROVED",
                        "created_at": "2026-01-01T10:00:00",
                    },
                    {
                        "branch_id": "11111111-1111-1111-1111-111111111111",
                        "external_id": "r2",
                        "author_name": "Bob",
                        "rating": 2,
                        "text": "Too slow",
                        "id": "33333333-3333-3333-3333-333333333333",
                        "status": "NEW",
                        "created_at": "2026-01-02T10:00:00",
                    },
                ],
            )
        if path == "/api/v1/subscriptions/current":
            return httpx.Response(
                200,
                json={
                    "status": "TRIAL",
                    "trial_ends_at": "2026-01-15T00:00:00",
                    "paid_until": None,
                    "id": "44444444-4444-4444-4444-444444444444",
                    "user_id": "99999999-9999-9999-9999-999999999999",
                    "payment_provider_id": None,
                },
            )
        if path == "/api/v1/funnel/short-link/11111111-1111-1111-1111-111111111111":
            return httpx.Response(200, json={"short_url": "https://short.example/abc"})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def api() -> ReputAPIClient:
    client = ReputAPIClient(base_url="http://api.test", transport=make_transport())
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def test_health(api: ReputAPIClient) -> None:
    assert api.health() == {"status": "ok", "app": "ReputationAI"}


def test_login_returns_token(api: ReputAPIClient) -> None:
    assert api.login("owner@example.com", "correct-horse") == "jwt-token"


def test_login_rejects_bad_password(api: ReputAPIClient) -> None:
    with pytest.raises(DashboardAPIError) as exc:
        api.login("owner@example.com", "wrong")
    assert exc.value.status_code == 401


def test_authenticated_endpoints(api: ReputAPIClient) -> None:
    token = api.login("owner@example.com", "correct-horse")
    assert api.me(token)["email"] == "owner@example.com"
    assert len(api.list_branches(token)) == 1

    reviews = api.list_reviews(token)
    assert [r["rating"] for r in reviews] == [5, 2]

    # Optional branch filter is passed as a query parameter.
    filtered = api.list_reviews(token, branch_id="11111111-1111-1111-1111-111111111111")
    assert len(filtered) == 2

    subscription = api.get_subscription(token)
    assert subscription["status"] == "TRIAL"

    link = api.get_short_link(token, "11111111-1111-1111-1111-111111111111")
    assert link["short_url"] == "https://short.example/abc"


def test_unauthenticated_request_fails(api: ReputAPIClient) -> None:
    with pytest.raises(DashboardAPIError) as exc:
        api.list_reviews("bad-token")
    assert exc.value.status_code == 401


def test_unreachable_api_raises() -> None:
    bad = ReputAPIClient(
        base_url="http://api.test",
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
        ),
    )
    with pytest.raises(DashboardAPIError) as exc:
        bad.health()
    assert exc.value.status_code == 0
    bad.close()


# ---------------------------------------------------------------------------
# Client: registration, branch CRUD, review transitions (frontend API layer)
# ---------------------------------------------------------------------------
def test_register_returns_created_user(api: ReputAPIClient) -> None:
    user = api.register("new-owner@example.com", "secret-pass", 777)
    assert user["email"] == "new-owner@example.com"
    assert user["telegram_id"] == 777


def test_register_without_telegram_id(api: ReputAPIClient) -> None:
    user = api.register("another@example.com", "secret-pass")
    assert user["email"] == "another@example.com"
    assert user["telegram_id"] is None


def test_register_duplicate_email_rejected(api: ReputAPIClient) -> None:
    with pytest.raises(DashboardAPIError) as exc:
        api.register("owner@example.com", "secret-pass")
    assert exc.value.status_code == 400
    assert "already registered" in exc.value.detail


def test_create_branch_sends_spec_fields(api: ReputAPIClient) -> None:
    branch = api.create_branch(
        "jwt-token",
        name="Coffee Downtown",
        platform_type="GIS2",
        platform_url="https://2gis.ru/foobar",
        tone_of_voice="FRIENDLY",
        is_active=False,
    )
    assert branch["id"] == "55555555-5555-5555-5555-555555555555"
    assert branch["platform_type"] == "GIS2"
    assert branch["tone_of_voice"] == "FRIENDLY"
    assert branch["is_active"] is False


def test_update_branch_sends_only_supported_fields() -> None:
    """PATCH /branches/{id} accepts only name/tone_of_voice/is_active (BranchUpdate)."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=captured["json"])

    client = ReputAPIClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    try:
        client.update_branch(
            "jwt-token",
            "11111111-1111-1111-1111-111111111111",
            name="Renamed",
            tone_of_voice="HUMOROUS",
            is_active=False,
        )
    finally:
        client.close()
    assert captured["json"] == {
        "name": "Renamed",
        "tone_of_voice": "HUMOROUS",
        "is_active": False,
    }


def test_update_review_status_with_final_reply(api: ReputAPIClient) -> None:
    updated = api.update_review_status(
        "jwt-token",
        "22222222-2222-2222-2222-222222222222",
        "APPROVED",
        "Спасибо за отзыв!",
    )
    assert updated["status"] == "APPROVED"
    assert updated["final_reply"] == "Спасибо за отзыв!"


def test_update_review_status_omits_final_reply_when_not_provided() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=captured["json"])

    client = ReputAPIClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    try:
        client.update_review_status("jwt-token", "review-1", "REJECTED")
    finally:
        client.close()
    assert captured["json"] == {"status": "REJECTED"}


def test_update_review_status_rejects_unknown_status(api: ReputAPIClient) -> None:
    with pytest.raises(DashboardAPIError) as exc:
        api.update_review_status("jwt-token", "review-1", "PUBLISHED_MANUALLY")
    assert exc.value.status_code == 422


def test_mutations_require_valid_token(api: ReputAPIClient) -> None:
    with pytest.raises(DashboardAPIError) as exc:
        api.update_branch("bad-token", "branch-1", name="X")
    assert exc.value.status_code == 401

    with pytest.raises(DashboardAPIError) as exc:
        api.update_review_status("bad-token", "review-1", "APPROVED")
    assert exc.value.status_code == 401

    with pytest.raises(DashboardAPIError) as exc:
        api.create_branch(
            "bad-token",
            name="X",
            platform_type="YANDEX",
            platform_url="https://example.com",
            tone_of_voice="OFFICIAL",
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Dashboard option lists must match the specification / backend enums
# ---------------------------------------------------------------------------
def test_dashboard_options_match_specification() -> None:
    from reput_ai.dashboard.options import (
        PLATFORM_TYPES,
        REVIEW_STATUSES,
        SUBSCRIPTION_STATUSES,
        TONE_OF_VOICE_OPTIONS,
    )

    assert PLATFORM_TYPES == ["YANDEX", "GIS2", "GOOGLE", "AVITO"]
    assert TONE_OF_VOICE_OPTIONS == ["OFFICIAL", "FRIENDLY", "HUMOROUS"]
    assert REVIEW_STATUSES == [
        "NEW",
        "PENDING_APPROVAL",
        "APPROVED",
        "REJECTED",
        "PUBLISHED",
    ]
    assert SUBSCRIPTION_STATUSES == ["TRIAL", "ACTIVE", "PAST_DUE", "CANCELED"]


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
REVIEWS = [
    {"rating": 5, "status": "APPROVED", "branch_id": "b1"},
    {"rating": 4, "status": "APPROVED", "branch_id": "b1"},
    {"rating": 2, "status": "NEW", "branch_id": "b2"},
    {"rating": 1, "status": "PENDING_APPROVAL", "branch_id": "b2"},
    {"rating": 3, "status": "NEW", "branch_id": "b1"},
]


def test_compute_kpis() -> None:
    kpi = compute_kpis(REVIEWS)
    assert kpi["total"] == 5
    assert kpi["avg_rating"] == 3.0
    assert kpi["negative"] == 3  # 1-3 stars
    assert kpi["positive"] == 2  # 4-5 stars
    assert kpi["pending"] == 2


def test_compute_kpis_empty() -> None:
    kpi = compute_kpis([])
    assert kpi == {"total": 0, "avg_rating": 0.0, "negative": 0, "positive": 0, "pending": 0}


def test_rating_distribution() -> None:
    dist = rating_distribution(REVIEWS)
    assert dist == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1}


def test_filter_reviews_by_branch_and_rating() -> None:
    assert len(filter_reviews(REVIEWS, branch_ids={"b1"})) == 3
    assert len(filter_reviews(REVIEWS, max_rating=3)) == 3
    assert len(filter_reviews(REVIEWS, min_rating=4)) == 2
    assert len(filter_reviews(REVIEWS, min_rating=3, max_rating=4)) == 2
    assert len(filter_reviews(REVIEWS, status="NEW")) == 2


# ---------------------------------------------------------------------------
# Headless boot of the Streamlit UI (no frontend automation, no browser)
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/_stcore/health", timeout=2
        ) as response:
            return response.status == 200 and response.read().decode() == "ok"
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def test_streamlit_dashboard_boots_headless() -> None:
    """The dashboard must serve its health endpoint when started headless."""
    if importlib.util.find_spec("streamlit") is None:
        pytest.skip("streamlit is not installed")

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP_PATH),
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"streamlit exited early with code {proc.returncode}")
            if _health_ok(port):
                break
            time.sleep(0.5)
        else:
            pytest.fail("streamlit health endpoint did not become ready in 60s")
        assert _health_ok(port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)



