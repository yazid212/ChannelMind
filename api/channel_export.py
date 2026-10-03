"""Resumable channel caption collection and portable transcript bundles."""

import html
import io
import json
import re
import secrets
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse

from src.utils import log_exception_safely
from src.utils import clean_text, extract_channel_id

from .export_store import get_export_store
from .youtube_authorized import AuthorizedYouTube, YouTubeAccessError

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TIMING = re.compile(r"^((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d{3})")


def seconds(timestamp):
    result = 0.0
    for part in timestamp.replace(",", ".").split(":"):
        result = result * 60 + float(part)
    return result


def parse_vtt(content):
    segments = []
    for block in re.split(r"\n\s*\n", content.lstrip("\ufeff").replace("\r\n", "\n")):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            continue
        for index, line in enumerate(lines[:2]):
            match = TIMING.match(line.strip())
            if not match:
                continue
            text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(lines[index + 1:]))).strip()
            text = " ".join(text.split())
            start, end = seconds(match[1]), seconds(match[2])
            if text and end > start:
                if segments and segments[-1]["text"] == text and start <= segments[-1]["end"]:
                    segments[-1]["end"] = max(segments[-1]["end"], end)
                    segments[-1]["duration"] = segments[-1]["end"] - segments[-1]["start"]
                else:
                    segments.append({"text": text, "start": start, "end": end, "duration": end - start})
            break
    return segments


def stamp(value):
    value = int(value)
    return f"{value // 3600:02}:{value // 60 % 60:02}:{value % 60:02}"


def run_key(workspace_id, run_id):
    return f"run:{workspace_id}:{run_id}"


def save_run(store, run):
    run["updated_at"] = time.time()
    store.put(run_key(run["workspace_id"], run["id"]), run, run["expires_at"])


def transcript_key(run, video_id):
    return f"text:{run['workspace_id']}:{run['id']}:{video_id}"


def public_run(run):
    videos = run["videos"]
    successful = sum(v["status"] == "exported" for v in videos)
    skipped = sum(v["status"] == "skipped" for v in videos)
    status = run["status"]
    if status in {"running", "indexing"} and not get_export_store().get("worker:" + run["workspace_id"]):
        status = "interrupted"
    return {
        "id": run["id"], "channel": run["channel"], "status": status,
        "message": run.get("message", ""), "error_code": run.get("error_code"),
        "retry_after": run.get("retry_after"),
        "catalog_complete": run["catalog_complete"], "total": len(videos),
        "processed": successful + skipped, "exported": successful, "skipped": skipped,
        "indexed": run.get("indexed", False), "metrics": run.get("metrics"),
        "indexed_count": len(run.get("indexed_video_ids", [])),
        "expires_at": datetime.fromtimestamp(run["expires_at"], timezone.utc).isoformat(),
        "issues": [{"title": v["title"], "reason": v.get("reason", "unavailable")}
                   for v in videos if v["status"] == "skipped"][:25],
    }


