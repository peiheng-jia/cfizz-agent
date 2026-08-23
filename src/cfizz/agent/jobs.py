"""In-process asynchronous rendering jobs for the local Agent workspace."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional


@dataclass
class RenderJob:
    job_id: str
    session_id: str
    version_id: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RenderJobManager:
    """Run matplotlib/cfizz work outside request threads with bounded concurrency."""

    def __init__(self, render_function: Callable[[str, str, Dict[str, Any]], Any], max_workers: int = 1):
        self.render_function = render_function
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cfizz-render")
        self._jobs: Dict[str, RenderJob] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.RLock()

    def submit(self, session_id: str, version_id: str, spec: Dict[str, Any]) -> RenderJob:
        job = RenderJob(uuid.uuid4().hex, session_id, version_id)
        with self._lock:
            self._jobs[job.job_id] = job
            self._futures[job.job_id] = self.executor.submit(self._run, job.job_id, spec)
        return RenderJob(**job.to_dict())

    def get(self, job_id: str) -> RenderJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return RenderJob(**self._jobs[job_id].to_dict())

    def _run(self, job_id: str, spec: Dict[str, Any]) -> None:
        self._set(job_id, status="running", started_at=self._now())
        job = self.get(job_id)
        try:
            result = self.render_function(job.session_id, job.version_id, spec)
            if result.success:
                self._set(job_id, status="succeeded", artifacts=list(result.artifacts), finished_at=self._now())
            else:
                self._set(job_id, status="failed", artifacts=list(result.artifacts), error=result.error, finished_at=self._now())
        except Exception as exc:
            self._set(job_id, status="failed", error=f"渲染任务失败：{exc}", finished_at=self._now())

    def _set(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=True)
