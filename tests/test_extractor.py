import os
from unittest.mock import Mock, patch
from uuid import uuid4

from src.extractor import YouTubeExtractor
from src.utils import (
    clean_text,
    extract_channel_id,
    format_timestamp,
    load_transcripts_cache,
    save_transcripts_cache,
)


def test_extract_channel_id_direct():
    url = "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"
    assert extract_channel_id(url) == "UC_x5XG1OV2P6uZZ5FSM9Ttw"


def test_extract_channel_id_handle():
    url = "https://www.youtube.com/@Fireship"
    assert extract_channel_id(url) == "Fireship"


def test_clean_text():
    text = "[Music] Hello world  [Applause]"
    assert clean_text(text) == "Hello world"


def test_format_timestamp():
    assert format_timestamp(45) == "00:45"
    assert format_timestamp(125) == "02:05"
    assert format_timestamp(3665) == "1:01:05"


def test_cache_roundtrip():
    test_data = [{"video_id": "v1", "title": "Test Title", "transcript": "sample text", "segments": []}]
    channel_id = f"UC_test_{uuid4().hex}"
    saved_path = save_transcripts_cache(channel_id, test_data)

    try:
        assert os.path.exists(saved_path)
        loaded = load_transcripts_cache(channel_id)
        assert loaded == test_data
    finally:
        if os.path.exists(saved_path):
            os.remove(saved_path)


def test_extractor_initialization_uses_mocked_external_clients():
    with (
        patch("src.extractor.build") as youtube_build,
        patch("src.extractor.YouTubeTranscriptApi") as transcript_api,
        patch("src.extractor.requests.Session") as session_class,
    ):
        extractor = YouTubeExtractor(api_key="test-key")

    youtube_build.assert_called_once_with("youtube", "v3", developerKey="test-key")
    transcript_api.assert_called_once_with(
        http_client=session_class.return_value,
        proxy_config=None,
    )
    assert session_class.return_value.trust_env is False
    assert extractor.youtube is youtube_build.return_value


def test_video_catalog_paginates_and_normalizes_metadata():
    extractor = YouTubeExtractor.__new__(YouTubeExtractor)
    extractor.youtube = youtube = Mock()
    youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads-id"}}}]
    }
    youtube.playlistItems.return_value.list.return_value.execute.side_effect = [
        {
            "items": [{
                "contentDetails": {"videoId": "video-1"},
                "snippet": {
                    "title": "First video",
                    "description": "Useful [Music] details",
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
            }],
            "nextPageToken": "next",
        },
        {
            "items": [{
                "contentDetails": {"videoId": "video-2"},
                "snippet": {"title": "Second video", "description": "More details"},
            }]
        },
    ]

    catalog = extractor.get_video_catalog("channel-id", max_results=2)

    assert [item["video_id"] for item in catalog] == ["video-1", "video-2"]
    assert catalog[0]["description"] == "Useful details"
    assert youtube.playlistItems.return_value.list.call_count == 2


def test_handle_resolution_falls_back_to_channel_search():
    extractor = YouTubeExtractor.__new__(YouTubeExtractor)
    extractor.youtube = youtube = Mock()
    youtube.channels.return_value.list.return_value.execute.return_value = {"items": []}
    youtube.search.return_value.list.return_value.execute.return_value = {
        "items": [{"snippet": {"channelId": "UC-fallback"}}]
    }

    assert extractor.get_channel_id("@valid-handle") == "UC-fallback"
    youtube.search.return_value.list.assert_called_once()
