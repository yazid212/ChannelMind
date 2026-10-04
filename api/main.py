"""FastAPI entrypoint for container deployment."""

import os
import re

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError

from config import LLM_PROVIDER, LOCAL_WHISPER_MODEL, VECTOR_BACKEND
from src.rag import YouTubeRAG
from src.embedder import Embedder
from src.utils import format_timestamp, log_exception_safely

from .jobs import JobManager
from .export_routes import create_export_router
from .rate_limit import RateLimiter
from .schemas import (
    ChatRequest,
    ChatResponse,
    TranscriptChunkResponse,
    TranscriptResponse,
    ExpansionRequest,
    IngestionRequest,
    VideoIngestionRequest,
    IngestionResponse,
    JobResponse,
    WorkspaceResponse,
    expires_datetime,
)
from .security import (
    WorkspaceIdentity,
    client_address,
    issue_workspace_token,
    require_workspace,
)
from .settings import get_settings

settings = get_settings()
settings.validate()

app = FastAPI(
    title="YouTube RAG API",
    version="1.0.0",
    docs_url="/docs" if not settings.production else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins) + [f"chrome-extension://{item}" for item in settings.extension_ids],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

limiter = RateLimiter()
jobs = JobManager()
app.include_router(create_export_router(jobs, limiter))


@app.middleware("http")
async def request_guards(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            import_upload = (request.url.path.startswith("/api/v1/workspaces/") and request.url.path.endswith("/imports")) or request.url.path == "/api/v1/public-exports/resume-upload"
            too_large = int(content_length) > (60 * 1024 * 1024 if import_upload else settings.max_request_bytes)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        if too_large:
            return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz", include_in_schema=False)
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "vector_backend": VECTOR_BACKEND,
        "queue": "redis" if settings.redis_url else "local",
    }


@app.get("/readyz", include_in_schema=False)
def ready() -> dict:
    """Gate production traffic on the queue, vector database, and local ASR model."""
    if settings.production:
        if not os.path.isdir(LOCAL_WHISPER_MODEL):
            raise HTTPException(status_code=503, detail="Local speech model is unavailable")
        try:
            from redis import Redis
            from qdrant_client import QdrantClient

            Redis.from_url(
                settings.redis_url, socket_connect_timeout=3, socket_timeout=3
            ).ping()
            QdrantClient(
                url=os.environ["QDRANT_URL"],
                api_key=os.environ["QDRANT_API_KEY"],
                timeout=3,
            ).get_collections()
        except Exception as error:
            log_exception_safely("Checking production dependencies", error)
            raise HTTPException(status_code=503, detail="A required data service is unavailable") from None
    return {"status": "ready"}


@app.post(f"{settings.api_prefix}/workspaces", response_model=WorkspaceResponse, status_code=201)
def create_workspace(request: Request) -> WorkspaceResponse:
    limiter.check("workspace", client_address(request), limit=10, window=3600)
    identity, token = issue_workspace_token()
    return WorkspaceResponse(
        workspace_id=identity.workspace_id,
        token=token,
        expires_at=expires_datetime(identity.expires_at),
    )


def _assert_workspace(path_workspace_id: str, identity: WorkspaceIdentity) -> None:
    if path_workspace_id != identity.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


