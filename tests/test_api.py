import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "ReputationAI"


@pytest.mark.asyncio
async def test_auth_registration_and_login(client: AsyncClient):
    # Register
    reg_response = await client.post(
        "/api/v1/auth/register",
        json={"email": "tester@example.com", "password": "securepassword123", "telegram_id": 12345678},
    )
    assert reg_response.status_code == 201
    user_data = reg_response.json()
    assert user_data["email"] == "tester@example.com"
    assert "id" in user_data

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        data={"username": "tester@example.com", "password": "securepassword123"},
    )
    assert login_response.status_code == 200
    token_data = login_response.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # Access current user
    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "tester@example.com"
