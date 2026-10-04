"""Authenticated extension APIs for authorized channel exports."""

import secrets
import json
import re
import time
import uuid
import zipfile
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .channel_export import build_download, coverage_report, public_run, run_key, save_run, video_outcome
from .channel_export import transcript_key
from .bundle_import import MAX_BUNDLE_BYTES, parse_bundle
from .export_store import get_export_store
from .schemas import IngestionRequest
from .security import WorkspaceIdentity, client_address, require_workspace, sign_workspace_token
from .settings import get_settings
from .youtube_authorized import AuthorizedYouTube, YouTubeAccessError, finish_authorization, start_authorization
from src.utils import log_exception_safely


class AuthStart(BaseModel):
    extension_id: str = Field(pattern=r"^[a-p]{32}$")


class AuthFinish(BaseModel):
    state: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code: str = Field(min_length=1, max_length=4096)


class ExportRequest(BaseModel):
    channel_id: str = Field(pattern=r"^UC[A-Za-z0-9_-]{22}$")
    language: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9-]*$")


class PublicExportRequest(BaseModel):
    channel: str = Field(min_length=2, max_length=256)
    language: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9-]*$")

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        value = IngestionRequest.validate_channel(value)
        if value.startswith("https://"):
            parts = [part for part in urlparse(value).path.split("/") if part]
            if not parts or not (parts[0].startswith("@") or (parts[0] in {"channel", "c", "user"} and len(parts) > 1)):
                raise ValueError("Use a YouTube channel URL, not a video or playlist URL")
        return value


PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")


class PlaylistExportRequest(BaseModel):
    playlist: str = Field(min_length=10, max_length=512)
    language: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9-]*$")

    @field_validator("playlist")
    @classmethod
    def validate_playlist(cls, value: str) -> str:
        value = value.strip()
        if PLAYLIST_ID.fullmatch(value):
            return value
        parsed = urlparse(value)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
                or parsed.username or parsed.password or parsed.port
                or parsed.path not in {"/playlist", "/watch"}):
            raise ValueError("Use a public YouTube playlist URL or playlist ID")
        ids = parse_qs(parsed.query).get("list", [])
        if len(ids) != 1 or not PLAYLIST_ID.fullmatch(ids[0]):
            raise ValueError("The playlist URL needs a valid list parameter")
        return ids[0]


class HandoffRequest(BaseModel):
    code: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def access_error(error):
    code = {"setup": 503, "authorization": 401, "quota": 429, "forbidden": 403}.get(error.reason, 503)
    return HTTPException(status_code=code, detail=str(error))


def get_run(identity, export_id):
    run = get_export_store().get(run_key(identity.workspace_id, str(export_id)))
    if not run:
        raise HTTPException(404, "Export not found or expired.")
    return run


