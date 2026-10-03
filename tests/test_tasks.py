from unittest.mock import Mock, patch

from langchain_core.documents import Document

from api.tasks import expand_workspace, ingest_channel


@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_ingestion_catalogs_channel_but_only_transcribes_initial_videos(
    extractor_class,
    embedder_class,
):
    extractor = extractor_class.return_value
    extractor.get_channel_id.return_value = "UC-channel"
    catalog = [
        {"video_id": f"v{index}", "title": f"Video {index}", "description": ""}
        for index in range(5)
    ]
    extractor.get_video_catalog.return_value = catalog
    extractor.get_transcript.side_effect = lambda video_id: {
        "video_id": video_id,
        "title": video_id,
        "segments": [{"text": "content", "start": 0}],
    }
    embedder = embedder_class.return_value
    embedder._create_documents.return_value = [Mock(), Mock()]

    result = ingest_channel("workspace", "@channel", 2, 123)

    assert result["catalog_count"] == 5
    assert result["video_count"] == 2
    embedder.ingest_catalog.assert_called_once_with(catalog)
    assert extractor.get_transcript.call_count == 2


@patch("api.tasks.Embedder")
@patch("api.tasks.YouTubeExtractor")
def test_ingestion_keeps_catalog_ready_when_recent_transcripts_are_unavailable(
    extractor_class,
    embedder_class,
):
    extractor = extractor_class.return_value
    extractor.get_channel_id.return_value = "UC-channel"
    catalog = [
        {"video_id": f"v{index}", "title": f"Video {index}", "description": ""}
        for index in range(3)
    ]
    extractor.get_video_catalog.return_value = catalog
    extractor.get_transcript.return_value = None

    result = ingest_channel("workspace", "@channel", 1, 123)

    assert result["ok"] is True
    assert result["catalog_count"] == 3
    assert result["video_count"] == 0
    assert result["warning"]
    embedder_class.return_value.ingest.assert_not_called()


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
