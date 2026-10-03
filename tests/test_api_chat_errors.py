from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError

from api.main import app
from api.security import issue_workspace_token


def test_chat_explains_provider_connection_failure():
    identity, token = issue_workspace_token()
    failure = APIConnectionError(
        request=httpx.Request("POST", "https://example.invalid/chat"),
    )
    with patch("api.main.YouTubeRAG", side_effect=failure):
        response = TestClient(app).post(
            f"/api/v1/workspaces/{identity.workspace_id}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "What does the video compare?", "history": []},
        )

    assert response.status_code == 503
    assert "cannot reach the LLM provider" in response.json()["detail"]
