import re
import logging
from typing import Optional

# Setup logging once, use everywhere
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("youtube-rag")


def log_exception_safely(operation: str, error: Exception) -> None:
    """Log an exception without serializing credentials or response bodies."""
    logger.error("%s failed (%s)", operation, type(error).__name__)


def clean_text(text: str) -> str:
    """Clean raw transcript text."""
    if not text:
        return ""

    # Remove [Music], (Applause), etc.
    text = re.sub(r"[\[\(].*?[\]\)]", "", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    # Fix multiple periods
    text = re.sub(r"\.{2,}", ".", text)

    return text.strip()


def extract_channel_id(url: str) -> Optional[str]:
    """
    Extract channel identifier from various YouTube URL formats.
    Supports: /channel/UC..., /c/CustomName, /user/Username, /@Handle
    """
    # Direct channel ID
    if match := re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", url):
        return match.group(1)

    # Handle, custom name, or user
    if match := re.search(r"youtube\.com/(?:c/|user/|@)([a-zA-Z0-9_-]+)", url):
        return match.group(1)

    return None


def format_timestamp(seconds: int) -> str:
    """Convert seconds to MM:SS or HH:MM:SS."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


import json
import os
import tempfile

from config import TRANSCRIPT_CACHE_PATH

CACHE_DIR = os.path.abspath(TRANSCRIPT_CACHE_PATH)

def save_transcripts_cache(channel_id: str, transcripts: list) -> str:
    """Save transcripts list to disk under data/transcripts/<channel_id>.json."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", channel_id):
        raise ValueError("Invalid channel identifier")
    os.makedirs(CACHE_DIR, exist_ok=True)
    file_path = os.path.join(CACHE_DIR, f"{channel_id}.json")
    fd, temporary_path = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(transcripts, stream, indent=2, ensure_ascii=False)
        os.replace(temporary_path, file_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
    logger.info(f"Saved {len(transcripts)} transcripts to cache: {file_path}")
    return file_path

def load_transcripts_cache(channel_id: str) -> Optional[list]:
    """Load transcripts list from disk cache if exists."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", channel_id):
        return None
    file_path = os.path.join(CACHE_DIR, f"{channel_id}.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"Loaded {len(data)} transcripts from cache: {file_path}")
            return data
        except Exception as error:
            log_exception_safely(f"Loading transcript cache {file_path}", error)
    return None

def get_cached_channels() -> list:
    """Return list of channel cache files summary."""
    if not os.path.exists(CACHE_DIR):
        return []
    result = []
    for fname in os.listdir(CACHE_DIR):
        if fname.endswith(".json"):
            channel_id = fname[:-5]
            fpath = os.path.join(CACHE_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "channel_id": channel_id,
                    "video_count": len(data),
                    "file_path": fpath
                })
            except Exception as error:
                log_exception_safely(f"Reading cached channel summary {fpath}", error)
    return result
