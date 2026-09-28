"""Synchronous HTTP client for the ReputationAI backend API (used by the Streamlit dashboard).

The dashboard is a thin consumer of the existing REST API: it never talks to the
database directly, which keeps the "Headless First" contract of the RFP.
"""

from __future__ import annotations

from typing import Any, Self
from uuid import UUID

import httpx

from reput_ai.config import settings

API_PREFIX = settings.API_V1_STR


class DashboardAPIError(Exception):
    """Raised when the backend API returns an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


class ReputAPIClient:
    """Small synchronous client around httpx for the dashboard UI.

    A custom ``transport`` (e.g. ``httpx.MockTransport``) can be injected for tests.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.DASHBOARD_API_BASE_URL).rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout or settings.DASHBOARD_API_TIMEOUT_SECONDS,
            transport=transport,
            headers={"User-Agent": "ReputationAI-Dashboard/0.1"},
        )

    # -- context manager -------------------------------------------------
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- internals -------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> Any:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:  # network / timeout problems
            raise DashboardAPIError(0, f"Cannot reach API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise DashboardAPIError(response.status_code, str(detail))
        if not response.content:
            return None
        return response.json()

    # -- endpoints -------------------------------------------------------
    def health(self) -> dict[str, str]:
        """GET /health — unauthenticated liveness probe."""
        return self._request("GET", "/health")

    def login(self, email: str, password: str) -> str:
        """POST /api/v1/auth/login — returns the JWT access token."""
        data = self._request(
            "POST",
            f"{API_PREFIX}/auth/login",
            data={"username": email, "password": password},
        )
        return data["access_token"]

    def me(self, token: str) -> dict[str, Any]:
        return self._request("GET", f"{API_PREFIX}/auth/me", token=token)

    def list_branches(self, token: str) -> list[dict[str, Any]]:
        return self._request("GET", f"{API_PREFIX}/branches/", token=token)

    def list_reviews(self, token: str, branch_id: str | UUID | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if branch_id is not None:
            params["branch_id"] = str(branch_id)
        return self._request("GET", f"{API_PREFIX}/reviews/", token=token, params=params)

    def get_subscription(self, token: str) -> dict[str, Any]:
        return self._request("GET", f"{API_PREFIX}/subscriptions/current", token=token)

    def get_short_link(self, token: str, branch_id: str | UUID) -> dict[str, Any]:
        return self._request(
            "GET", f"{API_PREFIX}/funnel/short-link/{branch_id}", token=token
        )
