from dataclasses import replace
from concurrent.futures import Future
import time
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from pydantic import ValidationError
from rq.job import JobStatus

from api.schemas import IngestionRequest
from api import main as api_main
from api.jobs import JobManager
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
        "https://youtube.com/watch?v=aaaaaaaaaaa",
        "https://youtube.com/playlist?list=PL" + "x" * 32,
    ],
)
def test_ingestion_rejects_non_youtube_targets(channel):
    with pytest.raises(ValidationError):
        IngestionRequest(channel=channel)


def test_playlist_ingestion_requires_workspace_and_valid_url():
    with patch.object(api_main.limiter, "check"), patch.object(
        api_main.jobs, "enqueue_playlist", return_value="playlist-job",
    ) as enqueue, TestClient(api_main.app) as client:
        created = client.post("/api/v1/workspaces").json()
        client.headers.update({"Authorization": f"Bearer {created['token']}"})
        playlist = "https://www.youtube.com/playlist?list=PL" + "x" * 32
        response = client.post(f"/api/v1/workspaces/{created['workspace_id']}/playlists", json={"playlist": playlist})
        assert response.status_code == 202
        assert response.json()["job_id"] == "playlist-job"
        enqueue.assert_called_once()
        assert enqueue.call_args.args[1] == "PL" + "x" * 32
        invalid = client.post(f"/api/v1/workspaces/{created['workspace_id']}/playlists", json={
            "playlist": "https://evil.example/playlist?list=PL" + "x" * 32,
        })
        assert invalid.status_code == 422
        other = client.post("/api/v1/workspaces/not-my-workspace/playlists", json={"playlist": playlist})
        assert other.status_code == 404


def test_repeated_playlist_request_reuses_active_local_job():
    manager = JobManager()
    manager._queue = None
    manager._redis = None
    pending = Future()
    with patch.object(manager._executor, "submit", return_value=pending) as submit:
        first = manager.enqueue_playlist("workspace", "PL" + "x" * 32, int(time.time()) + 300)
        second = manager.enqueue_playlist("workspace", "PL" + "x" * 32, int(time.time()) + 300)
    assert first == second
    submit.assert_called_once()


@pytest.mark.parametrize("rq_status", [JobStatus.STARTED, JobStatus.FINISHED, JobStatus.FAILED])
def test_redis_job_status_uses_rq_enum_value(rq_status):
    manager = JobManager()
    manager._redis = object()
    job = SimpleNamespace(
        meta={"workspace_id": "workspace"},
        get_status=lambda refresh: rq_status,
        result={"ok": True},
    )
    with patch("rq.job.Job.fetch", return_value=job):
        result = manager.status("job", "workspace")
    assert result["status"] == rq_status.value


def test_channel_url_variants_reuse_active_workspace_job():
    manager = JobManager()
    manager._queue = None
    manager._redis = None
    pending = Future()
    with patch.object(manager._executor, "submit", return_value=pending) as submit:
        first = manager.enqueue("workspace", "@SEB", int(time.time()) + 300)
        second = manager.enqueue(
            "workspace", "https://www.youtube.com/channel/UCK3inMNRNAVUleEbpDU1k2g",
            int(time.time()) + 300,
        )
    assert first == second
    submit.assert_called_once()


def test_playlist_route_runs_caption_free_job_and_scopes_status(monkeypatch):
    class ProgressStore:
        def __init__(self):
            self.values = {}

        def put(self, key, value, expires_at):
            self.values[key] = value

        def get(self, key):
            return self.values.get(key)

    progress = ProgressStore()
    manager = JobManager()
    manager._queue = None
    manager._redis = None
    monkeypatch.setattr(api_main, "jobs", manager)
    monkeypatch.setattr("api.tasks.get_export_store", lambda: progress)
    monkeypatch.setattr("api.jobs.get_export_store", lambda: progress)
    video_id = "a" * 11
    with patch.object(api_main.limiter, "check"), patch("api.tasks.YouTubeExtractor") as extractor_class, patch(
        "api.tasks.fetch_video_transcript", return_value={
            "video_id": video_id, "title": "No captions", "segments": [{"text": "recognized speech", "start": 1}],
            "transcript_source": "local_speech_recognition",
        },
    ), patch("api.tasks.Embedder") as embedder_class, TestClient(api_main.app) as client:
        extractor = extractor_class.return_value
        extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
            "items": [{"snippet": {"title": "One lesson"}}],
        }
        extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [{"contentDetails": {"videoId": video_id}, "snippet": {"title": "No captions"}}],
        }
        embedder_class.return_value._create_documents.return_value = [object()]
        first = client.post("/api/v1/workspaces").json()
        second = client.post("/api/v1/workspaces").json()
        playlist = "https://www.youtube.com/playlist?list=PL" + "x" * 32
        started = client.post(f"/api/v1/workspaces/{first['workspace_id']}/playlists",
                              json={"playlist": playlist},
                              headers={"Authorization": f"Bearer {first['token']}"})
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        for _ in range(100):
            result = client.get(f"/api/v1/jobs/{job_id}",
                                headers={"Authorization": f"Bearer {first['token']}"}).json()
            if result["status"] == "finished":
                break
            time.sleep(0.01)
        assert result["status"] == "finished"
        assert result["result"]["coverage"]["indexed"] == 1
        assert embedder_class.return_value.ingest.call_count == 1
        hidden = client.get(f"/api/v1/jobs/{job_id}",
                            headers={"Authorization": f"Bearer {second['token']}"})
        assert hidden.status_code == 404


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
