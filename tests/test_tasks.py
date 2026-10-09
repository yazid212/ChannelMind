import threading
import time
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from api.tasks import expand_workspace, ingest_channel_full, ingest_playlist
from src.local_transcriber import VideoAccessBlockedError, VideoRestrictedError, VideoUnavailableError


@patch("api.tasks.fetch_video_transcript")
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_channel_indexes_all_uploads_with_caption_or_speech_path(
    extractor_class, embedder_class, fetch_transcript,
):
    ids = [f"{index:011d}" for index in range(32)]
    extractor = extractor_class.return_value
    extractor.get_channel_id.return_value = "UC-channel"
    extractor.youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Full channel"},
                   "contentDetails": {"relatedPlaylists": {"uploads": "UU-channel"}}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": video_id}, "snippet": {"title": video_id}}
                  for video_id in ids],
    }
    fetch_transcript.side_effect = lambda video_id, title: {
        "video_id": video_id, "title": video_id,
        "segments": [{"text": "recognized words", "start": 0}],
        "transcript_source": "local_speech_recognition",
    }
    embedder_class.return_value._create_documents.return_value = [Mock()]

    with patch("api.tasks.get_export_store") as progress_store:
        result = ingest_channel_full("workspace", "@channel", 123, "job-1")

    assert result["scope"] == "channel"
    assert result["title"] == "Full channel"
    assert result["catalog_count"] == 32
    assert result["video_count"] == 32
    assert result["coverage"]["indexed"] == 32
    assert fetch_transcript.call_count == 32
    assert embedder_class.return_value.ingest.call_count == 32
    assert progress_store.return_value.put.call_args_list[-1].args[1]["coverage"]["pending"] == 0


@patch("api.tasks.YouTubeExtractor")
@patch("api.tasks.Embedder")
def test_expansion_skips_indexed_videos_and_ingests_relevant_older_video(
    embedder_class,
    extractor_class,
):
    embedder = embedder_class.return_value
    embedder.indexed_video_ids.return_value = {"recent"}
    embedder.search_catalog.return_value = [
        Document(page_content="recent", metadata={"video_id": "recent"}),
        Document(page_content="older", metadata={"video_id": "older"}),
    ]
    transcript = {
        "video_id": "older",
        "title": "Older answer",
        "segments": [{"text": "answer", "start": 0}],
    }
    extractor_class.return_value.get_transcript.return_value = transcript
    embedder._create_documents.return_value = [Mock(), Mock(), Mock()]

    result = expand_workspace("workspace", "older topic", 123)

    extractor_class.return_value.get_transcript.assert_called_once_with("older")
    embedder.ingest.assert_called_once_with([transcript], replace_existing=False)
    assert result["added_video_count"] == 1
    assert result["chunk_count"] == 3


@patch("api.tasks.fetch_video_transcript")
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_playlist_accounts_for_every_entry_and_uses_video_transcription(
    extractor_class, embedder_class, fetch_transcript,
):
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Shared lessons"}}],
    }
    first, second, third = "a" * 11, "b" * 11, "c" * 11
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [
            {"contentDetails": {"videoId": video_id}, "snippet": {"title": title}}
            for video_id, title in [(first, "First"), (second, "No captions"), (first, "Duplicate"), (third, "Later")]
        ],
    }
    fetch_transcript.side_effect = [
        {"video_id": first, "title": "First", "segments": [{"text": "captions", "start": 0}]},
        {"video_id": second, "title": "No captions", "segments": [{"text": "recognized", "start": 1}],
         "transcript_source": "local_speech_recognition"},
        {"video_id": third, "title": "Later", "segments": [{"text": "more captions", "start": 2}]},
    ]
    embedder_class.return_value._create_documents.return_value = [Mock()]

    with patch("api.tasks.get_export_store") as progress_store:
        result = ingest_playlist("workspace", "PL" + "x" * 32, 123, "job-1")

    assert result["ok"] is True
    assert result["coverage"] == {
        "discovered": 4, "transcribed": 3, "indexed": 3,
        "unavailable": 1, "failed": 0, "pending": 0,
        "reasons": [{"reason": "duplicate_playlist_entry", "count": 1}],
    }
    assert result["issue_total"] == 1
    assert [issue["outcome"] for issue in result["issues"]] == ["unavailable"]
    assert fetch_transcript.call_count == 3
    assert embedder_class.return_value.ingest.call_count == 3
    snapshots = [call.args[1]["coverage"] for call in progress_store.return_value.put.call_args_list]
    assert [snapshot["pending"] for snapshot in snapshots] == [4, 3, 2, 1, 0]
    assert snapshots[-1]["indexed"] == 3


