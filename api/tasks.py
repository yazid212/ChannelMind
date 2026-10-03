"""Background tasks imported by both the API process and RQ workers."""

from config import CATALOG_SEARCH_K, MAX_CATALOG_VIDEOS, ON_DEMAND_VIDEOS
from src.embedder import Embedder
from src.extractor import YouTubeExtractor
from src.utils import extract_channel_id
from src.video_transcript import fetch_video_transcript


def ingest_video(
    workspace_id: str,
    video_id: str,
    workspace_expires_at: int | None = None,
) -> dict:
    """Index one complete caption track, regardless of video duration."""
    try:
        transcript = fetch_video_transcript(video_id)
    except (RuntimeError, ValueError) as error:
        return {"ok": False, "message": str(error), "video_id": video_id}
    embedder = Embedder(workspace_id=workspace_id, expires_at=workspace_expires_at)
    documents = embedder._create_documents([transcript])
    embedder.ingest_catalog([transcript])
    embedder.ingest([transcript])
    return {
        "ok": True,
        "video_id": video_id,
        "title": transcript["title"],
        "video_count": 1,
        "catalog_count": 1,
        "chunk_count": len(documents),
        "segment_count": len(transcript["segments"]),
        "transcript_source": transcript.get("transcript_source", "youtube_captions"),
    }


def ingest_channel(
    workspace_id: str,
    channel: str,
    max_videos: int,
    workspace_expires_at: int | None = None,
) -> dict:
    extractor = YouTubeExtractor()
    identifier = extract_channel_id(channel) or channel
    channel_id = extractor.get_channel_id(identifier)
    catalog_limit = MAX_CATALOG_VIDEOS if MAX_CATALOG_VIDEOS > 0 else None
    catalog = extractor.get_video_catalog(channel_id, max_results=catalog_limit)
    transcripts = []
    # Deleted videos, disabled captions, and regional restrictions are common.
    # Scan a bounded number of recent uploads to fulfill the requested initial
    # transcript count instead of failing on the first unavailable upload.
    attempt_limit = min(len(catalog), max(max_videos * 3, max_videos + 5))
    for video in catalog[:attempt_limit]:
        transcript = extractor.get_transcript(video["video_id"])
        if transcript:
            transcripts.append(transcript)
        if len(transcripts) >= max_videos:
            break

    embedder = Embedder(workspace_id=workspace_id, expires_at=workspace_expires_at)
    embedder.ingest_catalog(catalog)
    if not transcripts:
        return {
            "ok": True,
            "video_count": 0,
            "chunk_count": 0,
            "catalog_count": len(catalog),
            "warning": (
                "The channel catalog is ready, but no recent transcript was available. "
                "Ask a question to try relevant older videos."
            ),
        }

    documents = embedder._create_documents(transcripts)
    embedder.ingest(transcripts)
    return {
        "ok": True,
        "video_count": len(transcripts),
        "catalog_count": len(catalog),
        "chunk_count": len(documents),
        "channel": channel,
    }


def expand_workspace(
    workspace_id: str,
    question: str,
    workspace_expires_at: int | None = None,
) -> dict:
    """Transcribe the most relevant not-yet-indexed catalog videos."""
    embedder = Embedder(workspace_id=workspace_id, expires_at=workspace_expires_at)
    candidates = embedder.search_catalog(question, k=CATALOG_SEARCH_K)
    indexed = embedder.indexed_video_ids()
    extractor = YouTubeExtractor()
    transcripts = []
    attempted: set[str] = set()

    for candidate in candidates:
        video_id = candidate.metadata.get("video_id")
        if not video_id or video_id in indexed or video_id in attempted:
            continue
        attempted.add(video_id)
        transcript = extractor.get_transcript(video_id)
        if transcript:
            transcripts.append(transcript)
        if len(transcripts) >= ON_DEMAND_VIDEOS:
            break

    documents = embedder._create_documents(transcripts)
    if transcripts:
        embedder.ingest(transcripts, replace_existing=False)
    return {
        "ok": True,
        "added_video_count": len(transcripts),
        "chunk_count": len(documents),
        "added_titles": [item["title"] for item in transcripts],
    }