def collect_channel(workspace_id, run_id, mode="collect"):
    """Only identifiers enter the queue; OAuth grants stay in encrypted storage."""
    store = get_export_store()
    run = store.get(run_key(workspace_id, run_id))
    if not run:
        return {"ok": False}
    lease_key = "worker:" + workspace_id
    lease = secrets.token_urlsafe(24)
    # Longer than the worker timeout. A hard-killed worker is recoverable after
    # this lease expires; progress is committed after each video.
    if not store.put(lease_key, lease, min(time.time() + 3700, run["expires_at"]), only_if_absent=True):
        return {"ok": False, "message": "A channel job is already running."}
    try:
        if mode == "index":
            return index_export(store, run)
        if run["status"] == "finished":
            return {"ok": True}
        run["operation"] = "collect"
        run["indexed"] = False
        run.update(status="running", message="Discovering channel videos…", error_code=None, retry_after=None)
        save_run(store, run)
        if run.get("source") in {"youtube-public-transcripts", "youtube-public-playlist-transcripts"}:
            return collect_public_channel(store, run)
        api = AuthorizedYouTube(workspace_id)
        known = {video["video_id"] for video in run["videos"]}
        while not run["catalog_complete"]:
            if cancelled(store, run):
                return {"ok": False}
            response = api.video_page(run["uploads"], run.get("page_token"))
            for item in response.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId", "")
                if not VIDEO_ID.fullmatch(video_id) or video_id in known:
                    continue
                snippet = item.get("snippet", {})
                run["videos"].append({
                    "video_id": video_id, "title": snippet.get("title", "Untitled video"),
                    "description": snippet.get("description", ""),
                    "published_at": item.get("contentDetails", {}).get("videoPublishedAt", snippet.get("publishedAt", "")),
                    "url": "https://www.youtube.com/watch?v=" + video_id,
                    "status": "pending",
                })
                known.add(video_id)
            run["page_token"] = response.get("nextPageToken")
            run["catalog_complete"] = not bool(run["page_token"])
            save_run(store, run)

        for video in run["videos"]:
            if video["status"] != "pending":
                continue
            if cancelled(store, run):
                return {"ok": False}
            run["message"] = "Collecting captions: " + video["title"]
            save_run(store, run)
            try:
                caption, language, kind = api.caption(video["video_id"], run["language"])
                segments = parse_vtt(caption)
                if not segments:
                    raise YouTubeAccessError("no_captions", "Caption track contained no timed speech.")
                transcript = {
                    "video_id": video["video_id"], "title": video["title"],
                    "transcript": " ".join(segment["text"] for segment in segments),
                    "segments": segments, "language": language, "caption_kind": kind,
                }
                # A bounded bundle protects the shared Redis instance; never
                # truncate text silently when the deployment budget is reached.
                size = len(json.dumps(transcript, ensure_ascii=False).encode())
                if run.get("text_bytes", 0) + size > 50 * 1024 * 1024:
                    raise YouTubeAccessError("capacity", "Export reached the 50 MB text budget. Download the saved partial bundle.")
                store.put(transcript_key(run, video["video_id"]), transcript, run["expires_at"])
                run["text_bytes"] = run.get("text_bytes", 0) + size
                video.update(status="exported", language=language, caption_kind=kind)
            except YouTubeAccessError as error:
                if error.reason in {"quota", "temporary", "authorization", "capacity"}:
                    raise
                video.update(status="skipped", reason=error.reason)
            save_run(store, run)
        if cancelled(store, run):
            return {"ok": False}
        run.update(status="finished", message="Export complete. Unavailable captions are listed in the report.")
        save_run(store, run)
        return {"ok": True}
    except YouTubeAccessError as error:
        retry_count = run.get("retry_count", 0) + 1 if error.reason == "temporary" else 0
        retry_after = time.time() + min(300 * 3 ** (retry_count - 1), 3600) if 0 < retry_count <= 3 else None
        run.update(status="paused", message=str(error), error_code=error.reason,
                   retry_count=retry_count, retry_after=retry_after)
        save_run(store, run)
        return {"ok": False}
    except Exception as error:
        log_exception_safely("Channel caption export", error)
        api_status = getattr(getattr(error, "resp", None), "status", None)
        quota_limited = api_status in {403, 429} and "quota" in str(error).lower()
        run.update(
            status="paused",
            message=("YouTube Data API quota is exhausted. Resume after quota is restored."
                     if quota_limited else "Processing was interrupted. Resume to retry from saved progress."),
            error_code="quota" if quota_limited else "temporary",
        )
        if not quota_limited:
            run["retry_count"] = run.get("retry_count", 0) + 1
            run["retry_after"] = (time.time() + min(300 * 3 ** (run["retry_count"] - 1), 3600)
                                  if run["retry_count"] <= 3 else None)
        if run["expires_at"] > time.time():
            save_run(store, run)
        return {"ok": False}
    finally:
        store.release(lease_key, lease)


def cancelled(store, run):
    if store.get("cancel:" + run["id"]):
        run.update(status="cancelled", message="Stopped. Completed transcripts can still be downloaded.")
        save_run(store, run)
        return True
    return False


