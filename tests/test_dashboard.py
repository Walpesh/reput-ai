"""Stage 4 tests: Streamlit dashboard client/metrics (part 1: client)."""

from __future__ import annotations

import importlib.util
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

        if auth != "Bearer jwt-token":
            return httpx.Response(401, json={"detail": "Could not validate credentials"})

        if path == "/api/v1/auth/me":
            return httpx.Response(200, json={"email": "owner@example.com"})
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



