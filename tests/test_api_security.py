from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from pydantic import ValidationError

from api.schemas import IngestionRequest
from api import main as api_main
from api.security import client_address, issue_workspace_token, verify_workspace_token
from api.settings import Settings


TEST_SETTINGS = Settings(
    app_secret="test-secret-that-is-long-enough-for-signing",
    workspace_ttl_seconds=300,
)


def test_workspace_capability_round_trip_and_tamper_rejection():
    with patch("api.security.get_settings", return_value=TEST_SETTINGS):
        identity, token = issue_workspace_token()
        verified = verify_workspace_token(token)
        assert verified.workspace_id == identity.workspace_id

        payload, signature = token.split(".", 1)
        tampered = f"{payload}.{signature[:-1]}x"
        with pytest.raises(HTTPException) as error:
            verify_workspace_token(tampered)
        assert error.value.status_code == 401


@pytest.mark.parametrize(
    "channel",
    [
        "http://youtube.com/@channel",
        "https://evil.example/@channel",
        "file:///etc/passwd",
        "just a search query",
    ],
)
def test_ingestion_rejects_non_youtube_targets(channel):
    with pytest.raises(ValidationError):
        IngestionRequest(channel=channel)


def test_production_settings_fail_closed_without_shared_services():
    settings = replace(
        TEST_SETTINGS,
        app_env="production",
        redis_url=None,
        allowed_origins=("https://example.com",),
    )
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        settings.validate()


def test_production_settings_reject_local_origins_and_missing_provider_key(monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "test-qdrant-key")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-youtube-key")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = replace(
        TEST_SETTINGS,
        app_env="production",
        redis_url="redis://redis.internal:6379/0",
        allowed_origins=("http://localhost:3000",),
        allowed_hosts=("localhost",),
    )
    with pytest.raises(RuntimeError) as error:
        settings.validate()
    assert "OPENROUTER_API_KEY" in str(error.value)
    assert "ALLOWED_ORIGINS" in str(error.value)
    assert "ALLOWED_HOSTS" in str(error.value)


def test_production_settings_accept_explicit_hosts_and_services(monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "test-qdrant-key")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-youtube-key")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-llm-key")
    settings = replace(
        TEST_SETTINGS,
        app_env="production",
        redis_url="redis://redis.internal:6379/0",
        allowed_origins=("https://app.example.com",),
        allowed_hosts=("api.example.com",),
    )
    settings.validate()


def test_forwarded_address_requires_a_trusted_proxy():
    def request(peer):
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/healthz",
            "headers": [(b"x-forwarded-for", b"198.51.100.10, 203.0.113.7")],
            "client": (peer, 12345),
        })

    settings = replace(TEST_SETTINGS, trust_proxy_headers=True, trusted_proxy_cidrs=("10.0.0.0/8",))
    with patch("api.security.get_settings", return_value=settings):
        assert client_address(request("192.0.2.1")) == "192.0.2.1"
        assert client_address(request("10.1.2.3")) == "203.0.113.7"

        railway_request = Request({
            "type": "http",
            "method": "GET",
            "path": "/healthz",
            "headers": [(b"x-real-ip", b"198.51.100.22")],
            "client": ("10.1.2.3", 12345),
        })
        assert client_address(railway_request) == "198.51.100.22"


def test_production_readiness_fails_when_local_speech_model_is_missing():
    production = replace(TEST_SETTINGS, app_env="production")
    with patch.object(api_main, "settings", production), patch.object(
        api_main, "LOCAL_WHISPER_MODEL", "/channelmind-model-does-not-exist"
    ):
        response = TestClient(api_main.app).get("/readyz")
    assert response.status_code == 503
