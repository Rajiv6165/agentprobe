"""Tests for API key authentication."""

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.asyncio
async def test_auth_enforced_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "API_KEY", "secret-test-token-12345")

    # Request without header fails with 401
    resp_no_header = await client.get("/v1/projects")
    assert resp_no_header.status_code == 401
    err_body = resp_no_header.json()
    assert err_body["error"]["code"] == "UNAUTHORIZED"

    # Request with invalid header fails with 401
    resp_invalid = await client.get("/v1/projects", headers={"X-AgentProbe-Key": "wrong-key"})
    assert resp_invalid.status_code == 401

    # Request with valid key succeeds with 200
    resp_valid = await client.get(
        "/v1/projects", headers={"X-AgentProbe-Key": "secret-test-token-12345"}
    )
    assert resp_valid.status_code == 200


@pytest.mark.asyncio
async def test_auth_open_when_no_api_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "API_KEY", None)

    resp = await client.get("/v1/projects")
    assert resp.status_code == 200
