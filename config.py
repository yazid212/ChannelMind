"""Runtime configuration shared by the legacy UI and production API."""

import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


APP_ENV = os.getenv("APP_ENV", "development").lower()

# YouTube
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "small")
LOCAL_WHISPER_DEVICE = os.getenv("LOCAL_WHISPER_DEVICE", "cpu")
LOCAL_WHISPER_MODEL_PATH = os.getenv("LOCAL_WHISPER_MODEL_PATH", os.path.join("data", "models"))
MAX_VIDEOS = _int_env("MAX_VIDEOS", 10 if APP_ENV == "production" else 50)
MAX_CATALOG_VIDEOS = max(0, _int_env("MAX_CATALOG_VIDEOS", 2000))
ON_DEMAND_VIDEOS = max(1, min(_int_env("ON_DEMAND_VIDEOS", 2), 10))
CATALOG_SEARCH_K = max(
    ON_DEMAND_VIDEOS,
    min(_int_env("CATALOG_SEARCH_K", 12), 100),
)
YOUTUBE_TRANSCRIPT_TRUST_ENV = (
    os.getenv("YOUTUBE_TRANSCRIPT_TRUST_ENV", "false").lower() == "true"
)
WEBSHARE_PROXY_USERNAME = os.getenv("WEBSHARE_PROXY_USERNAME")
WEBSHARE_PROXY_PASSWORD = os.getenv("WEBSHARE_PROXY_PASSWORD")
WEBSHARE_PROXY_LOCATIONS = tuple(
    item.strip()
    for item in os.getenv("WEBSHARE_PROXY_LOCATIONS", "").split(",")
    if item.strip()
)
YOUTUBE_TRANSCRIPT_RETRIES = max(
    0,
    min(_int_env("YOUTUBE_TRANSCRIPT_RETRIES", 5), 20),
)

# A named model makes cost and behavior reproducible. Values can be overridden
# per deployment without changing source code.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").lower()
LLM_MODELS = {
    "openrouter": os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    "groq": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "grok": os.getenv("GROK_MODEL", "grok-4.3"),
    "nvidia": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
}
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_MAX_TOKENS = _int_env("LLM_MAX_TOKENS", 600)

# API keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

# Chunking and retrieval
CHUNK_SIZE = _int_env("CHUNK_SIZE", 1000)
TOP_K = _int_env("TOP_K", 3)
DIVERSE_FETCH_K = _int_env("DIVERSE_FETCH_K", 100)
MAX_HISTORY_MESSAGES = _int_env("MAX_HISTORY_MESSAGES", 4)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_DIMENSION = _int_env("EMBEDDING_DIMENSION", 384)
EMBEDDING_LOCAL_FILES_ONLY = (
    os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").lower() == "true"
)

# Production should use Qdrant; Chroma remains a convenient local fallback.
DATA_PATH = os.getenv("DATA_PATH", "data")
TRANSCRIPT_CACHE_PATH = os.getenv(
    "TRANSCRIPT_CACHE_PATH", os.path.join(DATA_PATH, "transcripts")
)
VECTORSTORE_PATH = os.getenv("VECTORSTORE_PATH", "vectorstore")
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "chroma").lower()
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "youtube_transcripts")