def collect_public_channel(store, run):
    """Collect captions from a public channel or playlist without OAuth."""
    from src.extractor import YouTubeExtractor

    try:
        extractor = YouTubeExtractor()
    except ValueError as error:
        raise YouTubeAccessError("setup", str(error)) from error
    if not run.get("uploads"):
        if run.get("source") == "youtube-public-playlist-transcripts":
            playlist_id = run["playlist_input"]
            details = extractor.youtube.playlists().list(
                part="snippet", id=playlist_id,
            ).execute().get("items", [])
            if not details:
                raise YouTubeAccessError("not_found", "Public playlist was not found or is not accessible.")
            run["channel"] = {"id": playlist_id, "title": clean_text(details[0]["snippet"]["title"]),
                              "kind": "playlist"}
            run["uploads"] = playlist_id
        else:
            identifier = run["channel_input"]
            if identifier.startswith("https://"):
                path = urlparse(identifier).path.split("/")
                identifier = path[1] if len(path) > 1 and path[1].startswith("@") else (extract_channel_id(identifier) or identifier)
            channel_id = extractor.get_channel_id(identifier)
            details = extractor.youtube.channels().list(
                part="snippet,contentDetails", id=channel_id,
            ).execute().get("items", [])
            if not details:
                raise YouTubeAccessError("not_found", "Public channel was not found.")
            run["channel"] = {"id": channel_id, "title": clean_text(details[0]["snippet"]["title"])}
            run["uploads"] = details[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        save_run(store, run)

    known = {video["video_id"] for video in run["videos"]}
    while not run["catalog_complete"]:
        if cancelled(store, run):
            return {"ok": False}
        response = extractor.youtube.playlistItems().list(
            part="snippet,contentDetails", playlistId=run["uploads"],
            maxResults=50, pageToken=run.get("page_token"),
        ).execute()
        for item in response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId", "")
            if not VIDEO_ID.fullmatch(video_id) or video_id in known:
                continue
            snippet = item.get("snippet", {})
            run["videos"].append({
                "video_id": video_id, "title": clean_text(snippet.get("title", "Untitled video")),
                "description": clean_text(snippet.get("description", "")),
                "published_at": item.get("contentDetails", {}).get("videoPublishedAt", snippet.get("publishedAt", "")),
                "url": "https://www.youtube.com/watch?v=" + video_id,
                "status": "pending",
            })
            known.add(video_id)
        run["page_token"] = response.get("nextPageToken")
        run["catalog_complete"] = not bool(run["page_token"])
        run["message"] = f"Found {len(run['videos'])} videos; checking public transcripts…"
        save_run(store, run)

    for video in run["videos"]:
        if video["status"] != "pending":
            continue
        if cancelled(store, run):
            return {"ok": False}
        run["message"] = "Collecting transcript: " + video["title"]
        save_run(store, run)
        try:
            tracks = list(extractor.ytt_api.list(video["video_id"]))
            if not tracks:
                video.update(status="skipped", reason="no_captions")
                save_run(store, run)
                continue
            preferred = run.get("language")
            tracks.sort(key=lambda track: (
                track.language_code != preferred if preferred else False,
                track.is_generated,
            ))
            track = tracks[0]
            fetched = track.fetch()
            segments = []
            for snippet in fetched:
                phrase = clean_text(snippet.text)
                if phrase:
                    start = float(snippet.start)
                    duration = max(0.0, float(snippet.duration))
                    segments.append({"text": phrase, "start": start,
                                     "end": start + duration, "duration": duration})
            if not segments:
                video.update(status="skipped", reason="no_captions")
            else:
                transcript = {
                    "video_id": video["video_id"], "title": video["title"],
                    "transcript": " ".join(segment["text"] for segment in segments),
                    "segments": segments, "language": track.language_code,
                    "caption_kind": "ASR" if track.is_generated else "standard",
                }
                size = len(json.dumps(transcript, ensure_ascii=False).encode())
                if run.get("text_bytes", 0) + size > 50 * 1024 * 1024:
                    raise YouTubeAccessError("capacity", "Export reached the 50 MB text budget. Download the partial bundle.")
                store.put(transcript_key(run, video["video_id"]), transcript, run["expires_at"])
                run["text_bytes"] = run.get("text_bytes", 0) + size
                video.update(status="exported", language=track.language_code,
                             caption_kind=transcript["caption_kind"])
                run["retry_count"] = 0
        except YouTubeAccessError:
            raise
        except Exception as error:
            name = type(error).__name__
            if name in {"RequestBlocked", "IpBlocked", "TooManyRequests"}:
                raise YouTubeAccessError("temporary", "YouTube blocked transcript requests from this connection. Resume later or run the API from another permitted network.") from error
            if name in {"NoTranscriptFound", "TranscriptsDisabled", "VideoUnavailable", "AgeRestricted"}:
                video.update(status="skipped", reason=name)
            else:
                raise YouTubeAccessError("temporary", f"Transcript retrieval stopped at {video['video_id']}: {name}. Resume to retry.") from error
        save_run(store, run)
    run.update(status="finished", message="Public transcript export complete. Unavailable captions are listed in the report.")
    save_run(store, run)
    return {"ok": True}


def index_export(store, run):
    from src.embedder import Embedder

    if run.get("indexed"):
        return {"ok": True}
    run["operation"] = "index"
    run.update(status="indexing", message="Preparing transcripts for ChannelMind…")
    save_run(store, run)
    # Give the website a separate search workspace. Its token cannot authorize
    # YouTube calls or reach the extension's stored OAuth grant.
    embedder = Embedder(workspace_id=run["index_workspace_id"], expires_at=run["expires_at"])
    if not run.get("index_started"):
        embedder.clear()
        embedder.ingest_catalog(run["videos"])
        run["index_started"] = True
        run["indexed_video_ids"] = []
        run["indexed_chunks"] = 0
        save_run(store, run)
    indexed = set(run.get("indexed_video_ids", []))
    count, chunks = len(indexed), run.get("indexed_chunks", 0)
    for video in run["videos"]:
        if cancelled(store, run):
            return {"ok": False}
        if video["status"] != "exported" or video["video_id"] in indexed:
            continue
        transcript = store.get(transcript_key(run, video["video_id"]))
        if not transcript:
            raise ValueError("Export data expired")
        # RAG uses second-resolution links; preserve precise timings in exports.
        for segment in transcript["segments"]:
            segment["start"] = int(segment["start"])
        chunks += len(embedder._create_documents([transcript]))
        embedder.ingest([transcript], replace_existing=False)
        count += 1
        indexed.add(video["video_id"])
        run["indexed_video_ids"].append(video["video_id"])
        run["indexed_chunks"] = chunks
        run["message"] = f"Preparing {count} transcripts for ChannelMind…"
        save_run(store, run)
    run.update(indexed=True, operation="collect", status=run.get("before_index_status", "finished"), message="Ready to ask questions in ChannelMind.", metrics={
        "catalog": len(run["videos"]), "videos": count, "chunks": chunks,
    })
    save_run(store, run)
    return {"ok": True}


def bundle_data(store, run):
    videos = []
    for video in run["videos"]:
        entry = dict(video)
        if video["status"] == "exported":
            transcript = store.get(transcript_key(run, video["video_id"]))
            if not transcript:
                raise ValueError("Transcript data expired")
            entry.update(transcript)
        videos.append(entry)
    return {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "channel": run["channel"], "source": run.get("source", "youtube-authorized-captions"),
        "created_at": datetime.fromtimestamp(run["created_at"], timezone.utc).isoformat(),
        "complete": run["catalog_complete"] and all(v["status"] != "pending" for v in videos),
        "videos": videos,
    }


def video_text(video):
    lines = [video["title"], video["url"], ""]
    if video["status"] != "exported":
        return "\n".join(lines + ["Transcript unavailable: " + video.get("reason", video["status"]), ""])
    lines.extend(f"[{stamp(s['start'])}] {s['text']}" for s in video["segments"])
    return "\n".join(lines) + "\n"


def build_download(store, run, format_name):
    bundle = bundle_data(store, run)
    text = f"{run['channel']['title']} — channel transcripts\n" + "\n".join(
        "\n" + "=" * 60 + "\n" + video_text(video) for video in bundle["videos"]
    )
    encoded = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    if format_name == "json":
        return io.BytesIO(encoded), "application/json"
    if format_name == "txt":
        return io.BytesIO(text.encode("utf-8")), "text/plain; charset=utf-8"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("channel.txt", text)
        archive.writestr("channel.json", encoded)
        archive.writestr("README.txt", "ChannelMind caption export\n\n" +
                         ("PARTIAL EXPORT: Some videos are still pending. Resume the export in the extension.\n\n"
                          if not bundle["complete"] else "") +
                         "channel.json preserves timestamps, language, video links and per-video status.\nMissing captions are reported explicitly. No video/audio files are downloaded.\n")
        for video in bundle["videos"]:
            if video["status"] == "exported":
                archive.writestr("transcripts/" + video["video_id"] + ".txt", video_text(video))
    output.seek(0)
    return output, "application/zip"
