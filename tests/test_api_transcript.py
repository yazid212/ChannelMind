from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

from api.main import app
from api.security import issue_workspace_token


def test_transcript_endpoint_returns_only_authenticated_workspaces_video():
    identity, token = issue_workspace_token()
    other, _ = issue_workspace_token()
    embedder = Mock()
    embedder.transcript_chunks.return_value = [Document(
        page_content="The computer reads input and produces output.",
        metadata={"title": "Computer basics", "start_time": 65, "url": "https://youtube.com/watch?v=abcdefghijk&t=65s"},
    )]
    path = f"/api/v1/workspaces/{identity.workspace_id}/videos/abcdefghijk/transcript"
    with patch("api.main.Embedder", return_value=embedder) as constructor:
        client = TestClient(app)
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        denied = client.get(
            f"/api/v1/workspaces/{other.workspace_id}/videos/abcdefghijk/transcript",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["chunks"][0]["text"] == "The computer reads input and produces output."
    assert response.json()["chunks"][0]["timestamp"] == "01:05"
    assert denied.status_code == 404
    constructor.assert_called_once_with("youtube_transcripts", identity.workspace_id)
    embedder.transcript_chunks.assert_called_once_with("abcdefghijk")