def create_export_router(jobs, limiter):
    router = APIRouter(prefix="/api/v1")

    def enqueue_run(store, run, mode="collect"):
        try:
            jobs.enqueue_export(run["workspace_id"], run["id"], mode)
        except Exception as error:
            log_exception_safely("Queueing channel export", error)
            run.update(status="paused", operation=mode, error_code="temporary",
                       message="The worker queue is unavailable. Resume when the service is ready.")
            save_run(store, run)

    @router.post("/youtube/authorize")
    def authorize(payload: AuthStart, request: Request, identity=Depends(require_workspace)):
        limiter.check("youtube-auth", client_address(request), limit=10, window=3600)
        try:
            return start_authorization(identity, payload.extension_id)
        except YouTubeAccessError as error:
            raise access_error(error) from None

    @router.post("/youtube/complete")
    def complete(payload: AuthFinish, identity=Depends(require_workspace)):
        try:
            finish_authorization(identity, payload.state, payload.code)
            return {"connected": True}
        except YouTubeAccessError as error:
            raise access_error(error) from None

    @router.get("/youtube/channels")
    def channels(request: Request, identity=Depends(require_workspace)):
        limiter.check("youtube-channels", identity.workspace_id, limit=30, window=3600)
        try:
            return {"channels": AuthorizedYouTube(identity.workspace_id).channels()}
        except YouTubeAccessError as error:
            raise access_error(error) from None

    @router.delete("/youtube/connection", status_code=204)
    def disconnect(identity=Depends(require_workspace)):
        AuthorizedYouTube(identity.workspace_id).disconnect()

    @router.post("/exports", status_code=202)
    def start_export(payload: ExportRequest, request: Request, identity=Depends(require_workspace)):
        limiter.check("channel-export", client_address(request), limit=5, window=3600)
        store = get_export_store()
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "A channel job is already running in this workspace.")
        active_id = store.get("active:" + identity.workspace_id)
        if active_id:
            active = store.get(run_key(identity.workspace_id, active_id))
            if active and active["status"] not in {"finished", "cancelled"}:
                if active["channel"]["id"] != payload.channel_id:
                    raise HTTPException(409, "Finish or cancel the current channel export first.")
                return public_run(active)
            store.release("active:" + identity.workspace_id, active_id)
        try:
            available = AuthorizedYouTube(identity.workspace_id).channels()
        except YouTubeAccessError as error:
            raise access_error(error) from None
        channel = next((c for c in available if c["id"] == payload.channel_id), None)
        if not channel:
            raise HTTPException(403, "Select a channel available to your connected YouTube account.")
        expires = min(identity.expires_at, int(time.time()) + get_settings().export_retention_seconds)
        run = {
            "id": str(uuid.uuid4()), "workspace_id": identity.workspace_id,
            "index_workspace_id": str(uuid.uuid4()),
            "channel": {"id": channel["id"], "title": channel["title"]},
            "uploads": channel["uploads"], "language": payload.language,
            "status": "queued", "videos": [], "catalog_complete": False,
            "created_at": time.time(), "expires_at": expires, "indexed": False,
            "message": "Waiting for the channel export worker…",
        }
        save_run(store, run)
        if not store.put("active:" + identity.workspace_id, run["id"], expires, only_if_absent=True):
            store.delete(run_key(identity.workspace_id, run["id"]))
            raise HTTPException(409, "Another export was just started. Reopen the extension dashboard.")
        enqueue_run(store, run)
        return public_run(run)

    @router.post("/public-exports", status_code=202)
    def start_public_export(payload: PublicExportRequest, request: Request, identity=Depends(require_workspace)):
        limiter.check("public-channel-export", client_address(request), limit=5, window=3600)
        channel_input = payload.channel
        store = get_export_store()
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "A channel job is already running in this workspace.")
        active_id = store.get("active:" + identity.workspace_id)
        if active_id:
            active = store.get(run_key(identity.workspace_id, active_id))
            if active and active["status"] not in {"finished", "cancelled"}:
                if active.get("channel_input") == channel_input:
                    return public_run(active)
                raise HTTPException(409, "Finish or cancel the current channel export first.")
            store.release("active:" + identity.workspace_id, active_id)
        expires = min(identity.expires_at, int(time.time()) + get_settings().export_retention_seconds)
        run = {
            "id": str(uuid.uuid4()), "workspace_id": identity.workspace_id,
            "index_workspace_id": str(uuid.uuid4()),
            "channel": {"id": "pending", "title": channel_input},
            "channel_input": channel_input, "source": "youtube-public-transcripts",
            "uploads": None, "language": payload.language,
            "status": "queued", "videos": [], "catalog_complete": False,
            "created_at": time.time(), "expires_at": expires, "indexed": False,
            "message": "Waiting for the public channel export worker…",
        }
        save_run(store, run)
        if not store.put("active:" + identity.workspace_id, run["id"], expires, only_if_absent=True):
            store.delete(run_key(identity.workspace_id, run["id"]))
            raise HTTPException(409, "Another export was just started.")
        enqueue_run(store, run)
        return public_run(run)

    @router.post("/public-playlist-exports", status_code=202)
    def start_playlist_export(payload: PlaylistExportRequest, request: Request, identity=Depends(require_workspace)):
        limiter.check("public-playlist-export", client_address(request), limit=5, window=3600)
        store = get_export_store()
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "A collection job is already running in this workspace.")
        active_id = store.get("active:" + identity.workspace_id)
        if active_id:
            active = store.get(run_key(identity.workspace_id, active_id))
            if active and active["status"] not in {"finished", "cancelled"}:
                if active.get("source") == "youtube-public-playlist-transcripts" and active.get("playlist_input") == payload.playlist:
                    return public_run(active)
                raise HTTPException(409, "Finish or cancel the current export first.")
            store.release("active:" + identity.workspace_id, active_id)
        expires = min(identity.expires_at, int(time.time()) + get_settings().export_retention_seconds)
        run = {
            "id": str(uuid.uuid4()), "workspace_id": identity.workspace_id,
            "index_workspace_id": str(uuid.uuid4()),
            "channel": {"id": payload.playlist, "title": payload.playlist, "kind": "playlist"},
            "playlist_input": payload.playlist, "source": "youtube-public-playlist-transcripts",
            "uploads": None, "language": payload.language,
            "status": "queued", "videos": [], "catalog_complete": False,
            "created_at": time.time(), "expires_at": expires, "indexed": False,
            "message": "Waiting for the public playlist export worker…",
        }
        save_run(store, run)
        if not store.put("active:" + identity.workspace_id, run["id"], expires, only_if_absent=True):
            store.delete(run_key(identity.workspace_id, run["id"]))
            raise HTTPException(409, "Another export was just started.")
        enqueue_run(store, run)
        return public_run(run)

    @router.post("/public-exports/resume-upload", status_code=202)
    async def resume_public_upload(request: Request, identity=Depends(require_workspace)):
        """Continue an older partial public export without repeating saved transcripts."""
        limiter.check("public-channel-export", client_address(request), limit=5, window=3600)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BUNDLE_BYTES:
                raise HTTPException(413, "Export file exceeds 60 MB.")
        try:
            channel, videos = await run_in_threadpool(parse_bundle, bytes(data), True)
        except (ValueError, zipfile.BadZipFile) as error:
            raise HTTPException(400, str(error)) from None
        if not any(video["status"] == "pending" for video in videos):
            raise HTTPException(400, "This export has no pending videos to resume.")
        store = get_export_store()
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "A channel job is already running in this workspace.")
        active_id = store.get("active:" + identity.workspace_id)
        if active_id:
            active = store.get(run_key(identity.workspace_id, active_id))
            if active and active["status"] not in {"finished", "cancelled"}:
                raise HTTPException(409, "Resume or stop the current export before uploading a partial ZIP.")
            store.release("active:" + identity.workspace_id, active_id)
        expires = min(identity.expires_at, int(time.time()) + get_settings().export_retention_seconds)
        is_playlist = channel.get("kind") == "playlist"
        run = {
            "id": str(uuid.uuid4()), "workspace_id": identity.workspace_id,
            "index_workspace_id": str(uuid.uuid4()), "channel": channel,
            **({"playlist_input": channel["id"], "source": "youtube-public-playlist-transcripts"}
               if is_playlist else {"channel_input": channel["id"], "source": "youtube-public-transcripts"}),
            "uploads": None, "language": "", "status": "queued", "videos": [],
            "catalog_complete": False, "created_at": time.time(), "expires_at": expires,
            "indexed": False, "message": "Continuing the partial public export…",
            "text_bytes": 0,
        }
        for video in videos:
            transcript = video.pop("transcript_data", None)
            run["videos"].append(video)
            if transcript:
                run["text_bytes"] += len(json.dumps(transcript, ensure_ascii=False).encode())
                store.put(transcript_key(run, video["video_id"]), transcript, expires)
        save_run(store, run)
        if not store.put("active:" + identity.workspace_id, run["id"], expires, only_if_absent=True):
            raise HTTPException(409, "Another export was just started.")
        enqueue_run(store, run)
        return public_run(run)

    @router.post("/workspaces/{workspace_id}/imports", status_code=202)
    async def import_file(workspace_id: uuid.UUID, request: Request, identity=Depends(require_workspace)):
        if str(workspace_id) != identity.workspace_id:
            raise HTTPException(404, "Workspace not found.")
        limiter.check("transcript-import", identity.workspace_id, limit=5, window=3600)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BUNDLE_BYTES:
                raise HTTPException(413, "Transcript file exceeds 60 MB.")
        try:
            channel, videos = await run_in_threadpool(parse_bundle, bytes(data))
        except (ValueError, zipfile.BadZipFile) as error:
            raise HTTPException(400, str(error)) from None
        if not any(video["status"] == "exported" for video in videos):
            raise HTTPException(400, "The file contains no usable transcripts.")
        store = get_export_store()
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "Another channel job is running in this workspace.")
        expires = min(identity.expires_at, int(time.time()) + get_settings().export_retention_seconds)
        run = {
            "id": str(uuid.uuid4()), "workspace_id": identity.workspace_id,
            "index_workspace_id": identity.workspace_id, "channel": channel,
            "source": "uploaded-channelmind-export", "status": "queued",
            "operation": "index", "before_index_status": "finished",
            "videos": [], "catalog_complete": True, "indexed": False,
            "created_at": time.time(), "expires_at": expires,
            "message": "Preparing uploaded transcripts for ChannelMind…",
        }
        for video in videos:
            transcript = video.pop("transcript_data", None)
            run["videos"].append(video)
            if transcript:
                store.put(transcript_key(run, video["video_id"]), transcript, expires)
        save_run(store, run)
        enqueue_run(store, run, "index")
        return public_run(run)

    @router.get("/exports/{export_id}")
    def export_status(export_id: uuid.UUID, identity=Depends(require_workspace)):
        return public_run(get_run(identity, export_id))

    @router.get("/exports/{export_id}/issues")
    def export_issues(
        export_id: uuid.UUID,
        outcome: Literal["unavailable", "failed", "pending"] | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        identity=Depends(require_workspace),
    ):
        run = get_run(identity, export_id)
        issues = [video for video in run["videos"]
                  if video_outcome(video) in {"unavailable", "failed", "pending"}
                  and (outcome is None or video_outcome(video) == outcome)]
        return {
            "total": len(issues), "offset": offset,
            "items": [{"video_id": video["video_id"], "title": video["title"],
                       "reason": video.get("reason", "not_checked_yet"),
                       "outcome": video_outcome(video)}
                      for video in issues[offset:offset + limit]],
        }

    @router.post("/exports/{export_id}/resume", status_code=202)
    def resume(export_id: uuid.UUID, request: Request, identity=Depends(require_workspace)):
        limiter.check("export-resume", identity.workspace_id, limit=20, window=3600)
        store = get_export_store()
        run = get_run(identity, export_id)
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "The worker is still active. Please wait before resuming.")
        if run["status"] == "finished":
            return public_run(run)
        store.delete("cancel:" + run["id"])
        run.update(status="queued", message="Resuming from saved progress…")
        save_run(store, run)
        enqueue_run(store, run, run.get("operation", "collect"))
        return public_run(run)

    @router.post("/exports/{export_id}/cancel")
    def cancel(export_id: uuid.UUID, identity=Depends(require_workspace)):
        store = get_export_store()
        run = get_run(identity, export_id)
        store.put("cancel:" + run["id"], True, run["expires_at"])
        if not store.get("worker:" + identity.workspace_id):
            run.update(status="cancelled", message="Stopped. Saved transcripts remain downloadable.")
            save_run(store, run)
        return {"requested": True}

    @router.get("/exports/{export_id}/download")
    def download(export_id: uuid.UUID, format: Literal["zip", "txt", "json"] = "zip", identity=Depends(require_workspace)):
        run = get_run(identity, export_id)
        if public_run(run)["status"] in {"queued", "running", "indexing"}:
            raise HTTPException(409, "Wait for processing to finish or pause before downloading.")
        output, content_type = build_download(get_export_store(), run, format)
        def chunks():
            try:
                while data := output.read(64 * 1024):
                    yield data
            finally:
                output.close()
        return StreamingResponse(chunks(), media_type=content_type, headers={
            "Content-Disposition": f'attachment; filename="channelmind-{run["channel"]["id"]}.{format}"',
        })

    @router.post("/exports/{export_id}/index", status_code=202)
    def index(export_id: uuid.UUID, identity=Depends(require_workspace)):
        limiter.check("export-index", identity.workspace_id, limit=5, window=3600)
        store = get_export_store()
        run = get_run(identity, export_id)
        if run.get("indexed"):
            return public_run(run)
        if store.get("worker:" + identity.workspace_id):
            raise HTTPException(409, "Wait for the current channel job to finish.")
        if not any(v["status"] == "exported" for v in run["videos"]):
            raise HTTPException(409, "No transcripts are available to index.")
        if run["status"] in {"queued", "running", "indexing"}:
            raise HTTPException(409, "Wait for the export to finish or pause first.")
        store.delete("cancel:" + run["id"])
        run.update(before_index_status=run["status"], status="queued", operation="index", message="Waiting for the transcript index worker…")
        save_run(store, run)
        enqueue_run(store, run, "index")
        return public_run(run)

    @router.post("/exports/{export_id}/handoff")
    def handoff(export_id: uuid.UUID, identity=Depends(require_workspace)):
        run = get_run(identity, export_id)
        if not run.get("indexed"):
            raise HTTPException(409, "Prepare the transcripts for ChannelMind first.")
        code = secrets.token_urlsafe(32)
        get_export_store().put("handoff:" + code, {
            "workspace_id": run["index_workspace_id"], "expires_at": run["expires_at"],
            "channel": run["channel"], "metrics": run["metrics"],
            "coverage": coverage_report(run),
        }, min(time.time() + 60, run["expires_at"]))
        return {"code": code}

    @router.post("/export-handoffs/claim")
    def claim(payload: HandoffRequest, request: Request):
        limiter.check("export-handoff", client_address(request), limit=30, window=60)
        data = get_export_store().get("handoff:" + payload.code, consume=True)
        if not data:
            raise HTTPException(404, "This import link expired or was already used. Open ChannelMind from the extension again.")
        identity = WorkspaceIdentity(data["workspace_id"], data["expires_at"])
        return {**data, "token": sign_workspace_token(identity)}

    return router
