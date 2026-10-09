"""Background tasks imported by both the API process and RQ workers."""

import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from src.utils import clean_text, log_exception_safely
from .export_store import get_export_store

from config import CATALOG_SEARCH_K, LOCAL_TRANSCRIPTION_WORKERS, ON_DEMAND_VIDEOS
from src.embedder import Embedder
from src.extractor import YouTubeExtractor
from src.utils import extract_channel_id
from src.video_transcript import fetch_video_transcript
from src.local_transcriber import VideoAccessBlockedError, VideoRestrictedError, VideoUnavailableError


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


def ingest_playlist(
    workspace_id: str,
    playlist_id: str,
    workspace_expires_at: int | None = None,
    progress_id: str | None = None,
    *,
    scope: str = "playlist",
    title_override: str | None = None,
    extractor: YouTubeExtractor | None = None,
) -> dict:
    """Index every accessible collection entry using the single-video caption/ASR path."""
    try:
        extractor = extractor or YouTubeExtractor()
        if title_override is None:
            details = extractor.youtube.playlists().list(
                part="snippet", id=playlist_id,
            ).execute().get("items", [])
            if not details:
                return {"ok": False, "message": "Public playlist was not found or is restricted."}
            title = clean_text(details[0].get("snippet", {}).get("title", "YouTube playlist"))
        else:
            title = title_override
        videos = []
        seen = set()
        page_token = None
        while True:
            page = extractor.youtube.playlistItems().list(
                part="snippet,contentDetails", playlistId=playlist_id,
                maxResults=50, pageToken=page_token,
            ).execute()
            for item in page.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId", "")
                duplicate = video_id in seen and bool(video_id)
                if video_id:
                    seen.add(video_id)
                snippet = item.get("snippet", {})
                videos.append({
                    "video_id": video_id,
                    "title": clean_text(snippet.get("title", "Untitled video")),
                    "description": clean_text(snippet.get("description", "")),
                    "published_at": snippet.get("publishedAt", ""),
                    "duplicate": duplicate,
                })
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    except Exception as error:
        log_exception_safely("Reading public playlist", error)
        return {"ok": False, "message": f"Could not access this public {scope}. Check that it exists and is public."}

    if not videos:
        return {"ok": False, "message": f"This {scope} has no accessible videos."}
    embedder = Embedder(workspace_id=workspace_id, expires_at=workspace_expires_at)
    catalog = [video for video in videos if not video["duplicate"] and re.fullmatch(r"[A-Za-z0-9_-]{11}", video["video_id"])]
    embedder.ingest_catalog(catalog)
    existing_ids = embedder.indexed_video_ids()
    completed = {
        index for index, video in enumerate(videos)
        if not video["duplicate"] and video["video_id"] in existing_ids
    }
    indexed = len(completed)
    chunks = sum(len(embedder.transcript_chunks(videos[index]["video_id"])) for index in completed)
    unavailable = 0
    failed = 0
    issues = []
    reasons = {}
    def publish_progress(message: str) -> None:
        if not progress_id or not workspace_expires_at:
            return
        waiting = [video for index, video in enumerate(videos) if index not in completed]
        get_export_store().put(f"jobprogress:{workspace_id}:{progress_id}", {
            "message": message,
            "scope": scope, "title": title,
            "catalog_count": len(videos), "video_count": indexed, "chunk_count": chunks,
            "coverage": {
                "discovered": len(videos), "transcribed": indexed, "indexed": indexed,
                "unavailable": unavailable, "failed": failed, "pending": len(waiting),
                "reasons": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items())],
            },
            "issues": (issues + [{"video_id": item["video_id"], "title": item["title"],
                                   "reason": "not_indexed_yet", "outcome": "pending"}
                                  for item in waiting])[:25],
            "issue_total": len(issues) + len(waiting),
        }, workspace_expires_at)

    publish_progress(f"Found {len(videos)} {scope} videos. {indexed} transcripts already indexed.")

    def finish_video(position: int, transcript: dict | None = None, error: Exception | None = None) -> None:
        nonlocal indexed, chunks, unavailable, failed
        video = videos[position]
        video_id = video["video_id"]
        reason = ""
        outcome = ""
        if video["duplicate"]:
            reason, outcome = "duplicate_playlist_entry", "unavailable"
        elif not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            reason, outcome = "video_unavailable", "unavailable"
        elif error is not None:
            if isinstance(error, VideoUnavailableError):
                reason, outcome = "video_unavailable", "unavailable"
            elif isinstance(error, VideoRestrictedError):
                reason, outcome = "age_restricted", "unavailable"
            elif isinstance(error, VideoAccessBlockedError):
                reason, outcome = "youtube_access_blocked", "failed"
            elif "No speech was detected" in str(error):
                reason, outcome = "no_speech", "unavailable"
            else:
                reason, outcome = "transcription_failed", "failed"
            log_exception_safely("Processing playlist video", error)
        else:
            try:
                assert transcript is not None
                transcript["title"] = video["title"] or transcript["title"]
                video_chunks = len(embedder._create_documents([transcript]))
                embedder.ingest([transcript], replace_existing=False)
                chunks += video_chunks
                indexed += 1
            except (RuntimeError, ValueError) as error:
                reason = "no_speech" if "No speech was detected" in str(error) else "transcription_failed"
                outcome = "unavailable" if reason == "no_speech" else "failed"
                log_exception_safely("Processing playlist video", error)
            except Exception as error:
                reason, outcome = "processing_failed", "failed"
                log_exception_safely("Indexing playlist video", error)
        if outcome:
            unavailable += outcome == "unavailable"
            failed += outcome == "failed"
            reasons[reason] = reasons.get(reason, 0) + 1
            issues.append({"video_id": video_id, "title": video["title"],
                           "reason": reason, "outcome": outcome})
        completed.add(position)
        publish_progress(f"Processed {len(completed)} of {len(videos)} {scope} videos.")

    # Keep only active transcriptions in memory. Large channels can otherwise
    # retain hundreds of finished Future results (including full transcripts).
    workers = min(LOCAL_TRANSCRIPTION_WORKERS, 1 if len(videos) > 12 else 4)
    remaining = iter(
        (position, video) for position, video in enumerate(videos)
        if position not in completed and not video["duplicate"]
        and re.fullmatch(r"[A-Za-z0-9_-]{11}", video["video_id"])
    )
    for position, video in enumerate(videos):
        if position not in completed and (
            video["duplicate"] or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video["video_id"])
        ):
            finish_video(position)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="transcribe") as pool:
        futures = {}

        def submit_next() -> None:
            try:
                position, video = next(remaining)
            except StopIteration:
                return
            future = pool.submit(fetch_video_transcript, video["video_id"], video["title"])
            futures[future] = position

        for _ in range(workers):
            submit_next()
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                position = futures.pop(future)
                try:
                    finish_video(position, transcript=future.result())
                except Exception as error:
                    finish_video(position, error=error)
                submit_next()
    coverage = {
        "discovered": len(videos), "transcribed": indexed,
        "indexed": indexed, "unavailable": unavailable,
        "failed": failed, "pending": 0,
        "reasons": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items())],
    }
    return {
        "ok": True, "scope": scope, "title": title,
        "catalog_count": len(videos), "video_count": indexed, "chunk_count": chunks,
        "coverage": coverage, "issues": issues[:25], "issue_total": len(issues),
        "warning": (f"{unavailable} unavailable and {failed} failed {scope} entries."
                    if unavailable or failed else ""),
    }


def ingest_channel_full(
    workspace_id: str,
    channel: str,
    workspace_expires_at: int | None = None,
    progress_id: str | None = None,
) -> dict:
    """Resolve a channel, then index its entire uploads playlist."""
    try:
        extractor = YouTubeExtractor()
        channel_id = extractor.get_channel_id(extract_channel_id(channel) or channel)
        details = extractor.youtube.channels().list(
            part="snippet,contentDetails", id=channel_id,
        ).execute().get("items", [])
        if not details:
            return {"ok": False, "message": "Public channel was not found or is restricted."}
        item = details[0]
        uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
        title = clean_text(item["snippet"].get("title", "YouTube channel"))
    except Exception as error:
        log_exception_safely("Reading public channel", error)
        return {"ok": False, "message": "Could not access this public channel. Check that it exists and is public."}
    return ingest_playlist(
        workspace_id, uploads, workspace_expires_at, progress_id,
        scope="channel", title_override=title, extractor=extractor,
    )


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
