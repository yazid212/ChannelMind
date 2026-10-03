"""Get one video's words without a paid transcript API."""

import requests
from youtube_transcript_api import YouTubeTranscriptApi

from config import YOUTUBE_TRANSCRIPT_TRUST_ENV
from .local_transcriber import transcribe_video_audio
from .utils import clean_text, log_exception_safely


def _video_title(video_id: str) -> str:
    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL({
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "skip_download": True, "js_runtimes": {"node": {}},
            "proxy": "",
            "socket_timeout": 20,
        }) as downloader:
            info = downloader.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            if info and info.get("title"):
                return clean_text(str(info["title"]))
    except Exception as error:
        log_exception_safely("Fetching video title", error)
    return f"YouTube video {video_id}"


def fetch_video_transcript(video_id: str) -> dict:
    """Prefer free captions; transcribe downloaded audio locally when needed."""
    session = requests.Session()
    session.trust_env = YOUTUBE_TRANSCRIPT_TRUST_ENV
    try:
        fetched = YouTubeTranscriptApi(http_client=session).fetch(video_id)
        segments = []
        for item in fetched:
            text = clean_text(item.text)
            if text:
                segments.append({
                    "text": text,
                    "start": int(item.start),
                    "duration": item.duration,
                })
        if segments:
            return {
                "video_id": video_id,
                "title": _video_title(video_id),
                "transcript": " ".join(item["text"] for item in segments),
                "segments": segments,
                "transcript_source": "youtube_captions",
            }
    except Exception as error:
        log_exception_safely("Fetching video captions", error)
    finally:
        session.close()
    return transcribe_video_audio(video_id)
