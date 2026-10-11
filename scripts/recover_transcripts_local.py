"""Fetch YouTube captions from this computer and write an importable ChannelMind bundle.

Usage:
    python scripts/recover_transcripts_local.py input.zip -o recovered.zip
    python scripts/recover_transcripts_local.py "https://www.youtube.com/playlist?list=..." -o playlist.zip

This keeps transcript requests on the user's network. It does not download video/audio
or bypass sign-in, age, privacy, or other YouTube access restrictions.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
MAX_VIDEOS = 5000
MAX_BUNDLE_BYTES = 60 * 1024 * 1024


def _read_json_bundle(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Input export is larger than 60 MB.")
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            matches = [item for item in archive.infolist() if item.filename == "channel.json"]
            if len(matches) != 1 or matches[0].file_size > MAX_BUNDLE_BYTES:
                raise ValueError("ZIP must contain one channel.json smaller than 60 MB.")
            raw = archive.read(matches[0])
    else:
        raw = data
    try:
        bundle = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Input must be a ChannelMind ZIP/JSON export or a YouTube playlist URL.") from error
    if (not isinstance(bundle, dict) or bundle.get("schema") != "channelmind.channel-transcripts"
            or bundle.get("version") != 1):
        raise ValueError("Input JSON is not a supported ChannelMind transcript export.")
    return bundle


def _discover_url(url: str, max_videos: int) -> dict:
    from yt_dlp import YoutubeDL

    options = {
        "extract_flat": "in_playlist", "ignoreerrors": True, "quiet": True,
        "no_warnings": True, "skip_download": True, "playlistend": max_videos,
        "socket_timeout": 20, "retries": 2,
    }
    with YoutubeDL(options) as client:
        info = client.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise ValueError("Could not read video or playlist details from YouTube.")
    entries = info.get("entries")
    if entries is None:
        entries = [info]
    videos = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("id") or "")
        if not VIDEO_ID.fullmatch(video_id) or video_id in seen:
            continue
        seen.add(video_id)
        videos.append({
            "video_id": video_id,
            "title": str(item.get("title") or f"YouTube video {video_id}")[:500],
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "status": "pending",
        })
        if len(videos) >= max_videos:
            break
    if not videos:
        raise ValueError("No accessible video entries were found.")

    parsed = urlparse(url)
    channel_url = any(
        part == "channel" or part.startswith("@")
        for part in parsed.path.split("/") if part
    )
    playlist_id = next(iter(parse_qs(parsed.query).get("list", [])), "")
    if not channel_url and not playlist_id and info.get("_type") == "playlist":
        playlist_id = info.get("id") or ""
    if not channel_url and playlist_id and PLAYLIST_ID.fullmatch(str(playlist_id)):
        kind, collection_id = "playlist", str(playlist_id)
        source = "youtube-public-playlist-transcripts"
    else:
        channel_id = str(info.get("channel_id") or info.get("id") or "")
        if not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("Channel exports need a YouTube channel ID (UC followed by 22 characters). Use a playlist URL or an existing ChannelMind export instead.")
        kind, collection_id = "channel", channel_id
        source = "youtube-public-transcripts"
    return {
        "schema": "channelmind.channel-transcripts", "version": 1,
        "source": source, "complete": False,
        "channel": {"kind": kind, "id": collection_id,
                    "title": str(info.get("title") or info.get("channel") or "YouTube collection")[:500]},
        "videos": videos,
    }


def _fetch_one(video: dict, language: str = "") -> tuple[dict | None, str | None]:
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        transcripts = YouTubeTranscriptApi().list(video["video_id"])
        tracks = list(transcripts)
        if not tracks:
            return None, "no_captions"
        tracks.sort(key=lambda track: (
            track.language_code != language if language else False,
            track.is_generated,
        ))
        track = tracks[0]
        fetched = track.fetch()
        segments = []
        for item in fetched:
            phrase = re.sub(r"\s+", " ", str(item.text)).strip()
            if not phrase:
                continue
            start = max(0.0, float(item.start))
            duration = max(0.0, float(item.duration))
            segments.append({"text": phrase, "start": start, "duration": duration})
        if not segments:
            return None, "no_captions"
        return {
            "video_id": video["video_id"], "title": video["title"],
            "transcript": " ".join(item["text"] for item in segments),
            "segments": segments, "language": str(track.language_code)[:20],
            "caption_kind": "ASR" if track.is_generated else "standard",
        }, None
    except Exception as error:  # Keep one unavailable video from stopping a whole collection.
        name = type(error).__name__
        reason = {
            "NoTranscriptFound": "no_captions",
            "TranscriptsDisabled": "no_captions",
            "VideoUnavailable": "video_unavailable",
            "AgeRestricted": "restricted",
            "RequestBlocked": "local_request_blocked",
            "IpBlocked": "local_request_blocked",
            "TooManyRequests": "local_rate_limited",
        }.get(name, "local_fetch_failed")
        return None, reason


def recover_bundle(bundle: dict, language: str = "", *, fetcher=_fetch_one,
                   progress=print) -> dict:
    if bundle.get("schema") != "channelmind.channel-transcripts" or bundle.get("version") != 1:
        raise ValueError("Unsupported ChannelMind bundle.")
    videos = bundle.get("videos")
    channel = bundle.get("channel")
    if not isinstance(videos, list) or not isinstance(channel, dict) or not videos:
        raise ValueError("Bundle must include a collection and videos.")
    if len(videos) > MAX_VIDEOS:
        raise ValueError(f"Bundle exceeds the {MAX_VIDEOS:,}-video safety limit.")
    kind = channel.get("kind", "channel")
    if (kind == "playlist" and not PLAYLIST_ID.fullmatch(str(channel.get("id", "")))):
        raise ValueError("Invalid playlist ID in bundle.")
    if (kind == "channel" and not CHANNEL_ID.fullmatch(str(channel.get("id", "")))):
        raise ValueError("Invalid channel ID in bundle.")
    seen = set()
    output = []
    connection_blocked = ""
    for number, original in enumerate(videos, start=1):
        if not isinstance(original, dict):
            raise ValueError("Bundle contains an invalid video entry.")
        video_id = original.get("video_id", "")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
            raise ValueError("Bundle contains an invalid or duplicate video ID.")
        seen.add(video_id)
        video = {"video_id": video_id,
                 "title": str(original.get("title") or f"YouTube video {video_id}")[:500],
                 "url": f"https://www.youtube.com/watch?v={video_id}"}
        if original.get("status") == "exported" and isinstance(original.get("segments"), list) and original["segments"]:
            video.update(original)
            video["url"] = f"https://www.youtube.com/watch?v={video_id}"
            output.append(video)
            continue
        if connection_blocked:
            video.update(status="skipped", reason=connection_blocked)
            output.append(video)
            continue
        progress(f"[{number}/{len(videos)}] Fetching {video['title']}")
        if number > 1:
            time.sleep(0.75)
        transcript, reason = fetcher(video, language)
        if transcript:
            video.update(transcript)
            video["status"] = "exported"
        else:
            video.update(status="skipped", reason=reason or "local_fetch_failed")
            if reason in {"local_request_blocked", "local_rate_limited"}:
                connection_blocked = reason
        output.append(video)
    result = dict(bundle)
    result.update(complete=True, videos=output)
    return result


def write_bundle(bundle: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError("Recovered transcript bundle exceeds the 60 MB import limit.")
    text_lines = [f"{bundle['channel'].get('title', 'YouTube collection')} — transcripts", ""]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for video in bundle["videos"]:
            text_lines.extend(["=" * 60, video["title"], video["url"], ""])
            if video["status"] != "exported":
                text_lines.extend([f"Transcript unavailable: {video.get('reason', 'unknown')}", ""])
                continue
            segments = video.get("segments", [])
            transcript_lines = [video["title"], video["url"], ""]
            for segment in segments:
                stamp = int(float(segment["start"]))
                line = f"[{stamp // 3600:02d}:{(stamp % 3600) // 60:02d}:{stamp % 60:02d}] {segment['text']}"
                text_lines.append(line)
                transcript_lines.append(line)
            text_lines.append("")
            archive.writestr(f"transcripts/{video['video_id']}.txt", "\n".join(transcript_lines) + "\n")
        archive.writestr("channel.json", encoded)
        archive.writestr("channel.txt", "\n".join(text_lines))
        archive.writestr(
            "README.txt",
            "ChannelMind local transcript export\n\n"
            "Captions were requested from the computer running the local recovery tool.\n"
            "Videos still listed as skipped could not be fetched; their reasons are in channel.json.\n",
        )
    path.write_bytes(output.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover accessible YouTube captions locally for ChannelMind import.")
    parser.add_argument("source", help="ChannelMind ZIP/JSON export or public YouTube playlist/channel URL")
    parser.add_argument("-o", "--output", type=Path, default=Path("channelmind-local-recovered.zip"))
    parser.add_argument("--language", default="", help="Preferred caption language, such as en or fr")
    parser.add_argument("--max-videos", type=int, default=500, help="Safety cap for URL discovery (default: 500)")
    args = parser.parse_args(argv)
    try:
        source_path = Path(args.source)
        if source_path.is_file():
            bundle = _read_json_bundle(source_path)
        elif args.source.startswith(("https://www.youtube.com/", "https://youtube.com/")):
            if not 1 <= args.max_videos <= MAX_VIDEOS:
                raise ValueError(f"--max-videos must be between 1 and {MAX_VIDEOS}.")
            bundle = _discover_url(args.source, args.max_videos)
        else:
            raise ValueError("Provide a ChannelMind ZIP/JSON export or a public YouTube playlist/channel URL.")
        result = recover_bundle(bundle, args.language)
        write_bundle(result, args.output)
        exported = sum(item.get("status") == "exported" for item in result["videos"])
        skipped = len(result["videos"]) - exported
        print(f"Saved {args.output}: {exported} transcripts recovered, {skipped} still unavailable.")
        print("Import this ZIP in ChannelMind using ‘Import a channel or playlist export’.")
        return 0 if exported else 1
    except Exception as error:
        print(f"Local transcript recovery failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