@app.post(
    f"{settings.api_prefix}/workspaces/{{workspace_id}}/ingestions",
    response_model=IngestionResponse,
    status_code=202,
)
def start_ingestion(
    workspace_id: str,
    payload: IngestionRequest,
    request: Request,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> IngestionResponse:
    _assert_workspace(workspace_id, identity)
    limiter.check("ingestion", client_address(request), limit=3, window=3600)
    max_videos = min(payload.max_videos, settings.max_public_videos)
    job_id = jobs.enqueue(
        workspace_id,
        payload.channel,
        max_videos,
        identity.expires_at,
    )
    return IngestionResponse(job_id=job_id, status="queued")


@app.post(
    f"{settings.api_prefix}/workspaces/{{workspace_id}}/videos",
    response_model=IngestionResponse,
    status_code=202,
)
def start_video_ingestion(
    workspace_id: str,
    payload: VideoIngestionRequest,
    request: Request,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> IngestionResponse:
    _assert_workspace(workspace_id, identity)
    limiter.check("ingestion", client_address(request), limit=3, window=3600)
    job_id = jobs.enqueue_video(workspace_id, payload.video, identity.expires_at)
    return IngestionResponse(job_id=job_id, status="queued")


@app.post(
    f"{settings.api_prefix}/workspaces/{{workspace_id}}/expansions",
    response_model=IngestionResponse,
    status_code=202,
)
def start_expansion(
    workspace_id: str,
    payload: ExpansionRequest,
    request: Request,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> IngestionResponse:
    _assert_workspace(workspace_id, identity)
    limiter.check("expansion", client_address(request), limit=20, window=3600)
    job_id = jobs.enqueue_expansion(
        workspace_id,
        payload.question,
        identity.expires_at,
    )
    return IngestionResponse(job_id=job_id, status="queued")


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}", response_model=JobResponse)
def job_status(
    job_id: str,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> JobResponse:
    result = jobs.status(job_id, identity.workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(job_id=job_id, **result)


@app.get(
    f"{settings.api_prefix}/workspaces/{{workspace_id}}/videos/{{video_id}}/transcript",
    response_model=TranscriptResponse,
)
async def video_transcript(
    workspace_id: str,
    video_id: str,
    request: Request,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> TranscriptResponse:
    _assert_workspace(workspace_id, identity)
    limiter.check("transcript", client_address(request), limit=30, window=60)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(status_code=404, detail="Transcript not found")
    embedder = await run_in_threadpool(Embedder, "youtube_transcripts", workspace_id)
    documents = await run_in_threadpool(embedder.transcript_chunks, video_id)
    if not documents:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return TranscriptResponse(
        video_id=video_id,
        title=documents[0].metadata["title"],
        transcript_source=documents[0].metadata.get("transcript_source", "unknown"),
        chunks=[TranscriptChunkResponse(
            text=doc.page_content,
            start_time=doc.metadata["start_time"],
            timestamp=format_timestamp(int(doc.metadata["start_time"])),
            url=doc.metadata["url"],
        ) for doc in documents],
    )


@app.post(
    f"{settings.api_prefix}/workspaces/{{workspace_id}}/chat",
    response_model=ChatResponse,
)
async def chat(
    workspace_id: str,
    payload: ChatRequest,
    request: Request,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> ChatResponse:
    _assert_workspace(workspace_id, identity)
    limiter.check("chat", client_address(request), limit=20, window=60)
    try:
        rag = await run_in_threadpool(
            YouTubeRAG,
            LLM_PROVIDER,
            workspace_id,
        )
        result = await run_in_threadpool(
            rag.ask,
            payload.question,
            [message.model_dump() for message in payload.history],
        )
        return ChatResponse(**result)
    except APIConnectionError as error:
        log_exception_safely("API answer generation", error)
        raise HTTPException(
            status_code=503,
            detail="The API server cannot reach the LLM provider. Check its network or proxy connection and retry.",
        ) from None
    except APIStatusError as error:
        log_exception_safely("API answer generation", error)
        detail = {
            401: "The LLM provider rejected its API key. Check the key and restart the API.",
            402: "The LLM provider reports insufficient credit.",
            429: "The LLM provider is rate limiting requests. Retry shortly.",
        }.get(error.status_code, f"The LLM provider returned HTTP {error.status_code}.")
        raise HTTPException(status_code=503, detail=detail) from None
    except ValueError as error:
        if str(error) in {
            "OPENROUTER_API_KEY not set in .env",
            "GROQ_API_KEY not set in .env",
            "GROK_API_KEY not set in .env",
            "NVIDIA_API_KEY not set in .env",
        }:
            raise HTTPException(
                status_code=503,
                detail=f"Q&A needs an LLM provider key. {error} Restart the API after adding it.",
            ) from None
        log_exception_safely("API answer generation", error)
        raise HTTPException(
            status_code=503,
            detail="Unable to answer right now. Please try again shortly.",
        ) from None
    except Exception as error:
        log_exception_safely("API answer generation", error)
        raise HTTPException(
            status_code=503,
            detail="Unable to answer right now. Please try again shortly.",
        ) from None


@app.delete(f"{settings.api_prefix}/workspaces/{{workspace_id}}", status_code=204)
async def delete_workspace(
    workspace_id: str,
    identity: WorkspaceIdentity = Depends(require_workspace),
) -> None:
    _assert_workspace(workspace_id, identity)
    embedder = await run_in_threadpool(Embedder, "youtube_transcripts", workspace_id)
    await run_in_threadpool(embedder.clear)
