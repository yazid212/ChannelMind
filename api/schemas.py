from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse
import re

from pydantic import BaseModel, Field, field_validator


class WorkspaceResponse(BaseModel):
    workspace_id: str
    token: str
    expires_at: datetime


class IngestionRequest(BaseModel):
    channel: str = Field(min_length=2, max_length=256)
    max_videos: int = Field(default=5, ge=1, le=50)

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        value = value.strip()
        if value.startswith("UC") and len(value) == 24:
            return value
        if value.startswith("@") and 2 <= len(value) <= 101:
            return value
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.hostname in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
        }:
            return value
        raise ValueError("Use a YouTube channel URL, @handle, or channel ID")


class VideoIngestionRequest(BaseModel):
    video: str = Field(min_length=11, max_length=256)

    @field_validator("video")
    @classmethod
    def validate_video(cls, value: str) -> str:
        value = value.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"
        }:
            raise ValueError("Use a public YouTube video URL or 11-character video ID")
        if parsed.hostname in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/")
        elif parsed.path == "/watch":
            from urllib.parse import parse_qs
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("Use a public YouTube video URL or 11-character video ID")
        return video_id


class IngestionResponse(BaseModel):
    job_id: str
    status: Literal["queued", "started", "finished", "failed"]


class ExpansionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class JobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "started", "finished", "failed"]
    result: dict | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=4)


class SourceResponse(BaseModel):
    video_id: str
    title: str
    url: str
    start_time: float
    timestamp: str
    quote: str


class TranscriptChunkResponse(BaseModel):
    text: str
    start_time: float
    timestamp: str
    url: str


class TranscriptResponse(BaseModel):
    video_id: str
    title: str
    transcript_source: str
    chunks: list[TranscriptChunkResponse]


class UsageResponse(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceResponse]
    usage: UsageResponse | None = None
    retrieved_chunks: int


def expires_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)
