import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from .settings import get_settings
from .export_store import get_export_store
from .tasks import expand_workspace, ingest_channel_full, ingest_playlist, ingest_video


@dataclass
class _LocalJob:
    workspace_id: str
    future: Future


class JobManager:
    def __init__(self):
        self._settings = get_settings()
        self._queue = None
        self._redis = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingestion")
        self._jobs: dict[str, _LocalJob] = {}
        self._collection_job_ids: set[str] = set()
        self._active_collections: dict[str, str] = {}
        self._lock = threading.Lock()

        if self._settings.redis_url:
            from redis import Redis
            from rq import Queue

            self._redis = Redis.from_url(self._settings.redis_url)
            self._queue = Queue("ingestion", connection=self._redis)

    def enqueue_export(self, workspace_id: str, export_id: str, mode: str = "collect") -> str:
        from .channel_export import collect_channel
        job_id = str(uuid.uuid4())
        if self._queue is not None:
            self._queue.enqueue(
                collect_channel, workspace_id, export_id, mode,
                job_id=job_id, job_timeout=3600, result_ttl=3600,
                failure_ttl=3600, meta={"workspace_id": workspace_id},
            )
        else:
            future = self._executor.submit(collect_channel, workspace_id, export_id, mode)
            with self._lock:
                self._jobs[job_id] = _LocalJob(workspace_id, future)
        return job_id

    def enqueue_video(self, workspace_id: str, video_id: str, workspace_expires_at: int) -> str:
        job_id = str(uuid.uuid4())
        if self._queue is not None:
            job = self._queue.enqueue(
                ingest_video, workspace_id, video_id, workspace_expires_at,
                job_id=job_id, job_timeout=7200, result_ttl=3600,
                failure_ttl=3600,
            )
            job.meta["workspace_id"] = workspace_id
            job.save_meta()
        else:
            future = self._executor.submit(ingest_video, workspace_id, video_id, workspace_expires_at)
            with self._lock:
                self._jobs[job_id] = _LocalJob(workspace_id, future)
        return job_id

    def enqueue_playlist(self, workspace_id: str, playlist_id: str, workspace_expires_at: int) -> str:
        key = workspace_id
        if self._queue is None:
            with self._lock:
                existing_id = self._active_collections.get(key)
                existing = self._jobs.get(existing_id) if existing_id else None
                if existing and not existing.future.done():
                    return existing_id
                job_id = str(uuid.uuid4())
                future = self._executor.submit(
                    ingest_playlist, workspace_id, playlist_id, workspace_expires_at, job_id,
                )
                self._jobs[job_id] = _LocalJob(workspace_id, future)
                self._collection_job_ids.add(job_id)
                self._active_collections[key] = job_id
                return job_id
        job_id = str(uuid.uuid4())
        job = self._queue.enqueue(
            ingest_playlist, workspace_id, playlist_id, workspace_expires_at,
            job_id,
            job_id=job_id, job_timeout=82800, result_ttl=3600, failure_ttl=3600,
        )
        job.meta["workspace_id"] = workspace_id
        job.meta["kind"] = "collection"
        job.save_meta()
        return job_id

    def enqueue(
        self,
        workspace_id: str,
        channel: str,
        workspace_expires_at: int,
    ) -> str:
        key = workspace_id
        if self._queue is None:
            with self._lock:
                existing_id = self._active_collections.get(key)
                existing = self._jobs.get(existing_id) if existing_id else None
                if existing and not existing.future.done():
                    return existing_id
                job_id = str(uuid.uuid4())
                future = self._executor.submit(
                    ingest_channel_full, workspace_id, channel, workspace_expires_at, job_id,
                )
                self._jobs[job_id] = _LocalJob(workspace_id=workspace_id, future=future)
                self._collection_job_ids.add(job_id)
                self._active_collections[key] = job_id
                return job_id
        job_id = str(uuid.uuid4())
        job = self._queue.enqueue(
            ingest_channel_full,
            workspace_id, channel, workspace_expires_at, job_id,
            job_id=job_id, job_timeout=82800, result_ttl=3600, failure_ttl=3600,
        )
        job.meta["workspace_id"] = workspace_id
        job.meta["kind"] = "collection"
        job.save_meta()
        return job_id

    def enqueue_expansion(
        self,
        workspace_id: str,
        question: str,
        workspace_expires_at: int,
    ) -> str:
        job_id = str(uuid.uuid4())
        if self._queue is not None:
            job = self._queue.enqueue(
                expand_workspace,
                workspace_id,
                question,
                workspace_expires_at,
                job_id=job_id,
                job_timeout=600,
                result_ttl=3600,
                failure_ttl=3600,
            )
            job.meta["workspace_id"] = workspace_id
            job.save_meta()
            return job_id

        future = self._executor.submit(
            expand_workspace,
            workspace_id,
            question,
            workspace_expires_at,
        )
        with self._lock:
            self._jobs[job_id] = _LocalJob(workspace_id=workspace_id, future=future)
        return job_id

    def status(self, job_id: str, workspace_id: str) -> dict | None:
        if self._redis is not None:
            from rq.exceptions import NoSuchJobError
            from rq.job import Job

            try:
                job = Job.fetch(job_id, connection=self._redis)
            except NoSuchJobError:
                return None
            if job.meta.get("workspace_id") != workspace_id:
                return None
            status = job.get_status(refresh=True)
            mapped = {
                "deferred": "queued",
                "scheduled": "queued",
                "queued": "queued",
                "started": "started",
                "finished": "finished",
                "failed": "failed",
                "stopped": "failed",
                "canceled": "failed",
            }.get(getattr(status, "value", status), "queued")
            result = job.result if mapped == "finished" else (
                get_export_store().get(f"jobprogress:{workspace_id}:{job_id}")
                if job.meta.get("kind") == "collection" else None
            )
            if mapped == "finished" and isinstance(result, dict) and result.get("ok") is False:
                mapped = "failed"
            return {"status": mapped, "result": result}

        with self._lock:
            local_job = self._jobs.get(job_id)
        if not local_job or local_job.workspace_id != workspace_id:
            return None
        if local_job.future.running():
            progress = get_export_store().get(f"jobprogress:{workspace_id}:{job_id}") if job_id in self._collection_job_ids else None
            return {"status": "started", "result": progress}
        if not local_job.future.done():
            progress = get_export_store().get(f"jobprogress:{workspace_id}:{job_id}") if job_id in self._collection_job_ids else None
            return {"status": "queued", "result": progress}
        self._collection_job_ids.discard(job_id)
        if local_job.future.exception() is not None:
            return {"status": "failed", "result": None}
        result = local_job.future.result()
        if isinstance(result, dict) and result.get("ok") is False:
            return {"status": "failed", "result": result}
        return {"status": "finished", "result": result}
