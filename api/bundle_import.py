"""Validate portable transcript bundles before indexing untrusted file contents."""

import io
import json
import math
import re
import zipfile

from src.utils import clean_text

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
MAX_BUNDLE_BYTES = 60 * 1024 * 1024


def parse_bundle(data: bytes, allow_pending: bool = False) -> tuple[dict, list[dict]]:
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            matches = [info for info in archive.infolist() if info.filename == "channel.json"]
            if len(matches) != 1 or matches[0].file_size > MAX_BUNDLE_BYTES:
                raise ValueError("ZIP must contain one channel.json smaller than 60 MB.")
            raw = archive.read(matches[0])
    else:
        raw = data
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Transcript file is too large.")
    try:
        bundle = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("File is not a valid ChannelMind ZIP or JSON export.") from error
    if not isinstance(bundle, dict) or bundle.get("schema") != "channelmind.channel-transcripts" or bundle.get("version") != 1:
        raise ValueError("Unsupported ChannelMind export format.")
    source = bundle.get("source")
    if allow_pending and source not in {"youtube-public-transcripts", "youtube-public-playlist-transcripts"}:
        raise ValueError("Only a public channel or playlist export can be resumed.")
    channel = bundle.get("channel")
    videos = bundle.get("videos")
    if not isinstance(channel, dict):
        raise ValueError("Export has an invalid collection.")
    kind = channel.get("kind", "channel")
    collection_id = channel.get("id", "")
    if (kind == "playlist" and (source != "youtube-public-playlist-transcripts"
                               or not isinstance(collection_id, str) or not PLAYLIST_ID.fullmatch(collection_id))):
        raise ValueError("Export has an invalid playlist ID.")
    if (kind == "channel" and (source == "youtube-public-playlist-transcripts"
                              or not isinstance(collection_id, str) or not CHANNEL_ID.fullmatch(collection_id))):
        raise ValueError("Export has an invalid channel ID.")
    if kind not in {"channel", "playlist"}:
        raise ValueError("Export has an invalid collection type.")
    if not isinstance(videos, list) or not videos or len(videos) > 100_000:
        raise ValueError("Export must contain a channel video list.")
    normalized = []
    seen = set()
    for video in videos:
        if not isinstance(video, dict):
            raise ValueError("Export contains an invalid video entry.")
        video_id = video.get("video_id", "")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
            raise ValueError("Export contains an invalid or duplicate video ID.")
        seen.add(video_id)
        title = clean_text(str(video.get("title", "Untitled video")))[:500]
        entry = {"video_id": video_id, "title": title,
                 "url": "https://www.youtube.com/watch?v=" + video_id,
                 "status": video.get("status")}
        if entry["status"] == "exported":
            source_segments = video.get("segments")
            if not isinstance(source_segments, list) or not source_segments:
                raise ValueError(f"Exported video {video_id} has no timestamped segments.")
            segments = []
            for segment in source_segments:
                if not isinstance(segment, dict):
                    raise ValueError("Invalid transcript segment.")
                text = clean_text(str(segment.get("text", "")))
                start = segment.get("start")
                duration = segment.get("duration", 0)
                if (not text or isinstance(start, bool) or isinstance(duration, bool)
                        or not isinstance(start, (int, float)) or not isinstance(duration, (int, float))
                        or not math.isfinite(start) or not math.isfinite(duration)
                        or start < 0 or duration < 0):
                    raise ValueError("Invalid transcript segment timing or text.")
                segments.append({"text": text, "start": start, "duration": duration,
                                 "end": start + duration})
            entry["transcript_data"] = {"video_id": video_id, "title": title,
                                        "transcript": " ".join(item["text"] for item in segments),
                                        "segments": segments,
                                        "language": str(video.get("language", ""))[:20],
                                        "caption_kind": str(video.get("caption_kind", ""))[:20]}
            entry["language"] = entry["transcript_data"]["language"]
            entry["caption_kind"] = entry["transcript_data"]["caption_kind"]
        elif entry["status"] == "skipped":
            entry["reason"] = str(video.get("reason", "unavailable"))[:100]
        elif entry["status"] == "pending" and allow_pending:
            pass
        else:
            raise ValueError("Import a finished export with explicit video statuses.")
        normalized.append(entry)
    collection = {"id": collection_id, "title": clean_text(str(channel.get("title", "Collection")))[:500]}
    if kind == "playlist":
        collection["kind"] = "playlist"
    return collection, normalized
