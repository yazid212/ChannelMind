import io
import json
import zipfile

from api.bundle_import import parse_bundle
from scripts.recover_transcripts_local import recover_bundle, write_bundle


def test_local_recovery_retries_pending_entries_and_keeps_existing_transcripts(tmp_path):
    existing = {
        "video_id": "aaaaaaaaaaa", "title": "Already done", "status": "exported",
        "language": "en", "caption_kind": "standard",
        "segments": [{"text": "Keep me", "start": 2.5, "duration": 1.0}],
    }
    pending = {"video_id": "bbbbbbbbbbb", "title": "Retry me", "status": "pending"}
    bundle = {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": "youtube-public-playlist-transcripts", "complete": False,
        "channel": {"kind": "playlist", "id": "PLabcdefghij", "title": "Test playlist"},
        "videos": [existing, pending],
    }
    calls = []

    def fetcher(video, language):
        calls.append((video["video_id"], language))
        return ({"video_id": video["video_id"], "title": video["title"],
                 "segments": [{"text": "Recovered locally", "start": 1.25, "duration": 2}],
                 "language": "en", "caption_kind": "ASR"}, None)

    recovered = recover_bundle(bundle, "en", fetcher=fetcher, progress=lambda _: None)
    assert calls == [("bbbbbbbbbbb", "en")]
    assert recovered["complete"] is True
    assert [video["status"] for video in recovered["videos"]] == ["exported", "exported"]

    target = tmp_path / "recovered.zip"
    write_bundle(recovered, target)
    data = target.read_bytes()
    collection, videos = parse_bundle(data)
    assert collection == {"id": "PLabcdefghij", "title": "Test playlist", "kind": "playlist"}
    assert [video["video_id"] for video in videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert videos[1]["transcript_data"]["segments"][0] == {
        "text": "Recovered locally", "start": 1.25, "duration": 2, "end": 3.25,
    }
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "transcripts/bbbbbbbbbbb.txt" in archive.namelist()
        assert "Recovered locally" in archive.read("channel.txt").decode()


def test_failed_local_fetch_becomes_explicit_skipped_video():
    bundle = {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": "youtube-public-playlist-transcripts", "complete": False,
        "channel": {"kind": "playlist", "id": "PLabcdefghij", "title": "Test playlist"},
        "videos": [{"video_id": "ccccccccccc", "title": "Still blocked", "status": "pending"}],
    }
    recovered = recover_bundle(
        bundle, fetcher=lambda *_: (None, "local_request_blocked"), progress=lambda _: None,
    )
    assert recovered["videos"][0]["status"] == "skipped"
    assert recovered["videos"][0]["reason"] == "local_request_blocked"
    assert json.loads(json.dumps(recovered))["complete"] is True


def test_collection_stops_requests_after_local_ip_block():
    bundle = {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": "youtube-public-playlist-transcripts", "complete": False,
        "channel": {"kind": "playlist", "id": "PLabcdefghij", "title": "Test playlist"},
        "videos": [{"video_id": f"{letter * 11}", "title": "Video", "status": "pending"}
                   for letter in "abc"],
    }
    requested = []

    def blocked(video, _language):
        requested.append(video["video_id"])
        return None, "local_request_blocked"

    recovered = recover_bundle(bundle, fetcher=blocked, progress=lambda _: None)
    assert requested == ["a" * 11]
    assert [item["reason"] for item in recovered["videos"]] == [
        "local_request_blocked", "local_request_blocked", "local_request_blocked",
    ]
