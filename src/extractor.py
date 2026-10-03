import os
from typing import List, Dict, Optional
import requests
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

from .utils import (
    clean_text,
    extract_channel_id,
    load_transcripts_cache,
    log_exception_safely,
    logger,
    save_transcripts_cache,
)
from config import (
    MAX_VIDEOS,
    WEBSHARE_PROXY_LOCATIONS,
    WEBSHARE_PROXY_PASSWORD,
    WEBSHARE_PROXY_USERNAME,
    YOUTUBE_API_KEY,
    YOUTUBE_TRANSCRIPT_RETRIES,
    YOUTUBE_TRANSCRIPT_TRUST_ENV,
)


class YouTubeExtractor:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or YOUTUBE_API_KEY
        if not self.api_key:
            raise ValueError("YouTube API key is required. Set YOUTUBE_API_KEY in .env")

        self.youtube = build("youtube", "v3", developerKey=self.api_key)
        transcript_session = requests.Session()
        # Hosting/sandbox environments sometimes inject a proxy that permits
        # Google APIs but breaks YouTube transcript traffic. Direct requests
        # are the safe default; operators can explicitly opt into env proxies.
        transcript_session.trust_env = YOUTUBE_TRANSCRIPT_TRUST_ENV
        proxy_config = None
        if WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD:
            proxy_config = WebshareProxyConfig(
                proxy_username=WEBSHARE_PROXY_USERNAME,
                proxy_password=WEBSHARE_PROXY_PASSWORD,
                filter_ip_locations=list(WEBSHARE_PROXY_LOCATIONS),
                retries_when_blocked=YOUTUBE_TRANSCRIPT_RETRIES,
            )
        self.ytt_api = YouTubeTranscriptApi(
            http_client=transcript_session,
            proxy_config=proxy_config,
        )

    def get_channel_id(self, identifier: str) -> str:
        """Resolve any channel URL format to a proper UC... ID."""
        if identifier.startswith("UC") and len(identifier) == 24:
            return identifier

        if identifier.startswith("@"):
            handle = identifier[1:]
            response = self.youtube.channels().list(
                part="id",
                forHandle=handle
            ).execute()
            if response.get("items"):
                return response["items"][0]["id"]
            # Some valid public handles are not returned by forHandle even
            # though channel search resolves them. Keep a compatibility
            # fallback instead of rejecting a URL accepted by the UI.
            identifier = handle

        response = self.youtube.search().list(
            part="snippet",
            q=identifier,
            type="channel",
            maxResults=1
        ).execute()

        if not response.get("items"):
            raise ValueError(f"Could not find channel: {identifier}")

        return response["items"][0]["snippet"]["channelId"]

    def get_video_ids(self, channel_id: str, max_results: int = None) -> List[str]:
        """Fetch video IDs from uploads playlist."""
        max_results = max_results or MAX_VIDEOS

        channel_response = self.youtube.channels().list(
            part="contentDetails",
            id=channel_id
        ).execute()

        if not channel_response.get("items"):
            raise ValueError(f"Channel not found: {channel_id}")

        playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        video_ids = []
        next_page_token = None

        while len(video_ids) < max_results:
            response = self.youtube.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=min(50, max_results - len(video_ids)),
                pageToken=next_page_token
            ).execute()

            video_ids.extend([
                item["contentDetails"]["videoId"]
                for item in response.get("items", [])
            ])

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        logger.info(f"Found {len(video_ids)} videos")
        return video_ids[:max_results]

    def get_video_catalog(
        self,
        channel_id: str,
        max_results: int | None = None,
    ) -> List[Dict]:
        """Fetch lightweight metadata for a channel's upload catalog.

        This intentionally avoids transcript downloads. It gives retrieval a
        low-cost map of the channel so older videos can be selected and
        transcribed only when a question needs them.
        """
        channel_response = self.youtube.channels().list(
            part="contentDetails",
            id=channel_id,
        ).execute()
        if not channel_response.get("items"):
            raise ValueError(f"Channel not found: {channel_id}")

        playlist_id = channel_response["items"][0]["contentDetails"][
            "relatedPlaylists"
        ]["uploads"]
        videos: List[Dict] = []
        next_page_token = None

        while max_results is None or len(videos) < max_results:
            page_size = 50
            if max_results is not None:
                page_size = min(page_size, max_results - len(videos))
            response = self.youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=page_size,
                pageToken=next_page_token,
            ).execute()
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                video_id = item.get("contentDetails", {}).get("videoId")
                if not video_id:
                    continue
                videos.append({
                    "video_id": video_id,
                    "title": clean_text(snippet.get("title", "Untitled video")),
                    "description": clean_text(snippet.get("description", "")),
                    "published_at": snippet.get("publishedAt", ""),
                })

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        logger.info("Cataloged %d channel videos", len(videos))
        return videos[:max_results] if max_results is not None else videos

    def get_transcript(self, video_id: str) -> Optional[Dict]:
        """Fetch transcript with timestamps (youtube-transcript-api v1.2.4)."""
        try:
            # Get video title
            video_response = self.youtube.videos().list(
                part="snippet",
                id=video_id
            ).execute()

            if not video_response.get("items"):
                return None

            title = video_response["items"][0]["snippet"]["title"]

            # v1.2.4 API: instance method .fetch()
            # Returns FetchedTranscript which is iterable
            fetched = self.ytt_api.fetch(video_id)

            segments = []
            full_text_parts = []

            for snippet in fetched:
                text = clean_text(snippet.text)
                if text:
                    segments.append({
                        "text": text,
                        "start": int(snippet.start),
                        "duration": int(snippet.duration)
                    })
                    full_text_parts.append(text)

            if not segments:
                return None

            return {
                "video_id": video_id,
                "title": title,
                "transcript": " ".join(full_text_parts),
                "segments": segments
            }

        except Exception as error:
            log_exception_safely(f"Fetching transcript for video {video_id}", error)
            return None

    def process_channel_generator(self, channel_url: str, max_videos: int = None, use_cache: bool = True):
        """Generator yielding (current_index, total_videos, video_title, transcripts_list, from_cache)."""
        identifier = extract_channel_id(channel_url) or channel_url
        channel_id = self.get_channel_id(identifier)
        logger.info(f"Resolved channel ID: {channel_id}")

        if use_cache:
            cached = load_transcripts_cache(channel_id)
            if cached:
                cached_subset = cached[:max_videos] if max_videos else cached
                yield (len(cached_subset), len(cached_subset), "Loaded from cache", cached_subset, True)
                return

        video_ids = self.get_video_ids(channel_id, max_videos)
        total = len(video_ids)

        transcripts = []
        for idx, vid in enumerate(video_ids, start=1):
            result = self.get_transcript(vid)
            title = result["title"] if result else f"Video {vid} (No Transcript)"
            if result:
                transcripts.append(result)
            yield (idx, total, title, transcripts, False)

        if transcripts and use_cache:
            save_transcripts_cache(channel_id, transcripts)

        logger.info(f"Successfully extracted {len(transcripts)} transcripts")

    def process_channel(self, channel_url: str, max_videos: int = None, use_cache: bool = True) -> List[Dict]:
        """Full pipeline: URL -> Channel ID -> Videos -> Transcripts."""
        last_item = None
        for step in self.process_channel_generator(channel_url, max_videos, use_cache=use_cache):
            last_item = step
        return last_item[3] if last_item else []
