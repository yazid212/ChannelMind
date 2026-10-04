import io
import json
import time
import uuid
import zipfile
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import channel_export, export_routes, youtube_authorized
from api.channel_export import collect_channel, parse_vtt, run_key
from api.export_routes import create_export_router
from api.export_store import ExportStore
from api.security import WorkspaceIdentity, require_workspace, verify_workspace_token
from api.settings import Settings
from api.youtube_authorized import SCOPE, YouTubeAccessError

CHANNEL_ID = "UC" + "a" * 22
VTT = "WEBVTT\n\n00:01.500 --> 00:03.000\n<v Speaker>Hello &amp; welcome</v>\n\n2\n00:03.000 --> 00:05.250 align:start\nA useful idea.\n"


@pytest.fixture
def export_system(monkeypatch):
    store = ExportStore(secret="test-key-that-is-long-and-not-a-real-secret")
    for module in (channel_export, export_routes, youtube_authorized):
        monkeypatch.setattr(module, "get_export_store", lambda: store)
    identity = WorkspaceIdentity(str(uuid.uuid4()), int(time.time()) + 7200)
    jobs = Mock()
    app = FastAPI()
    app.include_router(create_export_router(jobs, Mock()))
    app.dependency_overrides[require_workspace] = lambda: identity
    youtube = Mock()
    youtube.channels.return_value = [{"id": CHANNEL_ID, "title": "A creator", "uploads": "UUplaylist", "handle": "@creator"}]
    monkeypatch.setattr(export_routes, "AuthorizedYouTube", lambda _: youtube)
    monkeypatch.setattr(channel_export, "AuthorizedYouTube", lambda _: youtube)
    return TestClient(app), app, store, identity, youtube, jobs


def playlist_page(ids, next_page=None):
    result = {"items": [{
        "contentDetails": {"videoId": video_id},
        "snippet": {"title": f"A title {video_id}", "description": "Details"},
    } for video_id in ids]}
    if next_page:
        result["nextPageToken"] = next_page
    return result


def test_caption_parser_keeps_unicode_timestamps_and_removes_markup():
    segments = parse_vtt(VTT + "\nNOTE a comment\nignore\n\n00:05.250 --> 00:08.000\nمرحبا <b>bonjour</b>\n")
    assert segments[0] == {"text": "Hello & welcome", "start": 1.5, "end": 3.0, "duration": 1.5}
    assert segments[1]["start"] == 3
    assert segments[2]["text"] == "مرحبا bonjour"


