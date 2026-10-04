"""Asynchronous, observable rendering jobs for the Agent workspace."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import inspect
import statistics
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional


_DEFAULT_RENDER_SECONDS = 30
_FIGURE_ESTIMATES = {
    "compartment_saddle": 120,
    "compartment_multi": 90,
    "compartment_diff": 75,
    "compartment_diff_scatter": 75,
    "tad_boundary_pileup": 90,
    "tad_diff_region": 90,
    "loop_apa": 90,
    "loop_diff_heatmap": 75,
    "hic_multi": 45,
    "hic_triangle_multi": 45,
}


@dataclass(frozen=True)
class RenderOutcome:
    """Minimal render result shared by in-process and subprocess workers."""

    success: bool
    artifacts: List[str]
    error: Optional[str] = None


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
    figure_type: str = ""
    stage: str = "queued"
    progress: int = 0
    estimated_total_seconds: Optional[int] = None
    estimate_source: str = "default"
    cancel_requested: bool = False
    queue_position: int = 0
    estimated_wait_seconds: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        now = datetime.now(timezone.utc)
        created = self._parse_time(self.created_at)
        started = self._parse_time(self.started_at)
        finished = self._parse_time(self.finished_at)
        elapsed_origin = started or created
        elapsed_end = finished or now
        elapsed = max(0, int((elapsed_end - elapsed_origin).total_seconds())) if elapsed_origin else 0
        payload["elapsed_seconds"] = elapsed
        payload["cancellable"] = self.status in {"queued", "running", "cancelling"}

        estimated_total = self.estimated_total_seconds
        if self.status == "queued":
            wait = max(0, int(self.estimated_wait_seconds or 0))
            payload["estimated_remaining_seconds"] = (
                wait + int(estimated_total) if estimated_total is not None else None
            )
        elif self.status in {"running", "cancelling"} and estimated_total is not None:
            remaining = int(estimated_total) - elapsed
            payload["estimated_remaining_seconds"] = remaining if remaining > 0 else None
            if self.stage in {"loading_renderer", "reading_and_rendering", "running_analysis", "aggregating_contacts"}:
                time_progress = 20 + int(68 * min(1.0, elapsed / max(1, int(estimated_total))))
                payload["progress"] = max(int(self.progress), min(88, time_progress))
        else:
            payload["estimated_remaining_seconds"] = 0 if self.status == "succeeded" else None
        return payload

    @staticmethod
    def _parse_time(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


class RenderJobManager:
    """Run matplotlib/cfizz work outside request threads with bounded concurrency."""

    def __init__(self, render_function: Callable[[str, str, Dict[str, Any]], Any], max_workers: int = 1):
        self.render_function = render_function
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cfizz-render")
        self._jobs: Dict[str, RenderJob] = {}
        self._futures: Dict[str, Future] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._duration_history: Dict[str, List[float]] = {}
        self._lock = threading.RLock()

    def submit(self, session_id: str, version_id: str, spec: Dict[str, Any]) -> RenderJob:
        figure_type = str(spec.get("figure_type") or "")
        estimate, estimate_source = self._estimate_duration(figure_type)
        job = RenderJob(
            uuid.uuid4().hex,
            session_id,
            version_id,
            figure_type=figure_type,
            estimated_total_seconds=estimate,
            estimate_source=estimate_source,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._cancel_events[job.job_id] = threading.Event()
            self._futures[job.job_id] = self.executor.submit(self._run, job.job_id, spec)
        return self.get(job.job_id)

    def get(self, job_id: str) -> RenderJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = RenderJob(**asdict(self._jobs[job_id]))
            if job.status == "queued":
                queued = [
                    candidate for candidate in self._jobs.values()
                    if candidate.status == "queued"
                ]
                queued.sort(key=lambda candidate: candidate.created_at)
                ahead = queued[:queued.index(self._jobs[job_id])]
                running = [
                    candidate for candidate in self._jobs.values()
                    if candidate.status in {"running", "cancelling"}
                ]
                wait = sum(int(candidate.estimated_total_seconds or _DEFAULT_RENDER_SECONDS) for candidate in ahead)
                for candidate in running:
                    current = candidate.to_dict().get("estimated_remaining_seconds")
                    wait += int(current if current is not None else _DEFAULT_RENDER_SECONDS)
                job.queue_position = len(ahead) + 1
                job.estimated_wait_seconds = wait
            return job

    def latest_for_session(self, session_id: str, version_id: str) -> Optional[RenderJob]:
        """Return the latest attempt for one exact figure revision."""
        with self._lock:
            matching = [
                job for job in self._jobs.values()
                if job.session_id == session_id and job.version_id == version_id
            ]
            if not matching:
                return None
            return self.get(matching[-1].job_id)

    def cancel(self, job_id: str) -> RenderJob:
        """Cancel a queued job or request termination of a running render."""
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job.status not in {"queued", "running", "cancelling"}:
                return self.get(job_id)
            job.cancel_requested = True
            event = self._cancel_events[job_id]
            event.set()
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                job.status = "cancelled"
                job.stage = "cancelled"
                job.finished_at = self._now()
            else:
                job.status = "cancelling"
                job.stage = "cancelling"
            return self.get(job_id)

    def _run(self, job_id: str, spec: Dict[str, Any]) -> None:
        cancel_event = self._cancel_events[job_id]
        if cancel_event.is_set():
            self._set(job_id, status="cancelled", stage="cancelled", finished_at=self._now())
            return
        self._set(
            job_id,
            status="running",
            stage="starting_worker",
            progress=2,
            started_at=self._now(),
        )
        job = self.get(job_id)

        def report_progress(stage: str, progress: int) -> None:
            if not cancel_event.is_set():
                self._set(
                    job_id,
                    stage=str(stage),
                    progress=max(0, min(99, int(progress))),
                )

        try:
            result = self._invoke_render(
                job.session_id,
                job.version_id,
                spec,
                report_progress,
                cancel_event,
            )
            if cancel_event.is_set():
                self._set(
                    job_id,
                    status="cancelled",
                    stage="cancelled",
                    artifacts=[],
                    error=None,
                    progress=0,
                    finished_at=self._now(),
                )
                return
            if result.success:
                self._set(
                    job_id,
                    status="succeeded",
                    stage="completed",
                    progress=100,
                    artifacts=list(result.artifacts),
                    finished_at=self._now(),
                )
                self._record_duration(job_id)
            else:
                self._set(
                    job_id,
                    status="failed",
                    stage="failed",
                    artifacts=list(result.artifacts),
                    error=result.error,
                    finished_at=self._now(),
                )
        except Exception as exc:
            if cancel_event.is_set():
                self._set(job_id, status="cancelled", stage="cancelled", error=None, finished_at=self._now())
            else:
                self._set(job_id, status="failed", stage="failed", error=f"渲染任务失败：{exc}", finished_at=self._now())

    def _invoke_render(
        self,
        session_id: str,
        version_id: str,
        spec: Dict[str, Any],
        progress_callback: Callable[[str, int], None],
        cancel_event: threading.Event,
    ) -> Any:
        """Call new cancellable renderers while retaining the small public API."""
        try:
            parameters = list(inspect.signature(self.render_function).parameters.values())
            accepts_keywords = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
            )
            parameter_names = {parameter.name for parameter in parameters}
        except (TypeError, ValueError):
            accepts_keywords = False
            parameter_names = set()
        optional: Dict[str, Any] = {}
        if accepts_keywords or "progress_callback" in parameter_names:
            optional["progress_callback"] = progress_callback
        if accepts_keywords or "cancel_event" in parameter_names:
            optional["cancel_event"] = cancel_event
        if optional:
            return self.render_function(
                session_id,
                version_id,
                spec,
                **optional,
            )
        return self.render_function(session_id, version_id, spec)

    def _estimate_duration(self, figure_type: str) -> tuple[int, str]:
        with self._lock:
            samples = self._duration_history.get(figure_type, [])
            if samples:
                return max(5, int(round(statistics.median(samples)))), "history"
        return int(_FIGURE_ESTIMATES.get(figure_type, _DEFAULT_RENDER_SECONDS)), "default"

    def _record_duration(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            started = RenderJob._parse_time(job.started_at)
            finished = RenderJob._parse_time(job.finished_at)
            if not started or not finished:
                return
            duration = max(1.0, (finished - started).total_seconds())
            samples = self._duration_history.setdefault(job.figure_type, [])
            samples.append(duration)
            del samples[:-8]

    def _set(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            for job_id, event in self._cancel_events.items():
                if self._jobs[job_id].status in {"queued", "running", "cancelling"}:
                    event.set()
        self.executor.shutdown(wait=wait, cancel_futures=True)