@patch("api.tasks.fetch_video_transcript")
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_playlist_indexes_all_32_accessible_videos(extractor_class, embedder_class, fetch_transcript):
    ids = [f"{index:011d}" for index in range(32)]
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "32 videos"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": video_id}, "snippet": {"title": video_id}}
                  for video_id in ids],
    }
    fetch_transcript.side_effect = lambda video_id, title: {
        "video_id": video_id, "title": video_id,
        "segments": [{"text": "spoken words", "start": 0}],
    }
    embedder_class.return_value._create_documents.return_value = [Mock()]

    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)

    assert result["catalog_count"] == 32
    assert result["video_count"] == 32
    assert result["coverage"]["pending"] == 0
    assert result["coverage"]["indexed"] == 32
    assert fetch_transcript.call_count == 32


@patch("api.tasks.LOCAL_TRANSCRIPTION_WORKERS", 2)
@patch("api.tasks.fetch_video_transcript")
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_large_collection_limits_simultaneous_transcriptions(
    extractor_class, embedder_class, fetch_transcript,
):
    ids = [f"{index:011d}" for index in range(13)]
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Long collection"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": video_id}, "snippet": {"title": video_id}}
                  for video_id in ids],
    }
    embedder_class.return_value._create_documents.return_value = [Mock()]
    lock = threading.Lock()
    active = 0
    peak = 0

    def fetch(video_id, title):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return {"video_id": video_id, "title": title,
                "segments": [{"text": "spoken words", "start": 0}]}

    fetch_transcript.side_effect = fetch
    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)

    assert result["coverage"]["indexed"] == 13
    assert peak == 1


@patch("api.tasks.fetch_video_transcript")
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_playlist_restart_keeps_indexed_video_and_only_transcribes_remaining(
    extractor_class, embedder_class, fetch_transcript,
):
    first, second = "a" * 11, "b" * 11
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Lessons"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": video_id}, "snippet": {"title": video_id}}
                  for video_id in (first, second)],
    }
    embedder = embedder_class.return_value
    embedder.indexed_video_ids.return_value = {first}
    embedder.transcript_chunks.return_value = [Mock(), Mock()]
    embedder._create_documents.return_value = [Mock()]
    fetch_transcript.return_value = {
        "video_id": second, "title": second,
        "segments": [{"text": "remaining lesson", "start": 0}],
    }

    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)

    fetch_transcript.assert_called_once_with(second, second)
    assert result["video_count"] == 2
    assert result["chunk_count"] == 3
    assert result["coverage"]["pending"] == 0


@patch("api.tasks.YouTubeExtractor")
def test_restricted_playlist_returns_useful_failure(extractor_class):
    extractor_class.return_value.youtube.playlists.return_value.list.return_value.execute.return_value = {"items": []}
    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)
    assert result == {"ok": False, "message": "Public playlist was not found or is restricted."}


@patch("api.tasks.fetch_video_transcript", side_effect=VideoUnavailableError("unavailable"))
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_unplayable_entry_counts_as_unavailable_not_failed(
    extractor_class, embedder_class, fetch_transcript,
):
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Mixed playlist"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": "a" * 11},
                   "snippet": {"title": "Unavailable lesson"}}],
    }
    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)
    assert result["coverage"]["unavailable"] == 1
    assert result["coverage"]["failed"] == 0
    assert result["issues"][0]["reason"] == "video_unavailable"


@patch("api.tasks.fetch_video_transcript", side_effect=VideoRestrictedError("age restricted"))
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_age_restricted_entry_counts_as_unavailable_not_failed(
    extractor_class, embedder_class, fetch_transcript,
):
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Mixed playlist"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": "a" * 11},
                   "snippet": {"title": "Restricted lesson"}}],
    }
    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)
    assert result["coverage"]["unavailable"] == 1
    assert result["coverage"]["failed"] == 0
    assert result["issues"][0]["reason"] == "age_restricted"


@patch("api.tasks.fetch_video_transcript", side_effect=VideoAccessBlockedError("blocked"))
@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_youtube_bot_challenge_has_specific_playlist_reason(
    extractor_class, embedder_class, fetch_transcript,
):
    extractor = extractor_class.return_value
    extractor.youtube.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"title": "Public playlist"}}],
    }
    extractor.youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": "a" * 11},
                   "snippet": {"title": "Blocked lesson"}}],
    }
    result = ingest_playlist("workspace", "PL" + "x" * 32, 123)
    assert result["coverage"]["failed"] == 1
    assert result["issues"][0]["reason"] == "youtube_access_blocked"