def test_job_resumes_after_quota_without_redownloading_and_exports_full_zip(export_system):
    client, _, store, identity, youtube, jobs = export_system
    youtube.video_page.side_effect = [playlist_page(["a" * 11, "b" * 11], "page2"), playlist_page(["c" * 11])]
    youtube.caption.side_effect = [
        (VTT, "en", "standard"),
        YouTubeAccessError("quota", "Quota exhausted"),
        YouTubeAccessError("no_captions", "Missing captions"),
        (VTT, "fr", "ASR"),
    ]
    response = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID})
    assert response.status_code == 202
    run_id = response.json()["id"]
    collect_channel(identity.workspace_id, run_id)
    paused = client.get(f"/api/v1/exports/{run_id}").json()
    assert (paused["status"], paused["total"], paused["exported"]) == ("paused", 3, 1)
    assert paused["coverage"]["pending"] == 2
    assert paused["error_code"] == "quota"
    partial = client.get(f"/api/v1/exports/{run_id}/download?format=json").json()
    assert partial["complete"] is False
    assert client.post(f"/api/v1/exports/{run_id}/resume").status_code == 202
    collect_channel(identity.workspace_id, run_id)
    finished = client.get(f"/api/v1/exports/{run_id}").json()
    assert (finished["status"], finished["processed"], finished["exported"], finished["skipped"]) == ("finished", 3, 2, 1)
    assert finished["coverage"]["transcribed"] == 2
    assert finished["coverage"]["unavailable"] == 1
    assert finished["coverage"]["pending"] == 0
    assert finished["coverage"]["reasons"] == [{"reason": "no_captions", "count": 1}]
    assert [call.args[0] for call in youtube.caption.call_args_list] == ["a" * 11, "b" * 11, "b" * 11, "c" * 11]
    assert youtube.video_page.call_count == 2
    assert "segments" not in json.dumps(finished)
    response = client.get(f"/api/v1/exports/{run_id}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "transcripts/aaaaaaaaaaa.txt" in archive.namelist()
        bundle = json.loads(archive.read("channel.json"))
        assert bundle["complete"] is True
        assert bundle["videos"][0]["segments"][0]["start"] == 1.5
        assert bundle["videos"][1]["reason"] == "no_captions"
        assert "[00:00:01] Hello & welcome" in archive.read("channel.txt").decode()


def test_exports_cannot_be_read_or_resumed_by_another_workspace(export_system):
    client, app, _, identity, _, _ = export_system
    run_id = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()["id"]
    app.dependency_overrides[require_workspace] = lambda: WorkspaceIdentity(str(uuid.uuid4()), identity.expires_at)
    for suffix in ("", "/download", "/issues"):
        assert client.get(f"/api/v1/exports/{run_id}{suffix}").status_code == 404
    for suffix in ("resume", "cancel", "index", "handoff"):
        assert client.post(f"/api/v1/exports/{run_id}/{suffix}").status_code == 404


def test_large_export_report_accounts_for_every_video_and_paginates_issues(export_system):
    client, _, store, identity, _, _ = export_system
    videos = []
    for number in range(200):
        video = {"video_id": f"{number:011d}", "title": f"Video {number}"}
        if number < 43:
            video["status"] = "exported"
        elif number < 143:
            video.update(status="skipped", reason="no_captions")
        elif number < 150:
            video.update(status="skipped", reason="caption_parse_error")
        else:
            video["status"] = "pending"
        videos.append(video)
    run_id = str(uuid.uuid4())
    run = {"id": run_id, "workspace_id": identity.workspace_id,
           "channel": {"id": CHANNEL_ID, "title": "Large channel"},
           "status": "paused", "message": "Resume to continue", "catalog_complete": True,
           "expires_at": identity.expires_at, "videos": videos,
           "indexed_video_ids": [video["video_id"] for video in videos[:10]]}
    store.put(run_key(identity.workspace_id, run_id), run, identity.expires_at)

    report = client.get(f"/api/v1/exports/{run_id}").json()
    coverage = report["coverage"]
    assert {key: coverage[key] for key in ("discovered", "transcribed", "unavailable", "failed", "pending", "indexed")} == {
        "discovered": 200, "transcribed": 43, "unavailable": 100,
        "failed": 7, "pending": 50, "indexed": 10,
    }
    assert coverage["discovered"] == sum(coverage[key] for key in ("transcribed", "unavailable", "failed", "pending"))
    assert coverage["reasons"] == [
        {"reason": "no_captions", "count": 100},
        {"reason": "caption_parse_error", "count": 7},
    ]
    assert report["issue_total"] == 157
    assert len(report["issues"]) == 25
    page = client.get(f"/api/v1/exports/{run_id}/issues?offset=100&limit=50").json()
    assert page["total"] == 157
    assert len(page["items"]) == 50
    assert page["items"][0]["outcome"] == "failed"
    assert page["items"][-1]["outcome"] == "pending"
    all_issues = [item for offset in range(0, 157, 50)
                  for item in client.get(f"/api/v1/exports/{run_id}/issues?offset={offset}&limit=50").json()["items"]]
    assert len(all_issues) == len({item["video_id"] for item in all_issues}) == 157
    assert client.get(f"/api/v1/exports/{run_id}/issues?outcome=unavailable").json()["total"] == 100
    assert client.get(f"/api/v1/exports/{run_id}/issues?limit=101").status_code == 422


def test_export_requires_account_channel_and_download_rejects_inflight_job(export_system):
    client, _, _, _, _, _ = export_system
    assert client.post("/api/v1/exports", json={"channel_id": "UC" + "b" * 22}).status_code == 403
    run_id = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()["id"]
    assert client.get(f"/api/v1/exports/{run_id}/download").status_code == 409


def test_cancel_preserves_completed_transcripts(export_system):
    client, _, store, identity, youtube, _ = export_system
    youtube.video_page.return_value = playlist_page(["a" * 11])
    run_id = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()["id"]
    client.post(f"/api/v1/exports/{run_id}/cancel")
    collect_channel(identity.workspace_id, run_id)
    assert client.get(f"/api/v1/exports/{run_id}").json()["status"] == "cancelled"
    youtube.caption.assert_not_called()


def test_handoff_is_single_use_and_only_for_indexed_workspace(export_system):
    client, _, store, identity, _, _ = export_system
    run_id = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()["id"]
    assert client.post(f"/api/v1/exports/{run_id}/handoff").status_code == 409
    run = store.get(run_key(identity.workspace_id, run_id))
    run.update(indexed=True, metrics={"catalog": 5, "videos": 3, "chunks": 20})
    store.put(run_key(identity.workspace_id, run_id), run, identity.expires_at)
    code = client.post(f"/api/v1/exports/{run_id}/handoff").json()["code"]
    result = client.post("/api/v1/export-handoffs/claim", json={"code": code})
    assert result.status_code == 200
    search_identity = verify_workspace_token(result.json()["token"])
    assert search_identity.workspace_id == run["index_workspace_id"]
    assert search_identity.workspace_id != identity.workspace_id
    assert client.post("/api/v1/export-handoffs/claim", json={"code": code}).status_code == 404


def test_oauth_state_is_bound_to_workspace_pkce_and_single_use(export_system, monkeypatch):
    _, _, store, identity, _, _ = export_system
    settings = replace(Settings(), google_client_id="test-client", google_client_secret="test-secret", extension_ids=("a" * 32,))
    monkeypatch.setattr(youtube_authorized, "get_settings", lambda: settings)
    exchange = Mock(return_value={"access_token": "sensitive-access", "refresh_token": "sensitive-refresh", "expires_in": 3600, "scope": SCOPE})
    monkeypatch.setattr(youtube_authorized, "token_request", exchange)
    started = youtube_authorized.start_authorization(identity, "a" * 32)
    params = parse_qs(urlparse(started["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["https://" + "a" * 32 + ".chromiumapp.org/oauth2"]
    other = WorkspaceIdentity(str(uuid.uuid4()), identity.expires_at)
    with pytest.raises(YouTubeAccessError):
        youtube_authorized.finish_authorization(other, started["state"], "code")
    youtube_authorized.finish_authorization(identity, started["state"], "code")
    assert exchange.call_args.args[0]["code_verifier"]
    assert store.get("grant:" + identity.workspace_id)["access_token"] == "sensitive-access"
    assert b"sensitive-access" not in store._memory["grant:" + identity.workspace_id][1]
    with pytest.raises(YouTubeAccessError):
        youtube_authorized.finish_authorization(identity, started["state"], "code")
    assert exchange.call_count == 1


def test_oauth_rejects_unregistered_extension_and_missing_scope(export_system, monkeypatch):
    _, _, store, identity, _, _ = export_system
    settings = replace(Settings(), google_client_id="client", google_client_secret="secret", extension_ids=("a" * 32,))
    monkeypatch.setattr(youtube_authorized, "get_settings", lambda: settings)
    with pytest.raises(YouTubeAccessError):
        youtube_authorized.start_authorization(identity, "b" * 32)
    started = youtube_authorized.start_authorization(identity, "a" * 32)
    monkeypatch.setattr(youtube_authorized, "token_request", lambda _: {"scope": "other"})
    with pytest.raises(YouTubeAccessError):
        youtube_authorized.finish_authorization(identity, started["state"], "code")
    assert store.get("grant:" + identity.workspace_id) is None


def test_store_expiry_and_atomic_consume():
    store = ExportStore()
    store.put("one-time", {"private": "value"}, time.time() + 60)
    assert store.get("one-time", consume=True) == {"private": "value"}
    assert store.get("one-time", consume=True) is None
    with pytest.raises(ValueError):
        store.put("expired", "x", time.time() - 1)


def test_local_export_store_survives_restart_and_keeps_worker_lease_ephemeral(tmp_path):
    path = tmp_path / "exports.sqlite3"
    first = ExportStore(secret="same-secret", local_path=str(path))
    first.put("run:one", {"saved": 43}, time.time() + 60)
    first.put("worker:one", "old-process", time.time() + 60)
    second = ExportStore(secret="same-secret", local_path=str(path))
    assert second.get("run:one") == {"saved": 43}
    assert second.get("worker:one") is None
    assert second.put("run:one", "duplicate", time.time() + 60, only_if_absent=True) is False
    assert second.get("run:one", consume=True) == {"saved": 43}
    assert first.get("run:one") is None


def test_queue_failure_is_visible_and_resumable(export_system):
    client, _, _, _, _, jobs = export_system
    jobs.enqueue_export.side_effect = ConnectionError("Queue is unavailable")
    result = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()
    assert result["status"] == "paused"
    assert result["error_code"] == "temporary"


def test_indexing_imports_real_segments_and_preserves_partial_export_status(export_system, monkeypatch):
    client, _, store, identity, youtube, _ = export_system
    youtube.video_page.return_value = playlist_page(["a" * 11, "b" * 11])
    youtube.caption.side_effect = [(VTT, "en", "standard"), YouTubeAccessError("quota", "Quota")]
    run_id = client.post("/api/v1/exports", json={"channel_id": CHANNEL_ID}).json()["id"]
    collect_channel(identity.workspace_id, run_id)
    import src.embedder
    embedder = Mock()
    embedder._create_documents.return_value = [Mock(), Mock()]
    monkeypatch.setattr(src.embedder, "Embedder", lambda **_: embedder)
    queued = client.post(f"/api/v1/exports/{run_id}/index")
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    collect_channel(identity.workspace_id, run_id, "index")
    imported = client.get(f"/api/v1/exports/{run_id}").json()
    assert imported["indexed"] is True
    assert imported["status"] == "paused"
    assert imported["metrics"] == {"catalog": 2, "videos": 1, "chunks": 2}
    documents = embedder.ingest.call_args.args[0]
    assert documents[0]["segments"][0]["start"] == 1
    assert documents[0]["transcript"] == "Hello & welcome A useful idea."
    # The downloadable archive keeps precise timestamps after RAG preparation.
    bundle = client.get(f"/api/v1/exports/{run_id}/download?format=json").json()
    assert bundle["videos"][0]["segments"][0]["start"] == 1.5
    assert store.get(run_key(identity.workspace_id, run_id))["operation"] == "collect"


def test_public_export_collects_more_than_ten_unowned_videos_and_zip_imports(export_system, monkeypatch):
    client, _, store, identity, _, jobs = export_system
    video_ids = [f"{number:011d}" for number in range(12)]
    extractor = Mock()
    extractor.get_channel_id.return_value = CHANNEL_ID
    extractor.youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Someone else's channel"},
                   "contentDetails": {"relatedPlaylists": {"uploads": "UUpublic"}}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.side_effect = [
        playlist_page(video_ids[:8], "page2"), playlist_page(video_ids[8:]),
    ]
    track = SimpleNamespace(
        language_code="en", is_generated=True,
        fetch=lambda: [SimpleNamespace(text="Public caption", start=12.5, duration=2.0)],
    )
    extractor.ytt_api.list.return_value = [track]
    monkeypatch.setattr("src.extractor.YouTubeExtractor", lambda: extractor)
    started = client.post("/api/v1/public-exports", json={"channel": "@someoneelse"})
    assert started.status_code == 202
    assert jobs.enqueue_export.called
    run_id = started.json()["id"]
    collect_channel(identity.workspace_id, run_id)
    result = client.get(f"/api/v1/exports/{run_id}").json()
    assert (result["status"], result["total"], result["exported"]) == ("finished", 12, 12)
    assert extractor.youtube.playlistItems.return_value.list.call_count == 2
    assert extractor.ytt_api.list.call_count == 12
    zip_data = client.get(f"/api/v1/exports/{run_id}/download").content
    with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
        bundle = json.loads(archive.read("channel.json"))
        assert bundle["source"] == "youtube-public-transcripts"
        assert len(bundle["videos"]) == 12
        assert bundle["videos"][-1]["segments"][0]["start"] == 12.5

    import src.embedder
    embedder = Mock()
    embedder._create_documents.return_value = [Mock()]
    monkeypatch.setattr(src.embedder, "Embedder", lambda **_: embedder)
    imported = client.post(
        f"/api/v1/workspaces/{identity.workspace_id}/imports",
        content=zip_data, headers={"Content-Type": "application/zip"},
    )
    assert imported.status_code == 202
    assert imported.json()["status"] == "queued"
    import_id = imported.json()["id"]
    collect_channel(identity.workspace_id, import_id, "index")
    done = client.get(f"/api/v1/exports/{import_id}").json()
    assert done["indexed"] is True
    assert done["metrics"] == {"catalog": 12, "videos": 12, "chunks": 12}
    code = client.post(f"/api/v1/exports/{import_id}/handoff").json()["code"]
    claimed = client.post("/api/v1/export-handoffs/claim", json={"code": code}).json()
    assert claimed["coverage"]["discovered"] == 12
    assert claimed["coverage"]["indexed"] == 12
    assert embedder.ingest.call_count == 12
    assert store.get(run_key(identity.workspace_id, import_id))["index_workspace_id"] == identity.workspace_id


def test_partial_public_zip_continues_without_refetching_saved_transcripts(export_system, monkeypatch):
    client, _, _, identity, _, _ = export_system
    ids = [f"{number:011d}" for number in range(12)]
    bundle = {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": "youtube-public-transcripts", "channel": {"id": CHANNEL_ID, "title": "Creator"},
        "complete": False, "videos": [
            {"video_id": video_id, "title": video_id, "status": "exported",
             "segments": [{"text": "Saved", "start": 1, "duration": 2}], "language": "en"}
            if number < 3 else {"video_id": video_id, "title": video_id, "status": "pending"}
            for number, video_id in enumerate(ids)
        ],
    }
    extractor = Mock()
    extractor.get_channel_id.return_value = CHANNEL_ID
    extractor.youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Creator"},
                   "contentDetails": {"relatedPlaylists": {"uploads": "UUpublic"}}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = playlist_page(ids)
    track = SimpleNamespace(language_code="en", is_generated=True,
                            fetch=lambda: [SimpleNamespace(text="New", start=3, duration=2)])
    extractor.ytt_api.list.return_value = [track]
    monkeypatch.setattr("src.extractor.YouTubeExtractor", lambda: extractor)
    uploaded = client.post("/api/v1/public-exports/resume-upload", content=json.dumps(bundle),
                           headers={"Content-Type": "application/octet-stream"})
    assert uploaded.status_code == 202
    run_id = uploaded.json()["id"]
    collect_channel(identity.workspace_id, run_id)
    result = client.get(f"/api/v1/exports/{run_id}").json()
    assert (result["status"], result["total"], result["exported"]) == ("finished", 12, 12)
    assert extractor.ytt_api.list.call_count == 9
    downloaded = client.get(f"/api/v1/exports/{run_id}/download?format=json").json()
    assert downloaded["videos"][0]["segments"][0]["text"] == "Saved"
    assert downloaded["videos"][-1]["segments"][0]["text"] == "New"


def test_public_playlist_export_paginates_and_imports_mixed_channel_videos(export_system, monkeypatch):
    client, _, _, identity, _, _ = export_system
    ids = [f"{number:011d}" for number in range(120)]
    playlist_id = "PL" + "p" * 32
    extractor = Mock()
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "A mixed creator playlist"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.side_effect = [
        playlist_page(ids[:50], "page2"),
        playlist_page(ids[49:99], "page3"),
        playlist_page(ids[99:]),
    ]
    track = SimpleNamespace(language_code="en", is_generated=False,
                            fetch=lambda: [SimpleNamespace(text="A spoken idea", start=1.25, duration=3)])
    extractor.ytt_api.list.return_value = [track]
    monkeypatch.setattr("src.extractor.YouTubeExtractor", lambda: extractor)
    started = client.post("/api/v1/public-playlist-exports", json={
        "playlist": f"https://www.youtube.com/watch?v={ids[0]}&list={playlist_id}",
    })
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.json()["channel"]["kind"] == "playlist"
    collect_channel(identity.workspace_id, run_id)
    result = client.get(f"/api/v1/exports/{run_id}").json()
    assert (result["status"], result["total"], result["exported"]) == ("finished", 120, 120)
    assert result["coverage"]["discovered"] == 120
    assert result["coverage"]["transcribed"] == 120
    assert result["coverage"]["pending"] == 0
    assert result["channel"] == {"id": playlist_id, "title": "A mixed creator playlist", "kind": "playlist"}
    assert extractor.youtube.playlistItems.return_value.list.call_count == 3
    extractor.youtube.channels.assert_not_called()
    extractor.get_channel_id.assert_not_called()
    bundle = client.get(f"/api/v1/exports/{run_id}/download?format=json").json()
    assert bundle["source"] == "youtube-public-playlist-transcripts"
    assert bundle["complete"] is True
    assert bundle["videos"][0]["segments"][0]["start"] == 1.25
    uploaded = client.post(f"/api/v1/workspaces/{identity.workspace_id}/imports", content=json.dumps(bundle))
    assert uploaded.status_code == 202
    assert uploaded.json()["channel"]["kind"] == "playlist"


def test_public_playlist_partial_resume_reuses_transcript(export_system, monkeypatch):
    client, _, _, identity, _, _ = export_system
    playlist_id = "PL" + "q" * 32
    ids = ["a" * 11, "b" * 11]
    bundle = {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": "youtube-public-playlist-transcripts",
        "channel": {"id": playlist_id, "title": "Old playlist", "kind": "playlist"},
        "complete": False,
        "videos": [
            {"video_id": ids[0], "title": "Saved", "status": "exported", "language": "en",
             "segments": [{"text": "Already saved", "start": 2, "duration": 1}]},
            {"video_id": ids[1], "title": "Pending", "status": "pending"},
        ],
    }
    extractor = Mock()
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Old playlist"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = playlist_page(ids)
    track = SimpleNamespace(language_code="en", is_generated=True,
                            fetch=lambda: [SimpleNamespace(text="New speech", start=3, duration=1)])
    extractor.ytt_api.list.return_value = [track]
    monkeypatch.setattr("src.extractor.YouTubeExtractor", lambda: extractor)
    started = client.post("/api/v1/public-exports/resume-upload", content=json.dumps(bundle))
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.json()["coverage"]["pending"] == 1
    collect_channel(identity.workspace_id, run_id)
    result = client.get(f"/api/v1/exports/{run_id}").json()
    assert (result["status"], result["exported"]) == ("finished", 2)
    assert result["coverage"]["pending"] == 0
    extractor.ytt_api.list.assert_called_once_with(ids[1])
    downloaded = client.get(f"/api/v1/exports/{run_id}/download?format=json").json()
    assert downloaded["channel"]["kind"] == "playlist"
    assert downloaded["videos"][0]["segments"][0]["text"] == "Already saved"


@pytest.mark.parametrize("value", [
    "https://youtube.com/playlist?list=short", "https://youtube.com/watch?v=aaaaaaaaaaa",
    "https://youtube.com.evil.example/playlist?list=PL" + "x" * 32,
    "http://youtube.com/playlist?list=PL" + "x" * 32,
    "https://youtube.com/channel/UC" + "a" * 22,
])
def test_public_playlist_rejects_invalid_sources(export_system, value):
    client, _, _, _, _, _ = export_system
    assert client.post("/api/v1/public-playlist-exports", json={"playlist": value}).status_code == 422


def test_file_import_rejects_malformed_or_unusable_bundles(export_system):
    client, _, _, identity, _, _ = export_system
    path = f"/api/v1/workspaces/{identity.workspace_id}/imports"
    assert client.post(path, content=b"not JSON").status_code == 400
    bundle = {"schema": "channelmind.channel-transcripts", "version": 1,
              "channel": {"id": CHANNEL_ID, "title": "Example"},
              "videos": [{"video_id": "a" * 11, "title": "Unavailable", "status": "skipped"}]}
    assert client.post(path, content=json.dumps(bundle)).status_code == 400
    bundle["videos"][0].update(status="exported", segments=[{"text": "Hi", "start": -1, "duration": 1}])
    assert client.post(path, content=json.dumps(bundle)).status_code == 400


def test_file_import_preserves_missing_video_reasons_in_coverage(export_system, monkeypatch):
    client, _, _, identity, _, _ = export_system
    bundle = {"schema": "channelmind.channel-transcripts", "version": 1,
              "channel": {"id": CHANNEL_ID, "title": "Mixed coverage"},
              "videos": [
                  {"video_id": "a" * 11, "title": "Available", "status": "exported",
                   "segments": [{"text": "Explained", "start": 2, "duration": 1}]},
                  {"video_id": "b" * 11, "title": "No captions", "status": "skipped", "reason": "no_captions"},
                  {"video_id": "c" * 11, "title": "Unavailable", "status": "skipped", "reason": "VideoUnavailable"},
              ]}
    import src.embedder
    embedder = Mock()
    embedder._create_documents.return_value = [Mock()]
    monkeypatch.setattr(src.embedder, "Embedder", lambda **_: embedder)

    started = client.post(f"/api/v1/workspaces/{identity.workspace_id}/imports", content=json.dumps(bundle))
    assert started.status_code == 202
    assert started.json()["coverage"]["discovered"] == 3
    run_id = started.json()["id"]
    collect_channel(identity.workspace_id, run_id, "index")
    report = client.get(f"/api/v1/exports/{run_id}").json()
    assert report["coverage"]["indexed"] == 1
    assert report["coverage"]["unavailable"] == 2
    assert report["coverage"]["reasons"] == [
        {"reason": "VideoUnavailable", "count": 1},
        {"reason": "no_captions", "count": 1},
    ]


def test_public_export_rejects_video_urls(export_system):
    client, _, _, _, _, _ = export_system
    response = client.post("/api/v1/public-exports", json={
        "channel": "https://www.youtube.com/watch?v=" + "a" * 11,
    })
    assert response.status_code == 422


def test_interrupted_file_index_resumes_after_completed_video(export_system, monkeypatch):
    client, _, _, identity, _, _ = export_system
    bundle = {"schema": "channelmind.channel-transcripts", "version": 1,
              "channel": {"id": CHANNEL_ID, "title": "Large channel"},
              "videos": [{"video_id": f"{i:011d}", "title": f"Video {i}",
                          "status": "exported", "segments": [{"text": f"idea {i}",
                          "start": 10, "duration": 2}]} for i in range(3)]}
    path = f"/api/v1/workspaces/{identity.workspace_id}/imports"
    run_id = client.post(path, content=json.dumps(bundle)).json()["id"]
    import src.embedder
    embedder = Mock()
    embedder._create_documents.return_value = [Mock()]
    embedder.ingest.side_effect = [None, RuntimeError("worker stopped")]
    monkeypatch.setattr(src.embedder, "Embedder", lambda **_: embedder)
    collect_channel(identity.workspace_id, run_id, "index")
    paused = client.get(f"/api/v1/exports/{run_id}").json()
    assert paused["status"] == "paused"
    assert paused["indexed_count"] == 1
    embedder.ingest.side_effect = None
    assert client.post(f"/api/v1/exports/{run_id}/resume").status_code == 202
    collect_channel(identity.workspace_id, run_id, "index")
    assert client.get(f"/api/v1/exports/{run_id}").json()["metrics"]["videos"] == 3
    assert embedder.clear.call_count == 1
    assert embedder.ingest.call_count == 4
