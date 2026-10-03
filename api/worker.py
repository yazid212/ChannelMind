"""Start the ingestion worker using the deployment's REDIS_URL."""

import os

from redis import Redis
from rq import Queue, Worker

from config import LOCAL_WHISPER_MODEL, VECTOR_BACKEND, YOUTUBE_API_KEY
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required for the ingestion worker")
    if settings.production:
        if len(settings.app_secret) < 32 or settings.app_secret in {
            "development-only-change-me",
            "local-development-secret-change-before-deploying",
        }:
            raise RuntimeError("A unique APP_SECRET is required for the ingestion worker")
        if VECTOR_BACKEND != "qdrant" or not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
            raise RuntimeError("Qdrant configuration is required for the ingestion worker")
        if not YOUTUBE_API_KEY:
            raise RuntimeError("YOUTUBE_API_KEY is required for the channel workflow")
        if not os.path.isdir(LOCAL_WHISPER_MODEL):
            raise RuntimeError("The local speech model is not bundled in the worker image")

    connection = Redis.from_url(settings.redis_url)
    connection.ping()
    queue = Queue("ingestion", connection=connection)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
