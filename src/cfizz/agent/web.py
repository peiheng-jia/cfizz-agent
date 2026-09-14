"""FastAPI application for the local left-chat/right-figure workspace."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import json
import inspect
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
import uuid
from typing import Any, Dict, Literal, Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, SecretStr

from cfizz import __version__

from .adapter import CfizzRenderAdapter
from .bundled import DEMO_DATA_ROOT, RESOURCE_ROOT, load_demo_spec
from .companions import (
    companion_resolution,
    discover_companion,
    suggest_compartment_viewport,
    suggest_feature_viewport,
    suggest_selected_viewport,
)
from .dataset import (
    DatasetScan,
    _capabilities,
    _parse_region_query,
    build_integrated_spec,
    build_workflow_spec,
    build_track_patch,
    prepare_workflow_selection,
    scan_dataset,
    select_dataset_files,
    _suggest_workflow_bindings,
    _source_type_supports_role,
)
from .figure_spec import FigureSpecValidator
from .figure_types import (
    DIRECT_FIGURE_TYPE_IDS,
    FIGURE_TYPE_BY_ID,
    READY_FIGURE_TYPE_IDS,
    figure_type_catalog,
    supports_integrated_tracks,
)
from .inspection import DataInspector
from .intent import IntentResult, SimpleIntentInterpreter
from .jobs import RenderJobManager
from .planner import build_planner_registry_from_env
from .parameters import (
    ParameterCatalog,
    ParameterEdit,
    visualization_parameter_catalog,
)
from .references import ReferenceRegistry, normalize_chromosome
from .session import FigureSession, FileFigureSessionStore


DATASET_UPLOAD_SUFFIXES = {
    ".cool", ".mcool", ".bw", ".bigwig", ".gtf", ".gff", ".gff3",
    ".bed", ".bedpe", ".tsv", ".txt", ".npy",
}
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024 * 1024


class CreateSessionBody(BaseModel):
    session_id: str = Field(min_length=1)
    spec: Dict[str, Any]


class DemoSessionBody(BaseModel):
    session_id: str = "foxj1_demo"


class BlankSessionBody(BaseModel):
    """Create the lightweight session used by a newly opened chat pane."""

    session_id: str = Field(min_length=1)


class FromHicBody(BaseModel):
    session_id: str = Field(min_length=1)
    hic_path: str = Field(min_length=1)
    title: Optional[str] = None
    chrom: Optional[str] = None
    start: Optional[int] = Field(default=None, ge=0)
    end: Optional[int] = Field(default=None, gt=0)
    region: Optional[str] = Field(default=None, max_length=160)
    reference_build: str = "hg38"
    figure_type: str = "hic_triangle"
    render: bool = True


class PatchBody(BaseModel):
    patch: Dict[str, Any]
    render: bool = True


class ParameterUpdateBody(ParameterEdit):
    """Typed, allow-listed update for one current visualization parameter."""

    render: bool = True
    confirm_scientific_change: bool = False


class WorkflowBody(BaseModel):
    figure_type: str = Field(min_length=1, max_length=120)
    source_ids: list[str] = Field(default_factory=list)
    dataset_path: Optional[str] = None
    source_paths: list[str] = Field(default_factory=list)
    selected_paths: list[str] = Field(default_factory=list)
    gene: Optional[str] = Field(default=None, max_length=160)
    reference_build: str = "hg38"
    resolution: Optional[int] = Field(default=None, gt=0)
    options: Dict[str, Any] = Field(default_factory=dict)
    file_overrides: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    workflow_bindings: list[Dict[str, Any]] = Field(default_factory=list)
    pairings_confirmed: bool = False
    render: bool = True


class ChatBody(BaseModel):
    message: str = Field(min_length=1)
    provider: Optional[str] = None
    confirm_scientific_change: bool = False
    render: bool = True


class PlannerConfigBody(BaseModel):
    provider: Literal["openai", "deepseek"]
    api_key: SecretStr
    model: Optional[str] = Field(default=None, max_length=120)


class DatasetScanBody(BaseModel):
    path: str = Field(min_length=1)
    source_paths: list[str] = Field(default_factory=list)
    gene: Optional[str] = Field(default=None, max_length=120)
    reference_build: str = "hg38"
    selected_paths: Optional[list[str]] = None
    file_overrides: Dict[str, Dict[str, str]] = Field(default_factory=dict)


class DatasetAuthorizeBody(BaseModel):
    path: str = Field(min_length=1)


class DatasetSessionBody(DatasetScanBody):
    session_id: str = Field(min_length=1)
    figure_type: str = "hic_triangle"
    window: int = Field(default=500_000, ge=1_000, le=100_000_000)
    resolution: Optional[int] = Field(default=None, gt=0)
    workflow_bindings: list[Dict[str, Any]] = Field(default_factory=list)
    pairings_confirmed: bool = False
    render: bool = True
    preview_only: bool = False


class WorkspaceService:
    """Application service joining sessions, intent parsing, and render jobs."""

    def __init__(self, project_root: Path, runtime_root: Path):
        self.project_root = project_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.session_root = self.runtime_root / "sessions"
        self.artifact_root = self.runtime_root / "artifacts"
        self.upload_root = Path(
            os.environ.get("CFIZZ_AGENT_UPLOAD_ROOT", self.runtime_root / "uploads")
        ).expanduser().resolve()
        self.upload_root.mkdir(parents=True, exist_ok=True)
        try:
            self.max_upload_file_bytes = int(
                os.environ.get("CFIZZ_AGENT_MAX_UPLOAD_FILE_BYTES", DEFAULT_MAX_UPLOAD_FILE_BYTES)
            )
        except ValueError:
            self.max_upload_file_bytes = DEFAULT_MAX_UPLOAD_FILE_BYTES
        self.max_upload_file_bytes = max(MAX_UPLOAD_CHUNK_BYTES, self.max_upload_file_bytes)
        self.store = FileFigureSessionStore(str(self.session_root))
        configured_roots = os.environ.get("CFIZZ_AGENT_DATA_ROOTS")
        # Region-sized reference tracks are generated inside the private runtime
        # directory.  Treat that directory as an internal trusted data root so
        # the normal FigureSpec path validator can render them.
        data_roots = [
            str(self.project_root),
            str(self.runtime_root),
            str(self.upload_root),
            str(DEMO_DATA_ROOT),
            str(RESOURCE_ROOT),
        ]
        if configured_roots:
            data_roots.extend(item for item in configured_roots.split(os.pathsep) if item)
        self.inspector = DataInspector(data_roots)
        self.references = ReferenceRegistry(self.project_root, self.runtime_root / "reference_cache")
        self.validator = FigureSpecValidator(self.inspector)
        self.planner_registry = build_planner_registry_from_env()
        self.sessions: Dict[str, FigureSession] = {}
        self.pending_actions: Dict[str, Dict[str, Any]] = {}
        self.dialogue_history: Dict[str, list[Dict[str, str]]] = {}
        self._lock = threading.RLock()
        self.jobs = RenderJobManager(self._render, max_workers=1)

    def create(self, session_id: str, spec: Dict[str, Any], replace: bool = False) -> FigureSession:
        with self._lock:
            if not replace and (session_id in self.sessions or (self.session_root / f"{session_id}.json").exists()):
                raise ValueError(f"会话 {session_id!r} 已存在。")
            session = FigureSession(session_id, spec, validator=self.validator)
            self.sessions[session_id] = session
            self.store.save(session)
            return session

    def get(self, session_id: str) -> FigureSession:
        with self._lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = self.store.load(session_id, validator=self.validator)
            session = self.sessions[session_id]
            self._restore_session_data_roots(session)
            return session

    def save(self, session: FigureSession) -> None:
        self.store.save(session)

    def submit_render(self, session: FigureSession):
        self._restore_session_data_roots(session)
        return self.jobs.submit(session.session_id, session.current.version_id, session.current_spec)

    def _restore_session_data_roots(self, session: FigureSession) -> None:
        """Re-authorize existing source directories from a persisted session.

        Browser-authorized directories are process-local.  A service restart
        must not make an otherwise valid saved figure impossible to render.
        Only parents of files that still exist are restored.
        """
        for source in session.current_spec.get("data_sources", []):
            raw_path = source.get("path") if isinstance(source, dict) else None
            if not raw_path:
                continue
            candidate = self.inspector.platform_path(str(raw_path)).expanduser()
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            try:
                resolved = candidate.resolve()
                if resolved.is_file():
                    self.inspector.authorize_root(str(resolved.parent))
            except (OSError, PermissionError, ValueError):
                # The validator will report a precise missing/unreadable file
                # error later; root restoration itself is best-effort.
                continue

    def _render(self, session_id: str, version_id: str, spec: Dict[str, Any]):
        output_dir = self.artifact_root / session_id / version_id
        render_spec = deepcopy(spec)
        render_spec.setdefault("export", {})["formats"] = ["svg", "png", "pdf"]
        return CfizzRenderAdapter(self.validator, str(output_dir)).render(render_spec)

    def artifact_url(self, path: str) -> str:
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ValueError("渲染产物不在服务目录中。") from exc
        return "/artifacts/" + relative.as_posix()

    def session_payload(self, session: FigureSession) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "version_id": session.current.version_id,
            "spec": session.current_spec,
            "history": session.history(),
            "can_undo": session.can_undo,
            "can_redo": session.can_redo,
        }

    def remember_dialogue(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        history = self.dialogue_history.setdefault(session_id, [])
        history.extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_reply},
        ])
        del history[:-12]


def create_app(project_root: Optional[str] = None, runtime_root: Optional[str] = None) -> FastAPI:
    # In a source checkout the repository root is three levels above this
    # module.  In a wheel that path is site-packages, which is not writable
    # and must not become the default data/runtime directory.  The CLI sets
    # these environment variables explicitly; the fallback also makes
    # ``python -m`` and programmatic use safe after installation.
    checkout_root = Path(__file__).resolve().parents[3]
    default_root = os.environ.get("CFIZZ_AGENT_PROJECT_ROOT")
    if not default_root:
        default_root = str(checkout_root if (checkout_root / "pyproject.toml").is_file() else Path.cwd())
    root = Path(project_root or default_root)
    runtime = Path(runtime_root or os.environ.get("CFIZZ_AGENT_RUNTIME", root / "agent_runtime"))
    static_root = Path(__file__).resolve().parent / "static"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "artifacts").mkdir(parents=True, exist_ok=True)

    service = WorkspaceService(root, runtime)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        service.jobs.shutdown(wait=False)

    app = FastAPI(title="CFIZZ Agent", version=__version__, lifespan=lifespan)
    app.state.workspace = service
    @app.get("/", include_in_schema=False)
    async def index():
        return HTMLResponse((static_root / "index.html").read_text(encoding="utf-8"))

    @app.get("/assets/{asset_name}", include_in_schema=False)
    async def asset(asset_name: str):
        allowed = {
            "app.css": "text/css; charset=utf-8",
            "app.js": "text/javascript; charset=utf-8",
            "cfizz-brand-mark.png": "image/png",
        }
        if asset_name not in allowed:
            raise HTTPException(404, "找不到静态资源。")
        return Response((static_root / asset_name).read_bytes(), media_type=allowed[asset_name])

    @app.get("/artifacts/{artifact_path:path}", include_in_schema=False)
    async def artifact(artifact_path: str):
        resolved = (service.artifact_root / artifact_path).resolve()
        try:
            resolved.relative_to(service.artifact_root)
        except ValueError as exc:
            raise HTTPException(404, "找不到渲染产物。") from exc
        if not resolved.is_file() or resolved.suffix.lower() not in {".png", ".svg", ".pdf"}:
            raise HTTPException(404, "找不到渲染产物。")
        if resolved.stat().st_size > 100 * 1024 * 1024:
            raise HTTPException(413, "该产物过大，请通过文件系统直接导出。")
        media_type = {".png": "image/png", ".svg": "image/svg+xml", ".pdf": "application/pdf"}[resolved.suffix.lower()]
        return Response(resolved.read_bytes(), media_type=media_type)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "cfizz-agent", "api_revision": 13}

    @app.get("/api/planner")
    async def planner_status():
        return service.planner_registry.default.status()

    @app.get("/api/planners")
    async def planner_catalog():
        return service.planner_registry.status()

    @app.post("/api/planners/configure")
    async def configure_planner(body: PlannerConfigBody):
        try:
            service.planner_registry.configure_runtime(
                provider=body.provider,
                api_key=body.api_key.get_secret_value(),
                model=body.model,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "message": f"{service.planner_registry.LABELS[body.provider]} 已在当前服务进程中启用。",
            **service.planner_registry.status(),
        }

    @app.delete("/api/planners/{provider}")
    async def remove_planner(provider: str):
        try:
            service.planner_registry.remove_runtime(provider)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"message": "API 配置已从内存中移除。", **service.planner_registry.status()}

    @app.get("/api/figure-types")
    async def figure_types():
        return {"figure_types": figure_type_catalog()}

    @app.get("/api/visualization-parameters")
    async def visualization_parameters(figure_type: Optional[str] = None):
        """Describe supported controls without requiring an active session."""
        try:
            catalog = visualization_parameter_catalog(figure_type)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"figure_types": catalog}

    @app.get("/api/references")
    async def references():
        return {"references": service.references.catalog()}

    @app.post("/api/datasets/scan")
    async def scan_data_directory(body: DatasetScanBody):
        try:
            scan = _scan_dataset_sources(
                body.path,
                body.source_paths,
                service.inspector,
                service.references,
                gene=body.gene,
                build=body.reference_build,
                file_overrides=body.file_overrides,
            )
        except (OSError, PermissionError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return scan.to_dict()

    @app.post("/api/datasets/authorize")
    async def authorize_data_directory(body: DatasetAuthorizeBody):
        try:
            authorized = service.inspector.authorize_root(body.path)
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"authorized_root": str(authorized), "message": "目录已授权给当前服务进程，可重新扫描。"}

    @app.post("/api/datasets/upload")
    async def upload_dataset_file(request: Request):
        """Save a browser-selected companion file into the scanned data folder.

        Uploads are intentionally scoped to an already authorized directory:
        the user must scan/load that directory first.  Files are streamed to a
        temporary sibling and atomically replaced, so a failed upload cannot
        leave a partial TSV/BED/BigWig that the workflow might discover.
        """
        # Browsers encode non-ASCII names before putting them in the custom
        # header; decode once, then strip any path components defensively.
        filename = Path(unquote(request.headers.get("x-filename", ""))).name.strip()
        target_raw = unquote(request.headers.get("x-target-path", "")).strip()
        if not filename or filename in {".", ".."}:
            raise HTTPException(422, "上传文件名不能为空。")
        if Path(filename).suffix.lower() not in DATASET_UPLOAD_SUFFIXES:
            raise HTTPException(422, "暂不支持该文件类型；请上传 cool、mcool、BigWig、GTF、BED、BEDPE、TSV、TXT 或 NPY。")
        if not target_raw:
            raise HTTPException(422, "请先扫描数据目录，再上传补充文件。")
        target = service.inspector.platform_path(target_raw).expanduser()
        if not target.is_absolute():
            target = service.project_root / target
        target = target.resolve()
        if target.exists() and target.is_file():
            parent = target.parent
        elif target.exists() and target.is_dir():
            parent = target
        else:
            raise HTTPException(422, "上传目标目录不存在；请先扫描一个已存在的数据目录。")
        try:
            parent = service.inspector.resolve_path(str(parent))
        except (OSError, PermissionError) as exc:
            raise HTTPException(403, str(exc)) from exc

        destination = (parent / filename).resolve()
        try:
            destination.relative_to(parent)
        except ValueError as exc:  # defensive, Path(filename).name already strips traversal
            raise HTTPException(422, "上传文件名无效。") from exc
        temporary = parent / f".{filename}.upload-{uuid.uuid4().hex}.tmp"
        maximum_bytes = 2 * 1024 * 1024 * 1024
        written = 0
        try:
            with temporary.open("wb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise HTTPException(413, "上传文件超过 2 GiB 限制。")
                    handle.write(chunk)
            os.replace(temporary, destination)
        except HTTPException:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(422, f"保存上传文件失败：{exc}") from exc
        return {"path": str(destination), "filename": filename, "size_bytes": written, "message": "补充文件已上传，可重新扫描目录。"}

    @app.post("/api/datasets/uploads/{session_id}/{upload_id}/files")
    async def upload_local_dataset_chunk(session_id: str, upload_id: str, request: Request):
        """Receive one resumable browser-upload chunk inside a private batch.

        The browser sends files sequentially in small raw-body requests.  A
        server-controlled session/batch root preserves folder structure while
        preventing absolute paths, traversal, unsupported file types, and
        writes outside the configured upload directory.
        """
        relative_raw = unquote(request.headers.get("x-relative-path", "")).strip()
        try:
            batch_root, destination = _local_upload_destination(
                service, session_id, upload_id, relative_raw
            )
            file_size = _upload_header_integer(request, "x-file-size", minimum=0)
            offset = _upload_header_integer(request, "x-chunk-offset", minimum=0)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if file_size > service.max_upload_file_bytes:
            limit_gib = service.max_upload_file_bytes / (1024 ** 3)
            raise HTTPException(413, f"单个上传文件不能超过 {limit_gib:g} GiB。")
        if offset > file_size:
            raise HTTPException(422, "上传分块偏移超过文件大小。")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                announced_chunk_size = int(content_length)
            except ValueError as exc:
                raise HTTPException(422, "上传分块大小无效。") from exc
            if announced_chunk_size > MAX_UPLOAD_CHUNK_BYTES:
                raise HTTPException(413, "单个上传分块不能超过 64 MiB。")
            if offset + announced_chunk_size > file_size:
                raise HTTPException(422, "上传分块超出声明的文件大小。")

        batch_root.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.parent.resolve().relative_to(batch_root.resolve())
        except ValueError as exc:
            raise HTTPException(422, "上传路径超出当前批次目录。") from exc

        partial = destination.with_name(f".{destination.name}.cfizz-upload-part")
        if destination.exists():
            if destination.is_file() and destination.stat().st_size == file_size:
                return {
                    "path": str(destination), "relative_path": relative_raw,
                    "received_bytes": file_size, "size_bytes": file_size,
                    "complete": True,
                }
            raise HTTPException(409, "同名文件已经存在且大小不同，请重新选择文件。")

        existing_size = partial.stat().st_size if partial.exists() else 0
        if existing_size < offset:
            raise HTTPException(409, f"上传分块不连续；服务器已有 {existing_size} 字节，请从该位置继续。")
        free_bytes = shutil.disk_usage(service.upload_root).free
        reserve_bytes = 256 * 1024 * 1024
        required_bytes = file_size - offset
        if free_bytes < required_bytes + reserve_bytes:
            raise HTTPException(507, "服务器磁盘空间不足，无法继续上传。")

        written = 0
        try:
            mode = "r+b" if partial.exists() else "w+b"
            with partial.open(mode) as handle:
                handle.seek(offset)
                handle.truncate(offset)
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > MAX_UPLOAD_CHUNK_BYTES:
                        raise HTTPException(413, "单个上传分块不能超过 64 MiB。")
                    if offset + written > file_size:
                        raise HTTPException(422, "上传数据超过声明的文件大小。")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except HTTPException:
            if partial.exists():
                with partial.open("r+b") as handle:
                    handle.truncate(offset)
            raise
        except OSError as exc:
            if partial.exists():
                with partial.open("r+b") as handle:
                    handle.truncate(offset)
            raise HTTPException(422, f"保存上传分块失败：{exc}") from exc

        received = offset + written
        complete = received == file_size
        if complete:
            os.replace(partial, destination)
        return {
            "path": str(destination), "relative_path": relative_raw,
            "received_bytes": received, "size_bytes": file_size,
            "complete": complete,
        }

    @app.post("/api/datasets/uploads/{session_id}/{upload_id}/complete")
    async def complete_local_dataset_upload(session_id: str, upload_id: str):
        try:
            batch_root = _local_upload_batch_root(service, session_id, upload_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not batch_root.exists() or not batch_root.is_dir():
            raise HTTPException(404, "找不到本次上传。")
        if any(path.is_file() for path in batch_root.rglob("*.cfizz-upload-part")):
            raise HTTPException(409, "仍有文件尚未上传完成。")
        files = [
            path for path in batch_root.rglob("*")
            if path.is_file() and path.suffix.lower() in DATASET_UPLOAD_SUFFIXES
        ]
        if not files:
            raise HTTPException(422, "没有收到可识别的数据文件。")
        try:
            service.inspector.authorize_root(str(batch_root))
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "path": str(batch_root),
            "file_count": len(files),
            "size_bytes": sum(path.stat().st_size for path in files),
            "message": "本机文件已上传，正在扫描数据类型。",
        }

    @app.post("/api/sessions/from-dataset")
    async def create_from_dataset(body: DatasetSessionBody):
        try:
            scan = _scan_dataset_sources(
                body.path,
                body.source_paths,
                service.inspector,
                service.references,
                gene=body.gene,
                build=body.reference_build,
                file_overrides=body.file_overrides,
            )
            selected_paths = body.selected_paths
            requires_hic = _workflow_requires_hic(body.figure_type)
            # API callers that omit a selection retain the historical
            # single-sample default: use the first discovered Hi-C file while
            # keeping companion tracks.  The web picker always sends explicit
            # paths, so multi-sample choices there remain user-controlled.
            item = FIGURE_TYPE_BY_ID.get(body.figure_type)
            rule = (item or {}).get("input_cardinality", {}).get("hic")
            if selected_paths is None:
                if requires_hic:
                    hic_files = [file for file in scan.files if file.usable and file.role == "hic"]
                    integrated_type = supports_integrated_tracks(body.figure_type)
                    hic_selection = hic_files[:1] if rule and rule.get("max") == 1 else hic_files
                    # An omitted selection is still a selection.  Only include
                    # roles declared by this figure's contract and source formats
                    # accepted by those roles.  The old fallback appended every
                    # non-Hi-C file, allowing a boundary/insulation TSV to leak
                    # into an ``intervals`` layer and fail in FigureSpec validation.
                    contract = (item or {}).get("input_contract") or {}
                    allowed_roles = {
                        str(role) for role in contract.get("allowed_roles")
                        or (contract.get("roles") or {}).keys()
                    }
                    companions = [
                        file for file in scan.files
                        if file.usable
                        and file.role in allowed_roles
                        and file.role != "hic"
                        and _source_type_supports_role(file.type, file.role)
                    ]
                    selected_paths = [file.path for file in hic_selection]
                    if integrated_type:
                        selected_paths.extend(file.path for file in companions)
                else:
                    # Analysis-only workflows (for example differential TAD,
                    # compartment, and loop result comparisons) do not need a
                    # cooler matrix.  Select only their declared roles and
                    # honour the contract's maximum so an omitted API selection
                    # cannot accidentally turn into a many-file request.
                    selected_paths = _default_workflow_selection_paths(scan, body.figure_type)
            selected_scan = select_dataset_files(scan, selected_paths)
            selected_scan, normalized_bindings = prepare_workflow_selection(
                selected_scan,
                body.figure_type,
                body.workflow_bindings,
                body.pairings_confirmed,
            )
            if not requires_hic:
                # ``scan_dataset`` reports a useful inventory warning when a
                # directory has no matrix.  Once the workflow contract has
                # validated a result-only selection, that warning is no
                # longer an execution error and must not leak into the
                # session's user-facing status.
                selected_scan = replace(
                    selected_scan,
                    missing=[
                        message for message in selected_scan.missing
                        if "cool" not in message.lower() and "mcool" not in message.lower()
                    ],
                )
                spec = build_workflow_spec(
                    selected_scan,
                    body.session_id,
                    body.figure_type,
                    service.references,
                    gene=body.gene,
                    build=body.reference_build,
                    window=body.window,
                    resolution=body.resolution,
                )
            else:
                spec = build_integrated_spec(
                    selected_scan,
                    body.session_id,
                    service.references,
                    gene=body.gene,
                    build=body.reference_build,
                    window=body.window,
                    resolution=body.resolution,
                )
            if normalized_bindings:
                spec.setdefault("metadata", {})["workflow_bindings"] = normalized_bindings
            if requires_hic and not body.gene:
                _auto_default_viewport(spec, body.figure_type, service.inspector)
            selected_track_count = sum(
                item.usable and item.role in {"signal", "intervals", "gene_annotation"}
                for item in selected_scan.files
            )
            # Keep the user's checked files in the FigureSpec for provenance,
            # but record when the selected official CFIZZ renderer cannot put
            # those tracks in the same figure.  The UI can explain this rather
            # than preventing the underlying Hi-C figure from being created.
            if selected_track_count and not supports_integrated_tracks(body.figure_type):
                metadata = spec.setdefault("metadata", {})
                metadata["unrendered_track_count"] = int(selected_track_count)
                metadata["track_renderer_note"] = (
                    f"{FIGURE_TYPE_BY_ID.get(body.figure_type, {}).get('label', body.figure_type)} 的 CFIZZ 官方接口不带整合轨道面板；"
                    "已按所选图类型生成 Hi-C 图，轨道数据保留在会话中，可切换到 Hi-C 多组学整合图后使用。"
                )
            if body.figure_type == "hic_triangle":
                session = (
                    FigureSession(body.session_id, spec, validator=service.validator)
                    if body.preview_only
                    else service.create(body.session_id, spec, replace=True)
                )
            else:
                # A workflow button may be used immediately after a directory
                # scan, before a triangle session exists.  Build the standard
                # integrated spec first, then resolve the requested catalogue
                # workflow against all sources discovered in that directory.
                if body.figure_type not in READY_FIGURE_TYPE_IDS:
                    raise ValueError("该 CFIZZ 工作流未登记或当前不可执行。")
                session = FigureSession(body.session_id, spec, validator=service.validator)
                intent = IntentResult(
                    "workflow",
                    f"准备执行 CFIZZ 工作流：{FIGURE_TYPE_BY_ID[body.figure_type]['label']}。",
                    {"workflow_request": {
                        "figure_type": body.figure_type,
                        "source_ids": [source.get("id") for source in spec.get("data_sources", [])],
                        "options": {},
                    }},
                    planner="ui:workflow",
                )
                resolved = _resolve_ai_workflow_intent(session, intent)
                if resolved.action != "patch" or not resolved.patch:
                    raise ValueError(resolved.reply)
                session.apply_patch(_augment_compartment_patch(resolved.patch, session.current_spec, service.inspector))
                if not body.preview_only:
                    with service._lock:
                        service.sessions[body.session_id] = session
                        service.save(session)
        except (OSError, PermissionError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.preview_only:
            return {
                "spec": session.current_spec,
                "dataset_scan": scan.to_dict(),
                "job": None,
                "preview_only": True,
            }
        job = service.submit_render(session).to_dict() if body.render else None
        return {**service.session_payload(session), "dataset_scan": scan.to_dict(), "job": job}

    @app.post("/api/sessions/{session_id}/tracks/from-dataset")
    async def add_tracks_from_dataset(session_id: str, body: DatasetScanBody):
        session = _session_or_404(service, session_id)
        current_figure_type = session.current_spec.get("figure_type")
        if not supports_integrated_tracks(current_figure_type):
            item = FIGURE_TYPE_BY_ID.get(str(current_figure_type)) or {}
            raise HTTPException(
                422,
                f"{item.get('label', current_figure_type)} 使用的 CFIZZ 官方接口没有整合轨道面板；"
                "这不是三角形限制。请切换到“Hi-C 多组学整合图”（或支持整合轨道的 TAD 区域图）后再添加轨道。",
            )
        try:
            for source_path in _dataset_source_paths(body.path, body.source_paths):
                resolved = service.inspector.platform_path(source_path).expanduser()
                if resolved.exists() and resolved.is_dir():
                    service.inspector.authorize_root(str(resolved))
            scan = _scan_dataset_sources(
                body.path,
                body.source_paths,
                service.inspector,
                service.references,
                gene=body.gene,
                build=body.reference_build,
                file_overrides=body.file_overrides,
            )
            selected_scan = select_dataset_files(scan, body.selected_paths)
            patch = build_track_patch(selected_scan, session.current_spec, service.inspector, {"signal", "intervals"})
            added = patch.pop("added")
            skipped = patch.pop("skipped_incompatible", [])
            already_present = bool(patch.pop("already_present", False))
            summary = patch.pop("summary", "")
            if patch.get("operations"):
                with service._lock:
                    revision = session.apply_patch(patch)
                    service.save(session)
            else:
                # A repeated selection is an informational no-op.  Do not
                # append an empty revision or turn it into a 422 error.
                revision = session.current
        except (OSError, PermissionError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session).to_dict() if patch.get("operations") else None
        return {
            **service.session_payload(session), "revision": revision.to_dict(), "dataset_scan": scan.to_dict(),
            "added": added, "skipped_incompatible": skipped, "already_present": already_present,
            "summary": summary, "job": job,
        }

    @app.post("/api/sessions")
    async def create_session(body: CreateSessionBody):
        try:
            session = service.create(body.session_id, body.spec)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return service.session_payload(session)

    @app.post("/api/sessions/demo")
    async def create_demo(body: DemoSessionBody):
        try:
            spec = load_demo_spec()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HTTPException(500, f"内置 FOXJ1 示例不完整：{exc}") from exc
        session = service.create(body.session_id, spec, replace=True)
        job = service.submit_render(session)
        return {**service.session_payload(session), "job": job.to_dict()}

    @app.post("/api/sessions/blank")
    async def create_blank(body: BlankSessionBody):
        """Open chat without inventing a figure or triggering a render.

        The browser can therefore accept a first message such as
        ``/data/case1 画多样本 Hi-C``.  The chat workflow then replaces this
        draft with a validated, data-backed FigureSpec after confirmation.
        """
        try:
            session = service.create(body.session_id, _blank_chat_spec(body.session_id), replace=True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return service.session_payload(session)

    @app.post("/api/sessions/from-hic")
    async def create_from_hic(body: FromHicBody):
        try:
            reference = service.references._build(body.reference_build).to_dict()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.figure_type not in DIRECT_FIGURE_TYPE_IDS:
            raise HTTPException(422, "该图类型需要额外输入，当前不能仅凭一个 cool/mcool 文件生成。")
        try:
            _validate_hic_count(body.figure_type, 1)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        inspection = service.inspector.inspect(body.hic_path)
        if not inspection.usable:
            raise HTTPException(422, inspection.error or "Hi-C 文件不可用。")
        if inspection.type not in {"cool", "mcool"}:
            raise HTTPException(422, "请选择 .cool 或 .mcool 文件。")
        metadata = inspection.metadata
        chromsizes = metadata.get("chromsizes", {})
        chrom = body.chrom or next(iter(chromsizes), None)
        if chrom is None:
            raise HTTPException(422, "无法读取染色体信息，请确认已安装 cooler。")
        if chrom not in chromsizes:
            raise HTTPException(422, f"文件中没有染色体 {chrom}。")
        query_region = _parse_region_query(body.region)
        if body.region and query_region is None:
            location = service.references.locate_gene(body.region, body.reference_build)
            if location is None:
                raise HTTPException(422, f"无法识别“{body.region}”。请输入基因名，或范围如 chr1:1-2Mb。")
            query_region = (location.chrom, max(0, location.start - 500_000), location.end + 500_000)
        if query_region:
            requested_chrom, start, end = query_region
            chrom = normalize_chromosome(requested_chrom, chromsizes) or requested_chrom
            if chrom not in chromsizes:
                raise HTTPException(422, f"文件中没有染色体 {requested_chrom}。")
            end = min(chromsizes[chrom], end)
        else:
            start = 0 if body.start is None else body.start
            end = min(chromsizes[chrom], start + 2_000_000) if body.end is None else body.end
        resolutions = metadata.get("resolutions", [])
        if not resolutions:
            raise HTTPException(422, "无法读取 Hi-C 分辨率。")
        resolution = service.validator.choose_resolution(end - start, resolutions)
        companion = None
        companion_kind = None
        if body.figure_type in {"tad_insulation", "tad_insulation_track", "tad_boundary_square", "compartment", "loop_heatmap", "loop_apa"}:
            companion_kind = (
                "insulation" if body.figure_type in {"tad_insulation", "tad_insulation_track"}
                else "boundaries" if body.figure_type == "tad_boundary_square"
                else "compartment" if body.figure_type == "compartment"
                else "loops"
            )
            companion = discover_companion(inspection.path, companion_kind)
            if companion is None:
                requirement = (
                    "CFIZZ insulation TSV" if companion_kind == "insulation"
                    else "CFIZZ boundaries TSV" if companion_kind == "boundaries"
                    else "预计算 E1 TSV" if companion_kind == "compartment"
                    else "Loop BEDPE/TSV"
                )
                raise HTTPException(422, f"没有找到与该矩阵匹配的{requirement}。请将配套结果放在样本附近的 output 目录。")
            if companion.resolution is not None:
                if companion.resolution not in resolutions:
                    raise HTTPException(422, f"配套结果使用 {companion.resolution} bp，但矩阵没有该分辨率。")
                resolution = companion.resolution
        spec = _single_hic_spec(
            body.session_id, body.title, inspection.path, inspection.type,
            chrom, start, end, resolution, body.figure_type, reference,
        )
        if companion is not None:
            spec["data_sources"].append({
                "id": (
                    "insulation_result" if companion_kind == "insulation"
                    else "tad_boundaries" if companion_kind == "boundaries"
                    else "compartment_result" if companion_kind == "compartment"
                    else "loop_calls"
                ),
                "type": (
                    "insulation_tsv" if companion_kind == "insulation"
                    else "tad_tsv" if companion_kind == "boundaries"
                    else "compartment_tsv" if companion_kind == "compartment"
                    else "loop_tsv"
                ),
                "path": str(companion.path),
                "label": (
                    "Insulation" if companion_kind == "insulation"
                    else "TAD boundaries" if companion_kind == "boundaries"
                    else "E1" if companion_kind == "compartment"
                    else "Loops"
                ),
                "resolution": companion.resolution,
            })
        if body.start is None and body.end is None and not body.region:
            _auto_default_viewport(spec, body.figure_type, service.inspector)
        try:
            session = service.create(body.session_id, spec)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        job = service.submit_render(session).to_dict() if body.render else None
        return {**service.session_payload(session), "inspection": inspection.to_dict(), "job": job}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return service.session_payload(_session_or_404(service, session_id))

    @app.get("/api/sessions/{session_id}/parameters")
    async def get_session_parameters(session_id: str):
        session = _session_or_404(service, session_id)
        return {
            "session_id": session.session_id,
            "version_id": session.current.version_id,
            "figure_type": session.current_spec.get("figure_type"),
            "parameters": ParameterCatalog(session.current_spec).model_catalog(),
        }

    @app.post("/api/sessions/{session_id}/parameters")
    async def update_session_parameter(session_id: str, body: ParameterUpdateBody):
        """Safely update one catalogued parameter and optionally re-render."""
        session = _session_or_404(service, session_id)
        try:
            operation, scientific = ParameterCatalog(session.current_spec).compile(
                ParameterEdit(
                    target_kind=body.target_kind,
                    target_id=body.target_id,
                    parameter=body.parameter,
                    operation=body.operation,
                    value=body.value,
                )
            )
            if scientific and not body.confirm_scientific_change:
                raise HTTPException(
                    409,
                    detail={
                        "message": "该参数会改变科学计算结果，请确认后再提交。",
                        "requires_confirmation": True,
                        "parameter": body.parameter,
                    },
                )
            patch = {
                "summary": f"更新可视化参数 {body.parameter}",
                "operations": [operation],
            }
            with service._lock:
                revision = session.apply_patch(patch)
                service.save(session)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session).to_dict() if body.render else None
        return {
            **service.session_payload(session),
            "revision": revision.to_dict(),
            "parameters": ParameterCatalog(session.current_spec).model_catalog(),
            "job": job,
        }

    @app.post("/api/sessions/{session_id}/patch")
    async def apply_patch(session_id: str, body: PatchBody):
        session = _session_or_404(service, session_id)
        patch = _augment_compartment_patch(body.patch, session.current_spec, service.inspector)
        try:
            with service._lock:
                revision = session.apply_patch(patch)
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session).to_dict() if body.render else None
        return {**service.session_payload(session), "revision": revision.to_dict(), "job": job}

    @app.post("/api/sessions/{session_id}/workflow")
    async def run_workflow(session_id: str, body: WorkflowBody):
        """Run an explicitly selected catalogue workflow without exposing an
        implementation prompt in the conversation UI."""
        session = _session_or_404(service, session_id)
        item = FIGURE_TYPE_BY_ID.get(body.figure_type)
        if item is None or not item.get("ready"):
            raise HTTPException(422, "该 CFIZZ 工作流未登记或当前不可执行。")
        requires_hic = _workflow_requires_hic(body.figure_type)
        # A workflow owns its input selection.  Rebuild a temporary standard
        # spec from exactly those files, then commit the resolved workflow as
        # one undoable revision.  This avoids silently falling back to sources
        # that merely belonged to the previously displayed figure.
        if (body.dataset_path or body.source_paths) and body.selected_paths:
            try:
                primary_path = body.dataset_path or body.source_paths[0]
                scan = _scan_dataset_sources(
                    primary_path,
                    body.source_paths,
                    service.inspector,
                    service.references,
                    gene=body.gene,
                    build=body.reference_build,
                    file_overrides=body.file_overrides,
                )
                selected_scan = select_dataset_files(scan, body.selected_paths)
                selected_scan, normalized_bindings = prepare_workflow_selection(
                    selected_scan,
                    body.figure_type,
                    body.workflow_bindings,
                    body.pairings_confirmed,
                )
                if not requires_hic:
                    selected_scan = replace(
                        selected_scan,
                        missing=[
                            message for message in selected_scan.missing
                            if "cool" not in message.lower() and "mcool" not in message.lower()
                        ],
                    )
                    base_spec = build_workflow_spec(
                        selected_scan,
                        session_id,
                        body.figure_type,
                        service.references,
                        gene=body.gene,
                        build=body.reference_build,
                        resolution=body.resolution,
                    )
                else:
                    base_spec = build_integrated_spec(
                        selected_scan,
                        session_id,
                        service.references,
                        gene=body.gene,
                        build=body.reference_build,
                        resolution=body.resolution,
                    )
                if normalized_bindings:
                    base_spec.setdefault("metadata", {})["workflow_bindings"] = normalized_bindings
                # Derive the initial region from the exact files selected for
                # this workflow.  Previously the temporary workflow spec
                # retained build_integrated_spec's neutral 0--2 Mb range and
                # only discovered/adjusted a companion after rendering.
                if requires_hic and not body.gene:
                    _auto_default_viewport(base_spec, body.figure_type, service.inspector)
            except (OSError, PermissionError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            working_session = FigureSession(f"{session_id}_workflow", base_spec, validator=service.validator)
            workflow_source_ids = [
                str(source.get("id"))
                for source in base_spec.get("data_sources", [])
                if source.get("id")
            ]
        else:
            working_session = session
            workflow_source_ids = body.source_ids
        intent = IntentResult(
            "workflow",
            f"准备执行 CFIZZ 工作流：{item['label']}。",
            {"workflow_request": {"figure_type": body.figure_type, "source_ids": workflow_source_ids, "options": body.options}},
            planner="ui:workflow",
        )
        resolved = _resolve_ai_workflow_intent(working_session, intent)
        if resolved.action != "patch" or not resolved.patch:
            raise HTTPException(422, resolved.reply)
        try:
            with service._lock:
                patch = _augment_compartment_patch(resolved.patch, working_session.current_spec, service.inspector)
                if working_session is session:
                    revision = session.apply_patch(patch)
                else:
                    working_session.apply_patch(patch)
                    revision = session.replace_spec(
                        working_session.current_spec,
                        summary=f"使用所选文件生成 {item['label']}",
                    )
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session).to_dict() if body.render else None
        return {**service.session_payload(session), "revision": revision.to_dict(), "intent": resolved.to_dict(), "job": job}

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatBody):
        session = _session_or_404(service, session_id)
        history = service.dialogue_history.get(session_id, [])
        confirmation = _confirmation_text(body.message)
        cancellation = _cancellation_text(body.message)
        pending = service.pending_actions.pop(session_id, None) if (confirmation or cancellation) else None
        intent = None
        # A chat workflow is a two-step operation: first prepare and explain
        # the exact CFIZZ inputs, then mutate/render only after the user says
        # “确认”.  This keeps conversational drawing consistent with the
        # right-hand workflow picker and avoids a hidden “guess all files” run.
        if pending is not None and pending.get("kind") == "workflow":
            if cancellation:
                intent = IntentResult("answer", "已取消这次 CFIZZ 工作流，没有修改当前图。", planner="chat:workflow")
            elif confirmation:
                intent = IntentResult(
                    "workflow_apply",
                    pending["apply_reply"],
                    {"workflow_pending": pending},
                    planner="chat:workflow",
                )
        elif not confirmation and not cancellation:
            request = _chat_workflow_request(body.message, history, session)
            if request is not None:
                intent = _chat_workflow_confirmation(service, session, request)

        if intent is None:
            intent = _hic_comparison_intent(service, session, body.message, history=history)
        if intent is None:
            intent = _directory_track_intent(service, session, body.message, history=history)
        if intent is None and pending is not None:
            intent = IntentResult(
                "patch",
                pending["apply_reply"],
                pending["patch"],
                planner="reference:gene",
            )

        planner = None
        planner_error = None
        if intent is None:
            try:
                planner = service.planner_registry.get(body.provider)
            except ValueError as exc:
                planner_error = exc

            # With a configured AI provider, the model sees the complete
            # sentence first and may emit a typed reference request.  The
            # local handlers remain the offline/unavailable-provider fallback.
            ai_first = planner is not None and planner.status().get("mode") == "ai-assisted"
            if not ai_first:
                intent = _region_gene_annotation_intent(service, session, body.message)
                if intent is None:
                    intent = _annotation_question_intent(service, session, body.message)
                if intent is None:
                    intent = _gene_annotation_intent(service, session, body.message)
                if intent is None:
                    intent = _reference_gene_intent(service, session, body.message)

        if intent is None:
            if planner_error is not None:
                raise HTTPException(422, str(planner_error)) from planner_error
            if body.message.strip():
                service.pending_actions.pop(session_id, None)
            async_interpret = getattr(planner, "interpret_async", None)
            history = service.dialogue_history.get(session_id, [])
            if async_interpret is not None:
                kwargs = {"history": history} if _method_accepts_history(async_interpret) else {}
                intent = await async_interpret(body.message, session.current_spec, **kwargs)
            else:
                kwargs = {"history": history} if _method_accepts_history(planner.interpret) else {}
                intent = planner.interpret(body.message, session.current_spec, **kwargs)

        # The model may unnecessarily turn an explicit, fully specified gene
        # drawing command into a multiple-choice question.  Keep AI-first
        # semantic handling, but do not expose that uncertainty to the user
        # when the local hg38 reference can validate and execute the request.
        # A workflow preview is deliberately a clarification: it contains the
        # exact files, pairing and CFIZZ entrypoint that will be used after the
        # user confirms.  Do not let the later gene/reference convenience
        # fallbacks reinterpret words such as “Hi-C” in that preview request
        # and turn it into an immediate annotation patch.
        if intent.action == "clarify" and intent.planner != "chat:workflow":
            local_gene_intent = _region_gene_annotation_intent(service, session, body.message)
            if local_gene_intent is None:
                local_gene_intent = _gene_annotation_intent(service, session, body.message)
            if local_gene_intent is not None and local_gene_intent.action != "clarify":
                intent = IntentResult(
                    local_gene_intent.action,
                    local_gene_intent.reply,
                    local_gene_intent.patch,
                    requires_confirmation=local_gene_intent.requires_confirmation,
                    planner=f"{intent.planner}→{local_gene_intent.planner}:explicit-fallback",
                )

        # A misconfigured OpenAI-compatible endpoint can return a literal
        # ``Not Found`` body as if it were a successful assistant answer.  It
        # is not useful to the user and used to appear as a sequence of blank
        # error messages in the chat.  Convert it into an actionable local
        # explanation instead of treating it as a valid answer.
        if intent.action in {"answer", "clarify"} and _is_unhelpful_ai_reply(intent.reply):
            intent = IntentResult(
                "clarify",
                "AI 服务没有返回可用的理解结果（接口返回 Not Found）。请检查 API 地址和模型名称后重试；也可以先切换到本地规则。",
                planner=f"{intent.planner}:invalid-response",
            )

        # The external planner receives redacted paths for privacy, so it
        # cannot itself bind a local file.  When it nevertheless identifies a
        # registered CFIZZ workflow, reuse the local path extractor and send
        # the request through the same preview/confirmation state machine as
        # deterministic aliases.  This lets API users say “画某某图” in
        # natural language without bypassing contract validation.
        if intent.action == "workflow":
            ai_workflow_type = str((intent.patch or {}).get("workflow_request", {}).get("figure_type") or "")
            explicit_paths, dataset_path = _chat_extract_paths(body.message)
            if ai_workflow_type and (explicit_paths or dataset_path):
                chat_request = _chat_workflow_request(
                    body.message,
                    history,
                    session,
                    figure_type_override=ai_workflow_type,
                    allow_without_draw_words=True,
                )
                if chat_request is not None:
                    intent = _chat_workflow_confirmation(service, session, chat_request)

        if intent.action == "reference":
            intent = _resolve_ai_reference_intent(service, session, intent)
        if intent.action == "workflow":
            intent = _resolve_ai_workflow_intent(session, intent)
        # A text-only model can inspect the FigureSpec but cannot verify the
        # pixels produced by a renderer.  In particular it used to see a
        # stored genes layer and claim that it was visible even when the
        # selected official CFIZZ matrix API has no track panel.  Renderer
        # capability is authoritative and overrides that unsupported claim.
        compact_message = re.sub(r"\s+", "", body.message).lower()
        asks_gene_track = (
            any(token in compact_message for token in ("基因轨道", "基因注释", "标注基因", "标上", "geneannotation", "genetrack"))
            and any(token in compact_message for token in ("画", "显示", "标", "添加", "加上", "可以", "能"))
        )
        if asks_gene_track and not supports_integrated_tracks(session.current_spec.get("figure_type")):
            capability = _gene_annotation_intent(service, session, body.message)
            if capability is not None and capability.planner == "reference:renderer-capability":
                intent = capability
        # Validate conversational resolution changes before asking for
        # confirmation. This reports the shared grids of all selected Hi-C
        # matrices instead of allowing a later renderer failure.
        if intent.action == "patch":
            resolution_issue = _check_resolution_patch(service, session, intent.patch)
            if resolution_issue is not None:
                intent = IntentResult("clarify", resolution_issue, planner=f"{intent.planner}:resolution-check")
        if intent.action in {"clarify", "answer"}:
            service.remember_dialogue(session_id, body.message, intent.reply)
            return {"intent": intent.to_dict(), **service.session_payload(session), "job": None}
        if intent.requires_confirmation and not body.confirm_scientific_change:
            service.remember_dialogue(session_id, body.message, intent.reply)
            return {"intent": intent.to_dict(), **service.session_payload(session), "job": None}
        try:
            with service._lock:
                if intent.action == "undo":
                    session.undo()
                elif intent.action == "redo":
                    session.redo()
                elif intent.action == "workflow_apply":
                    pending_workflow = (intent.patch or {}).get("workflow_pending")
                    if not isinstance(pending_workflow, dict) or not isinstance(pending_workflow.get("spec"), dict):
                        raise ValueError("聊天工作流确认已过期，请重新描述数据路径和图类型。")
                    revision = session.replace_spec(
                        deepcopy(pending_workflow["spec"]),
                        summary=str(pending_workflow.get("summary") or "使用聊天确认的数据生成 CFIZZ 图"),
                    )
                elif intent.action == "patch" and intent.patch:
                    session.apply_patch(_augment_compartment_patch(intent.patch, session.current_spec, service.inspector))
                elif intent.action != "render":
                    raise ValueError(f"不支持动作 {intent.action!r}。")
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # A draft session is a chat workspace, not a renderable figure.  Do
        # not enqueue a job until a real workflow or Hi-C source has replaced
        # the draft spec.
        is_draft = bool((session.current_spec.get("metadata") or {}).get("draft"))
        job = service.submit_render(session).to_dict() if body.render and not is_draft else None
        service.remember_dialogue(session_id, body.message, intent.reply)
        return {"intent": intent.to_dict(), **service.session_payload(session), "job": job}

    @app.post("/api/sessions/{session_id}/undo")
    async def undo(session_id: str):
        session = _session_or_404(service, session_id)
        try:
            with service._lock:
                session.undo()
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session)
        return {**service.session_payload(session), "job": job.to_dict()}

    @app.post("/api/sessions/{session_id}/redo")
    async def redo(session_id: str):
        session = _session_or_404(service, session_id)
        try:
            with service._lock:
                session.redo()
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session)
        return {**service.session_payload(session), "job": job.to_dict()}

    @app.post("/api/sessions/{session_id}/restore/{version_id}")
    async def restore(session_id: str, version_id: str):
        session = _session_or_404(service, session_id)
        try:
            with service._lock:
                session.restore(version_id)
                service.save(session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job = service.submit_render(session)
        return {**service.session_payload(session), "job": job.to_dict()}

    @app.post("/api/sessions/{session_id}/render")
    async def render(session_id: str):
        job = service.submit_render(_session_or_404(service, session_id))
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            job = service.jobs.get(job_id)
        except KeyError as exc:
            raise HTTPException(404, "找不到渲染任务。") from exc
        payload = job.to_dict()
        payload["artifact_urls"] = [service.artifact_url(path) for path in job.artifacts]
        return payload

    return app


def _dataset_source_paths(primary_path: str, source_paths: Optional[list[str]] = None) -> list[str]:
    """Return stable, de-duplicated roots for one multi-source selection."""
    roots: list[str] = []
    seen: set[str] = set()
    for raw_path in [primary_path, *(source_paths or [])]:
        value = str(raw_path or "").strip()
        if not value:
            continue
        key = value.replace("\\", "/").rstrip("/").casefold()
        if key in seen:
            continue
        seen.add(key)
        roots.append(value)
    if not roots:
        raise ValueError("请至少提供一个数据源路径。")
    return roots


def _scan_dataset_sources(
    primary_path: str,
    source_paths: Optional[list[str]],
    inspector: DataInspector,
    references: ReferenceRegistry,
    *,
    gene: Optional[str] = None,
    build: str = "hg38",
    file_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> DatasetScan:
    """Scan one or more authorized roots as a single explicit inventory.

    Files remain at their original absolute paths.  Merging only changes the
    selection inventory, so FigureSpec provenance and path validation still
    refer to the exact source file chosen by the user.
    """
    scans = [
        scan_dataset(
            path,
            inspector,
            references,
            gene=gene,
            build=build,
            file_overrides=file_overrides,
        )
        for path in _dataset_source_paths(primary_path, source_paths)
    ]
    if len(scans) == 1:
        return scans[0]

    files = []
    seen_files: set[str] = set()
    for scan in scans:
        for item in scan.files:
            key = str(item.path).replace("\\", "/").rstrip("/").casefold()
            if key in seen_files:
                continue
            seen_files.add(key)
            files.append(item)

    hic_scans = [
        scan for scan in scans
        if any(item.usable and item.role == "hic" for item in scan.files)
    ]
    chromosome_sets = [set(scan.chromosomes) for scan in hic_scans if scan.chromosomes]
    if chromosome_sets:
        shared_chromosomes = set.intersection(*chromosome_sets)
        chromosomes = [chrom for chrom in hic_scans[0].chromosomes if chrom in shared_chromosomes]
    else:
        chromosomes = []

    resolution_sets = [set(map(int, scan.resolutions)) for scan in hic_scans if scan.resolutions]
    resolutions = sorted(set.intersection(*resolution_sets)) if resolution_sets else []
    gene_location = next((scan.gene for scan in scans if scan.gene), None)
    reference_scan = next(
        (scan for scan in scans if scan.reference.get("user_annotation")),
        scans[0],
    )

    roles = {item.role for item in files if item.usable}
    missing = list(dict.fromkeys(message for scan in scans for message in scan.missing))
    if "hic" in roles:
        missing = [message for message in missing if "cool" not in message.lower() and "mcool" not in message.lower()]
    if gene_location:
        missing = [message for message in missing if "未在当前 GTF" not in message]

    warnings = list(dict.fromkeys(message for scan in scans for message in scan.warnings))
    if "signal" in roles:
        warnings = [message for message in warnings if "未发现 BigWig" not in message]
    if "gene_annotation" in roles:
        warnings = [
            message for message in warnings
            if "未发现 GTF" not in message and "均未发现基因注释" not in message
        ]

    return DatasetScan(
        root=" | ".join(scan.root for scan in scans),
        files=files,
        reference=dict(reference_scan.reference),
        gene=gene_location,
        chromosomes=chromosomes,
        resolutions=resolutions,
        capabilities=_capabilities(files),
        missing=missing,
        warnings=warnings,
    )


def _session_or_404(service: WorkspaceService, session_id: str) -> FigureSession:
    try:
        return service.get(session_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _upload_header_integer(request: Request, name: str, minimum: int = 0) -> int:
    raw = request.headers.get(name)
    if raw is None:
        raise ValueError(f"缺少上传请求头 {name}。")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"上传请求头 {name} 必须是整数。") from exc
    if value < minimum:
        raise ValueError(f"上传请求头 {name} 不能小于 {minimum}。")
    return value


def _local_upload_batch_root(service: WorkspaceService, session_id: str, upload_id: str) -> Path:
    if len(session_id) > 120:
        raise ValueError("上传会话标识过长。")
    safe_session_id = FigureSession._validate_id(session_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}", upload_id):
        raise ValueError("上传批次标识无效。")
    root = (service.upload_root / safe_session_id / upload_id).resolve()
    try:
        root.relative_to(service.upload_root)
    except ValueError as exc:
        raise ValueError("上传批次目录无效。") from exc
    return root


def _local_upload_destination(
    service: WorkspaceService,
    session_id: str,
    upload_id: str,
    relative_raw: str,
) -> tuple[Path, Path]:
    batch_root = _local_upload_batch_root(service, session_id, upload_id)
    normalized = relative_raw.replace("\\", "/").strip("/")
    if not normalized or len(normalized) > 1024:
        raise ValueError("上传文件的相对路径为空或过长。")
    relative = PurePosixPath(normalized)
    parts = relative.parts
    if (
        relative.is_absolute()
        or not parts
        or len(parts) > 32
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part) > 255 or any(ord(char) < 32 for char in part) for part in parts)
    ):
        raise ValueError("上传文件路径无效。")
    if Path(parts[-1]).suffix.lower() not in DATASET_UPLOAD_SUFFIXES:
        raise ValueError("暂不支持该文件类型；请选择 cool、mcool、BigWig、GTF、GFF、BED、BEDPE、TSV、TXT 或 NPY 文件。")
    destination = batch_root.joinpath(*parts).resolve()
    try:
        destination.relative_to(batch_root)
    except ValueError as exc:
        raise ValueError("上传文件路径超出当前批次目录。") from exc
    return batch_root, destination


def _method_accepts_history(method: Any) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.name == "history" or parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)


def _is_unhelpful_ai_reply(reply: Any) -> bool:
    """Recognise transport-looking text accidentally returned as an answer."""
    compact = re.sub(r"\s+", " ", str(reply or "")).strip().lower()
    return compact in {"not found", "404", "404 not found", "error", "internal server error"}


def _check_resolution_patch(service: WorkspaceService, session: FigureSession, patch: Any) -> Optional[str]:
    """Validate an explicit conversational resolution without mutating state."""
    if not isinstance(patch, dict):
        return None
    requested = None
    for operation in patch.get("operations", []):
        if not isinstance(operation, dict):
            continue
        if (
            operation.get("op") == "update"
            and operation.get("target_kind") == "analysis"
            and operation.get("field") == "resolution"
        ):
            requested = operation.get("value")
            break
    if not isinstance(requested, int) or isinstance(requested, bool):
        return None
    candidate = session.current_spec
    candidate.setdefault("analysis", {})["resolution"] = requested
    result = service.validator.validate(candidate, inspect_files=True)
    relevant = [
        issue for issue in result.errors
        if issue.code in {"resolution_unavailable", "no_common_resolution", "resolution_unknown"}
    ]
    if not relevant:
        return None
    issue = relevant[0]
    return f"{issue.message}{(' ' + issue.hint) if issue.hint else ''}"


def _validate_dataset_cardinality(scan: DatasetScan, figure_type: str) -> None:
    """Enforce the catalogue's input count before building any FigureSpec."""
    count = sum(file.usable and file.role == "hic" for file in scan.files)
    _validate_hic_count(figure_type, count)


def _validate_hic_count(figure_type: str, count: int) -> None:
    item = FIGURE_TYPE_BY_ID.get(str(figure_type))
    rule = ((item or {}).get("input_cardinality") or {}).get("hic")
    if not rule:
        return
    minimum = int(rule.get("min") or 0)
    maximum = rule.get("max")
    maximum = int(maximum) if maximum is not None else None
    label = item["label"]
    if maximum is not None and minimum == maximum and count != minimum:
        raise ValueError(f"“{label}”需要且只能选择 {minimum} 个 Hi-C 文件；当前选择了 {count} 个。")
    if count < minimum:
        raise ValueError(f"“{label}”至少需要选择 {minimum} 个 Hi-C 文件；当前选择了 {count} 个。")
    if maximum is not None and count > maximum:
        raise ValueError(f"“{label}”最多只能选择 {maximum} 个 Hi-C 文件；当前选择了 {count} 个。")


def _viewport_feature_kind(figure_type: str) -> Optional[str]:
    """Map a registered CFIZZ figure to the feature that defines its range."""
    figure_id = str(figure_type or "").casefold()
    if figure_id.startswith("compartment"):
        return "compartment"
    if figure_id.startswith("tad"):
        return "insulation" if "insulation" in figure_id or figure_id == "tad_multi" else "boundaries"
    if figure_id.startswith("loop"):
        return "loops"
    return None


def _selected_feature_paths(spec: Dict[str, Any], kind: str, inspector: DataInspector) -> list[str]:
    """Return only feature files already selected in this FigureSpec.

    A directory can contain many similarly named results (and often both a
    complete insulation grid and a sparse boundary export).  Viewport
    selection must therefore never call ``discover_companion`` and silently
    substitute another file.  ``build_integrated_spec`` records roles; direct
    Hi-C sessions record canonical source types, so both forms are accepted.
    """
    role_by_kind = {
        "compartment": {"compartment"},
        "insulation": {"insulation"},
        "boundaries": {"boundaries"},
        "loops": {"loops"},
    }
    type_by_kind = {
        "compartment": {"compartment_tsv"},
        "insulation": {"insulation_tsv"},
        "boundaries": {"tad_tsv"},
        "loops": {"loop_tsv", "bedpe"},
    }
    roles = role_by_kind.get(kind, {kind})
    types = type_by_kind.get(kind, set())
    paths: list[str] = []
    for source in spec.get("data_sources", []):
        if not isinstance(source, dict) or not source.get("path"):
            continue
        if source.get("role") not in roles and source.get("type") not in types:
            continue
        try:
            path = inspector.resolve_path(str(source["path"]))
        except (OSError, PermissionError, ValueError):
            path = Path(str(source["path"])).expanduser()
        if str(path) not in paths:
            paths.append(str(path))
    return paths


def _workflow_requires_hic(figure_type: str) -> bool:
    """Return whether the catalogue contract requires a Hi-C matrix.

    This is deliberately derived from ``figure_types.INPUT_CONTRACTS`` rather
    than from the renderer name or a hand-maintained UI list.  Difference
    analyses such as ``tad_diff_stacked`` consume two result TSVs directly;
    treating every workflow as a Hi-C canvas was the source of the late
    ``没有可用 .cool/.mcool`` error.
    """
    item = FIGURE_TYPE_BY_ID.get(str(figure_type)) or {}
    rule = (item.get("input_contract") or {}).get("roles", {}).get("hic") or {}
    try:
        return int(rule.get("min") or 0) > 0
    except (TypeError, ValueError):
        return False


def _default_workflow_selection_paths(scan: DatasetScan, figure_type: str) -> list[str]:
    """Choose a bounded default set for a no-Hi-C workflow.

    The directory inventory may contain many resolutions/windows for the same
    analysis product.  Selecting every compatible file makes a two-input
    workflow fail its own ``max=2`` contract.  Use the machine-readable role
    limits to pick a deterministic subset; the browser still sends explicit
    paths when the user wants a different pair.
    """
    item = FIGURE_TYPE_BY_ID.get(str(figure_type)) or {}
    contract = item.get("input_contract") or {}
    roles = contract.get("roles") or {}
    allowed = {str(role) for role in contract.get("allowed_roles") or roles}
    selected: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if str(role) not in allowed:
            continue
        candidates = [
            file for file in scan.files
            if file.usable and file.role == str(role)
            and _source_type_supports_role(file.type, file.role)
        ]
        rule = roles.get(role) or {}
        maximum = rule.get("max")
        if maximum is not None:
            try:
                candidates = candidates[: max(0, int(maximum))]
            except (TypeError, ValueError):
                pass
        for file in candidates:
            key = str(file.path).replace("\\", "/").casefold()
            if key in seen:
                continue
            seen.add(key)
            selected.append(file.path)
    return selected


def _chat_workflow_type(message: str) -> Optional[str]:
    """Map a natural-language drawing request to a registered CFIZZ workflow.

    This is intentionally a small, deterministic vocabulary layer.  It does
    not draw anything and it never invents a renderer: every returned ID must
    exist in :mod:`figure_types`, after which the normal workflow resolver and
    the CFIZZ adapter perform validation.  An AI planner can still handle
    requests that are too ambiguous for these aliases.
    """
    text = str(message or "").casefold()
    explicit = SimpleIntentInterpreter._parse_explicit_workflow(text)
    if explicit:
        # The intent parser returns ``(figure_id, label)``.  Chat requests
        # carry only the registered ID; passing the tuple through would make
        # the subsequent catalogue lookup fail with a stringified tuple.
        return str(explicit[0])

    # Exact catalogue IDs/labels are useful for power users who copy a
    # workflow name from the data panel or documentation.
    for item in figure_type_catalog():
        figure_id = str(item.get("id") or "")
        label = str(item.get("label") or "").casefold()
        if figure_id and figure_id.casefold() in text:
            return figure_id
        if label and label in text:
            return figure_id

    multi = any(token in text for token in ("双样本", "多样本", "两样本", "两个样本", "对比", "比较", "comparison"))
    diff = any(token in text for token in ("差异", "differential", "gain", "lost", "shift"))

    if "compartment" in text or "区室" in text or "a/b" in text or "a／b" in text:
        if diff and any(token in text for token in ("散点", "scatter")):
            return "compartment_diff_scatter"
        if diff and any(token in text for token in ("区域", "热图", "region")):
            return "compartment_diff_region"
        if any(token in text for token in ("saddle", "鞍形", "鞍")):
            return "compartment_saddle"
        if any(token in text for token in ("e1", "特征向量", "eigenvector")):
            return "compartment_eigenvector"
        if multi:
            return "compartment_multi"
        return "compartment"

    if "tad" in text or "绝缘" in text or "边界" in text:
        if diff and any(token in text for token in ("分类", "堆叠", "stacked")):
            return "tad_diff_stacked"
        if diff and any(token in text for token in ("pileup", "聚合")):
            return "tad_diff_pileup"
        if diff and any(token in text for token in ("区域", "热图", "region")):
            return "tad_diff_region"
        if any(token in text for token in ("pileup", "聚合")):
            return "tad_boundary_pileup"
        if multi:
            return "tad_multi"
        if any(token in text for token in ("边界", "boundary", "方形", "square")) and "绝缘" not in text and "insulation" not in text:
            return "tad_boundary_square"
        return "tad_insulation"

    if "loop" in text or "环" in text:
        if diff and any(token in text for token in ("apa", "聚合")):
            return "loop_diff_apa"
        if diff and any(token in text for token in ("区域", "热图", "region")):
            return "loop_diff_region"
        if diff and any(token in text for token in ("分类", "堆叠", "stacked")):
            return "loop_diff_stacked"
        if any(token in text for token in ("apa", "聚合")):
            return "loop_apa_multi" if multi else "loop_apa"
        return "loop_multi" if multi else "loop_heatmap"

    # Keep the generic multi-omics alias after the more specific Hi-C,
    # compartment, TAD and Loop branches.  Otherwise a request such as
    # “多组学 TAD 对比” was silently routed to the generic tracks view.
    if any(token in text for token in ("多组学", "multi-omics", "multiomics", "整合图", "综合图", "轨道整合")):
        return "tracks_integrated"

    if any(token in text for token in ("bigwig", "bw信号", "信号轨道")):
        return "tracks_signal"
    if any(token in text for token in ("gtf", "gff", "基因轨道", "基因注释")) and "hic" not in text and "hi-c" not in text:
        return "tracks_genes"
    if any(token in text for token in ("bed区间", "增强子", "peak轨道", "区间轨道")) and "hic" not in text and "hi-c" not in text:
        return "tracks_intervals"
    if any(token in text for token in ("混合轨道", "多个轨道")):
        return "tracks_mixed"

    if "hic" in text or "hi-c" in text or "热图" in text or "foxj1" in text:
        if multi and any(token in text for token in ("三角", "triangle", "foxj1")):
            return "hic_triangle_multi"
        if multi and any(token in text for token in ("方形", "square")):
            return "hic_multi"
        if any(token in text for token in ("方形", "square")):
            return "hic_square"
        if any(token in text for token in ("o/e", "oe", "observed/expected")):
            return "hic_oe"
        return "hic_triangle"
    return None


def _chat_path_parent(raw_path: str) -> str:
    """Get a parent path while preserving Windows spelling on any host."""
    value = str(raw_path or "").strip().strip("'\"")
    if not value:
        return value
    if value.endswith(("/", "\\")):
        return value.rstrip("/\\")
    match = re.match(r"^(.*)[\\/]([^\\/]+)$", value)
    return match.group(1) if match else value


def _chat_extract_paths(message: str) -> tuple[list[str], Optional[str]]:
    """Extract explicit data files (or a directory) from a chat sentence.

    Chat-driven workflows may start from a companion file rather than a
    matrix: for example, a user can provide a ``boundaries.tsv`` or a
    ``signal.bw`` path.  Keep the full path as an explicit input and use its
    parent only as the scan root, so the normal contract validator still
    decides whether the file is valid for the requested CFIZZ workflow.
    """
    text = str(message or "")
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", text)
    # Pasted Windows/POSIX directories are often not quoted.  Capture a
    # path-looking token up to whitespace or Chinese/ASCII sentence
    # punctuation; quoted paths above still support spaces in directory
    # names without ambiguity.
    unquoted_paths = re.findall(
        r"(?i)(?:(?:[a-z]:[\\/])|(?:/)|(?:\.\.?[\\/]))[^\s，。；,;]+",
        text,
    )
    data_pattern = re.compile(
        r"(?i)(?:(?:[a-z]:[\\/])|(?:/)|(?:\.\.?[\\/])|(?:demo[\\/])|(?:data[\\/]))"
        r"[^\s，。；,;]+?\.(?:mcool|cool|tsv|bed|bedpe|bw|bigwig|gtf|gff|gff3|npy)(?:\b|$)"
    )
    supported_suffix = re.compile(r"(?i)\.(?:mcool|cool|tsv|bed|bedpe|bw|bigwig|gtf|gff|gff3|npy)$")
    explicit: list[str] = []
    for candidate in quoted + unquoted_paths + data_pattern.findall(text):
        if supported_suffix.search(candidate.strip()):
            if candidate.strip() not in explicit:
                explicit.append(candidate.strip())

    directory: Optional[str] = None
    for candidate in quoted + unquoted_paths:
        value = candidate.strip()
        if value and not supported_suffix.search(value) and ("/" in value or "\\" in value):
            directory = value
            break
    if directory is None:
        # Relative project/data paths are common in the browser.  Keep this
        # deliberately conservative so ordinary prose is not mistaken for a
        # directory; absolute paths and quoted paths remain fully supported.
        match = re.search(
            r"(?i)((?:demo|data|cases|examples)[\\/][^\s，。；,;]+(?:[\\/][^\s，。；,;]+)*)",
            text,
        )
        if match:
            value = match.group(1).rstrip("，。；,;.)]")
            if not supported_suffix.search(value):
                directory = value
    if explicit and directory is None:
        directory = _chat_path_parent(explicit[0])
    return explicit, directory


def _chat_workflow_request(
    message: str,
    history: list[Dict[str, str]],
    session: FigureSession,
    *,
    figure_type_override: Optional[str] = None,
    allow_without_draw_words: bool = False,
) -> Optional[Dict[str, Any]]:
    """Parse a chat-only workflow request before the general AI intent path."""
    figure_type = figure_type_override or _chat_workflow_type(message)
    explicit_paths, directory = _chat_extract_paths(message)
    compact = re.sub(r"\s+", "", str(message or "")).casefold()
    draw_words = ("画", "绘制", "生成", "可视化", "出图", "plot", "draw", "visualiz", "workflow")
    if figure_type is None or (
        not allow_without_draw_words
        and not (any(token in compact for token in draw_words) or "figure_type=" in compact)
    ):
        return None

    # “这个目录/上面的路径” can refer to the latest path-bearing user
    # message, which makes a second turn such as “然后画 TAD 图” natural.
    if directory is None and not explicit_paths:
        # A path-only turn followed by “画双样本 TAD 图” is a normal
        # conversational workflow.  First prefer explicit references, then
        # fall back to the most recent path-bearing user turn.  The pending
        # confirmation below always shows the selected root and files, so the
        # user can verify the inferred reference before anything is rendered.
        reference_tokens = ("这个目录", "该目录", "上面的路径", "刚才的路径", "当前数据")
        # Once the user has supplied a path in an earlier turn, a subsequent
        # request such as “然后画双样本三角 Hi-C 对比图” should naturally
        # reuse that path even when they do not repeat “这个目录”.  The
        # confirmation card still exposes the inferred root and exact files,
        # so this convenience never hides which inputs will be used.
        use_history = (
            any(token in compact for token in reference_tokens)
            or allow_without_draw_words
            or any(token in compact for token in draw_words)
        )
        if use_history:
            for entry in reversed(history):
                if entry.get("role") != "user":
                    continue
                old_explicit, old_directory = _chat_extract_paths(entry.get("content", ""))
                if old_explicit:
                    explicit_paths, directory = old_explicit, _chat_path_parent(old_explicit[0])
                    break
                if old_directory:
                    directory = old_directory
                    break
    if directory is None and not explicit_paths:
        # A current FigureSpec may already provide a data root.  This is only
        # a fallback for conversational follow-up; a first request still
        # needs a path so the user sees exactly what will be used.
        source_paths = [str(source.get("path")) for source in session.current_spec.get("data_sources", []) if source.get("path")]
        if source_paths and any(token in compact for token in ("当前图", "当前数据", "这个图", "继续")):
            directory = _chat_path_parent(source_paths[0])
        else:
            return None

    region = SimpleIntentInterpreter._parse_region(message)
    resolution = SimpleIntentInterpreter._parse_resolution(message)
    gene: Optional[str] = None
    gene_phrase = re.search(r"(?i)(?:基因|gene)\s*(?:是|为|叫|用|附近|区域)?\s*([A-Za-z][A-Za-z0-9_.-]{1,})", message)
    if gene_phrase:
        candidate = gene_phrase.group(1)
        if candidate.casefold() not in {"区域", "附近", "轨道", "图", "annotation"}:
            gene = candidate
    if gene is None and any(token in message for token in ("基因", "gene")):
        uppercase = re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", message)
        ignored = {"HIC", "CFIZZ", "TAD", "LOOP", "BED", "GTF", "GFF", "API", "DNA", "RNA"}
        gene = next((value for value in uppercase if value not in ignored), None)

    return {
        "figure_type": figure_type,
        "explicit_paths": explicit_paths,
        "dataset_path": directory,
        "query": region or gene,
        "gene": None if region else gene,
        "region": region,
        "resolution": resolution,
        "message": message,
    }


def _chat_file_key(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _chat_match_selected_file(scan: DatasetScan, raw_path: str, inspector: DataInspector, project_root: Path):
    value = str(raw_path or "").strip().strip("'\"")
    candidate = inspector.platform_path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    wanted = {_chat_file_key(str(resolved)), _chat_file_key(value), _chat_file_key(Path(value).name)}
    for item in scan.files:
        keys = {_chat_file_key(item.path), _chat_file_key(item.name)}
        try:
            keys.add(_chat_file_key(str(Path(item.path).resolve())))
        except OSError:
            pass
        if wanted & keys:
            return item
    return None


_CHAT_COMPANION_KINDS = {
    "compartment": "compartment",
    "oe": "oe",
    "insulation": "insulation",
    "boundaries": "boundaries",
    "loops": "loops",
}
_CHAT_TRACK_ROLES = {"signal", "gene_annotation", "intervals"}


def _chat_sample_key(value: Any) -> str:
    """Normalize a sample label for conservative companion matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _chat_candidate_for_anchor(
    anchor: Optional[Any],
    role: str,
    candidates: list[Any],
    selected_keys: set[str],
) -> Optional[Any]:
    """Return one unambiguous, sample-matched companion candidate.

    The browser's directory picker can show every file, but a conversational
    request must not silently choose a random resolution or another sample.
    Prefer the same companion discovered by CFIZZ's companion resolver; then
    use the sample labels emitted by :func:`scan_dataset`.  A tie is returned
    as ``None`` so the caller can ask the user to choose explicitly.
    """
    available = [
        candidate for candidate in candidates
        if _chat_file_key(candidate.path) not in selected_keys
    ]
    if not available:
        return None

    discovered_key: Optional[str] = None
    if anchor is not None and role in _CHAT_COMPANION_KINDS:
        try:
            discovered = discover_companion(anchor.path, _CHAT_COMPANION_KINDS[role])
        except (OSError, ValueError):
            discovered = None
        if discovered is not None:
            discovered_key = _chat_file_key(str(discovered.path))
            try:
                discovered_key = _chat_file_key(str(discovered.path.resolve()))
            except OSError:
                pass

    anchor_sample = _chat_sample_key(
        getattr(anchor, "sample", None) or (Path(anchor.path).stem if anchor is not None else "")
    )
    scored: list[tuple[int, Any]] = []
    for candidate in available:
        candidate_keys = {_chat_file_key(candidate.path), _chat_file_key(candidate.name)}
        try:
            candidate_keys.add(_chat_file_key(str(Path(candidate.path).resolve())))
        except OSError:
            pass
        candidate_sample = _chat_sample_key(
            getattr(candidate, "sample", None) or Path(candidate.path).stem
        )
        score = 0
        if discovered_key and discovered_key in candidate_keys:
            score += 100_000
        if anchor_sample and candidate_sample == anchor_sample:
            score += 1_000
        elif anchor_sample and candidate_sample and anchor_sample in candidate_sample:
            score += 700
        elif anchor_sample and candidate_sample and candidate_sample in anchor_sample:
            score += 500
        # These are useful tie-breakers only after the sample/discovery score;
        # they never make an unrelated sample look like a valid match.
        name = candidate.name.casefold()
        if role == "boundaries" and ".10b.boundaries" in name:
            score += 10
        if role == "loops" and ".loops." in name:
            score += 10
        scored.append((score, candidate))

    scored.sort(key=lambda value: (-value[0], value[1].name.casefold(), value[1].path.casefold()))
    if not scored or scored[0][0] <= 0:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _chat_auto_companion_allowed(figure_type: str, role: str) -> bool:
    """Whether chat may infer a regular companion for this workflow.

    Differential result files are intentionally never guessed from an HIC
    path: a normal boundary/loop table and a differential table can share the
    same suffix while representing different analyses.  The user must name
    those result files explicitly.  Ordinary single/multi-sample views can
    safely use the same CFIZZ companion discovery used by the HIC endpoint.
    """
    if "diff" in str(figure_type).casefold() or str(figure_type) in {
        "tad_diff_stacked", "loop_diff_stacked", "compartment_diff_scatter",
    }:
        return False
    return role in _CHAT_COMPANION_KINDS


def _chat_contract_error(item: Dict[str, Any], text: str) -> ValueError:
    return ValueError(f"“{item.get('label', '')}”{text}请在对话中明确给出文件路径，或回到数据面板选择文件。")


def _chat_select_workflow_files(
    scan: DatasetScan,
    figure_type: str,
    explicit_paths: list[str],
    message: str,
    inspector: DataInspector,
    project_root: Path,
) -> list[str]:
    """Select only the bounded inputs required by a chat-requested workflow."""
    item = FIGURE_TYPE_BY_ID.get(str(figure_type)) or {}
    contract = item.get("input_contract") or {}
    roles = contract.get("roles") or {}
    want_tracks = any(token in str(message).casefold() for token in ("轨道", "bigwig", "bw", "gtf", "gff", "bed", "多组学", "foxj1"))
    selected_by_role: Dict[str, list[Any]] = {}
    usable_by_role: Dict[str, list[Any]] = {}
    for role in roles:
        candidates = [
            file for file in scan.files
            if file.usable and file.role == str(role) and _source_type_supports_role(file.type, file.role)
        ]
        candidates.sort(key=lambda file: (str(file.sample or "").casefold(), file.name.casefold(), file.path.casefold()))
        usable_by_role[str(role)] = candidates

    # Keep one ordered list for both explicit-path and directory-selection
    # branches. The explicit branch fills it while resolving companions;
    # the directory branch fills it from ``selected_by_role`` below.
    selected: list[str] = []

    # Explicit chat paths are authoritative, but they do not disable the
    # contract: required companions are completed only when CFIZZ can identify
    # an unambiguous same-sample file.  This is the key difference from the old
    # implementation, which passed a lone HIC path through and failed later in
    # the renderer with a missing insulation/loop/E1 input.
    if explicit_paths:
        for raw_path in explicit_paths:
            matched = _chat_match_selected_file(scan, raw_path, inspector, project_root)
            if matched is None or not matched.usable:
                raise ValueError(f"找不到可用的数据文件：{raw_path}。请确认路径属于扫描目录，或直接提供所在目录。")
            if matched.path not in selected:
                selected.append(matched.path)
                selected_by_role.setdefault(str(matched.role), []).append(matched)

        selected_keys = {_chat_file_key(path) for path in selected}
        hic_rule = roles.get("hic") or {}
        hic_min = int(hic_rule.get("min") or 0)
        hic_max = hic_rule.get("max")
        hic_items = selected_by_role.get("hic", [])
        hic_candidates = usable_by_role.get("hic", [])
        if hic_min and not hic_items:
            # A direct companion path (for example boundaries.tsv) can still
            # infer its matrix when the sample label is unique.  Never choose
            # between two unrelated HIC files without telling the user.
            hint = next(iter(selected_by_role.values()), [None])[0]
            candidate = _chat_candidate_for_anchor(hint, "hic", hic_candidates, selected_keys)
            if candidate is None:
                if len(hic_candidates) == 1:
                    candidate = hic_candidates[0]
                else:
                    raise _chat_contract_error(item, f"需要至少 {hic_min} 个 Hi-C 文件，当前路径无法唯一对应到 Hi-C。")
            selected.append(candidate.path)
            selected_by_role.setdefault("hic", []).append(candidate)
            selected_keys.add(_chat_file_key(candidate.path))
            hic_items = selected_by_role["hic"]
        if hic_min and len(hic_items) < hic_min:
            raise _chat_contract_error(item, f"至少需要 {hic_min} 个 Hi-C 文件，当前只明确选择了 {len(hic_items)} 个。")
        if hic_max is not None and len(hic_items) > int(hic_max):
            raise _chat_contract_error(item, f"最多只能选择 {int(hic_max)} 个 Hi-C 文件，当前选择了 {len(hic_items)} 个。")

        anchor_count = len(hic_items)
        for role, rule in roles.items():
            role = str(role)
            if role == "hic":
                continue
            rule = rule or {}
            minimum = int(rule.get("min") or 0)
            per_anchor = bool(rule.get("per_anchor"))
            current = len(selected_by_role.get(role, []))
            if minimum <= current:
                continue
            if not _chat_auto_companion_allowed(figure_type, role):
                label = str(rule.get("label") or role)
                raise _chat_contract_error(item, f"还需要明确选择 {label} 文件；差异分析不会自动猜测结果文件。")
            if per_anchor and anchor_count == 0:
                raise _chat_contract_error(item, f"需要先选择 Hi-C 样本，才能为每个样本配对 {role} 文件。")
            candidates = usable_by_role.get(role, [])
            anchors = hic_items if per_anchor else [hic_items[0] if hic_items else None]
            desired = anchor_count * max(1, minimum) if per_anchor else minimum - current
            added = 0
            for anchor in anchors:
                for _ in range(max(1, minimum) if per_anchor else desired):
                    candidate = _chat_candidate_for_anchor(anchor, role, candidates, selected_keys)
                    if candidate is None:
                        label = str(rule.get("label") or role)
                        raise _chat_contract_error(item, f"无法唯一找到与样本对应的 {label} 文件。")
                    selected.append(candidate.path)
                    selected_by_role.setdefault(role, []).append(candidate)
                    selected_keys.add(_chat_file_key(candidate.path))
                    added += 1
            if added < desired:
                label = str(rule.get("label") or role)
                raise _chat_contract_error(item, f"需要 {desired} 个 {label} 文件，但只找到 {added} 个可唯一配对的文件。")

        # Optional tracks are opt-in.  If the user says “加轨道/多组学” while
        # naming only the HIC path, add at most one same-sample candidate per
        # track role.  If they did not ask for tracks, leave optional roles
        # untouched so a bare Hi-C request stays a bare Hi-C request.
        if want_tracks:
            anchor = (selected_by_role.get("hic") or [None])[0]
            for role in _CHAT_TRACK_ROLES & set(roles):
                if selected_by_role.get(role):
                    continue
                candidate = _chat_candidate_for_anchor(anchor, role, usable_by_role.get(role, []), selected_keys)
                if candidate is not None:
                    selected.append(candidate.path)
                    selected_by_role.setdefault(role, []).append(candidate)
                    selected_keys.add(_chat_file_key(candidate.path))

        for group in contract.get("any_of") or []:
            group_roles = [str(role) for role in group.get("roles") or []]
            minimum = int(group.get("min") or 0)
            if sum(len(selected_by_role.get(role, [])) for role in group_roles) >= minimum:
                continue
            if want_tracks:
                anchor = (selected_by_role.get("hic") or [None])[0]
                for role in group_roles:
                    candidate = _chat_candidate_for_anchor(anchor, role, usable_by_role.get(role, []), selected_keys)
                    if candidate is None:
                        continue
                    selected.append(candidate.path)
                    selected_by_role.setdefault(role, []).append(candidate)
                    selected_keys.add(_chat_file_key(candidate.path))
                    break
            if sum(len(selected_by_role.get(role, [])) for role in group_roles) < minimum:
                labels = "、".join(str(role) for role in group_roles)
                raise _chat_contract_error(item, f"至少需要 {minimum} 个以下类型之一的文件：{labels}。")
        return selected

    hic_rule = roles.get("hic") or {}
    hic_candidates = usable_by_role.get("hic", [])
    hic_max = hic_rule.get("max")
    hic_min = int(hic_rule.get("min") or 0)
    if hic_candidates:
        if hic_max is not None and int(hic_max) == hic_min:
            hic_count = hic_min
        elif hic_min >= 2:
            hic_count = hic_min
        else:
            hic_count = 1
        if len(hic_candidates) < hic_count:
            raise ValueError(f"“{item.get('label', figure_type)}”需要 {hic_count} 个可用 Hi-C 文件，当前目录只有 {len(hic_candidates)} 个。")
        selected_by_role["hic"] = hic_candidates[:hic_count]

    anchor_count = len(selected_by_role.get("hic", []))
    for role, rule in roles.items():
        role = str(role)
        if role == "hic":
            continue
        candidates = usable_by_role.get(role, [])
        minimum = int((rule or {}).get("min") or 0)
        per_anchor = bool((rule or {}).get("per_anchor"))
        if role in {"signal", "gene_annotation", "intervals"} and not want_tracks and minimum == 0:
            continue
        if per_anchor:
            desired = anchor_count * max(1, minimum)
        elif minimum:
            desired = minimum
        else:
            # Optional tracks are opt-in from chat and are intentionally
            # bounded to one representative file per role unless paths are
            # explicitly named.
            desired = 1 if want_tracks else 0
        maximum = (rule or {}).get("max")
        if maximum is not None:
            desired = min(desired, int(maximum) * max(1, anchor_count) if per_anchor else int(maximum))
        if len(candidates) < desired:
            label = str((rule or {}).get("label") or role)
            raise ValueError(f"“{item.get('label', figure_type)}”需要 {desired} 个{label}文件，但目录中只有 {len(candidates)} 个可用文件。请上传或明确指定文件。")
        selected_by_role[role] = candidates[:desired]

    for role in roles:
        selected.extend(file.path for file in selected_by_role.get(str(role), []))
    if not selected:
        # Workflows without a Hi-C anchor (for example result-only
        # differential plots) still use the same contract-driven selector.
        selected = _default_workflow_selection_paths(scan, figure_type)
    if not selected:
        raise ValueError(f"目录中没有发现可用于“{item.get('label', figure_type)}”的输入文件。")
    return selected


def _chat_expand_scan_root(
    service: WorkspaceService,
    raw_root: str,
    explicit_paths: list[str],
    figure_type: str,
) -> Path:
    """Widen a chat scan to the CFIZZ experiment root when necessary.

    A user commonly pastes ``.../data/sample.mcool`` even though CFIZZ's
    generated insulation, loop, boundary, or E1 files live beside ``data``
    under ``1_3_hicviz_output``.  The UI's directory scanner sees the whole
    experiment, but a chat request used to scan only the HIC's parent and then
    reported a misleading missing-file error.  Expand only to the smallest
    common ancestor containing the HIC and discovered companions; plain Hi-C
    workflows retain their narrow scan root.
    """
    root = service.inspector.platform_path(raw_root).expanduser()
    if not root.is_absolute():
        root = service.project_root / root
    root = root.resolve()
    if root.is_file():
        root = root.parent

    item = FIGURE_TYPE_BY_ID.get(str(figure_type)) or {}
    roles = (item.get("input_contract") or {}).get("roles") or {}
    companion_roles = [
        str(role) for role, rule in roles.items()
        if str(role) in _CHAT_COMPANION_KINDS and int((rule or {}).get("min") or 0) > 0
    ]
    if not companion_roles:
        return root

    def resolved_input(raw_path: str) -> Optional[Path]:
        candidate = service.inspector.platform_path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = service.project_root / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        return candidate if candidate.is_file() else None

    explicit_files = [path for raw_path in explicit_paths if (path := resolved_input(raw_path))]
    hic_paths = [
        path for path in explicit_files
        if path.suffix.casefold() in {".cool", ".mcool"}
    ]

    def find_hics(directory: Path) -> list[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        return sorted(
            (path for path in directory.rglob("*") if path.is_file() and path.suffix.casefold() in {".cool", ".mcool"}),
            key=lambda path: str(path).casefold(),
        )[:64]

    if not hic_paths:
        hic_paths = find_hics(root)
    # If a companion path was pasted from a nested result directory, look up
    # to three experiment parents for its HIC anchors.  This mirrors the
    # bounded search policy in companions._search_roots without scanning the
    # entire project tree.
    if not hic_paths:
        for parent in [root, *list(root.parents)[:3]]:
            candidates = find_hics(parent)
            if candidates:
                root = parent
                hic_paths = candidates
                break

    candidate_roots = [root]
    for hic_path in hic_paths:
        for role in companion_roles:
            try:
                companion = discover_companion(hic_path, _CHAT_COMPANION_KINDS[role])
            except (OSError, ValueError):
                companion = None
            if companion is not None:
                candidate_roots.append(companion.path.parent.resolve())
    try:
        common = Path(os.path.commonpath([str(path) for path in candidate_roots])).resolve()
    except (OSError, ValueError):
        common = root
    if common.is_file():
        common = common.parent
    return common


def _chat_prepare_workflow(
    service: WorkspaceService,
    session: FigureSession,
    request: Dict[str, Any],
) -> tuple[Dict[str, Any], list[str], list[Dict[str, Any]], Dict[str, Any]]:
    """Build a temporary spec exactly as the fixed workflow endpoint does."""
    figure_type = str(request["figure_type"])
    item = FIGURE_TYPE_BY_ID.get(figure_type)
    if item is None or not item.get("ready"):
        raise ValueError("该 CFIZZ 工作流未登记或当前不可执行。")
    raw_root = str(request.get("dataset_path") or "").strip()
    root = _chat_expand_scan_root(
        service, raw_root, list(request.get("explicit_paths") or []), figure_type,
    )
    service.inspector.authorize_root(str(root))
    scan = scan_dataset(
        str(root), service.inspector, service.references,
        # A genomic interval is a viewport request, not a gene lookup.  Only
        # pass an actual gene symbol to the dataset scanner; otherwise a
        # string/tuple such as ("chr1", 0, 2_000_000) can be misinterpreted
        # as a gene query and produce misleading annotation warnings.
        gene=request.get("gene"), build="hg38",
    )
    selected_paths = _chat_select_workflow_files(
        scan, figure_type, list(request.get("explicit_paths") or []), request.get("message", ""),
        service.inspector, service.project_root,
    )
    selected_scan = select_dataset_files(scan, selected_paths)
    bindings: list[Dict[str, Any]] = []
    pairing = ((item.get("input_contract") or {}).get("pairing") or {})
    if pairing:
        anchor_role = str(pairing.get("anchor_role") or "hic")
        companion_roles = [str(value) for value in pairing.get("companion_roles") or []]
        anchors = [file for file in selected_scan.files if file.usable and file.role == anchor_role]
        by_role: Dict[str, list[Any]] = {}
        for file in selected_scan.files:
            if file.usable:
                by_role.setdefault(file.role, []).append(file)
        bindings = _suggest_workflow_bindings(anchors, by_role, companion_roles)
        if len(bindings) != len(anchors):
            # Let the canonical validator produce the detailed “sample X is
            # missing role Y” message instead of silently pairing by order.
            prepare_workflow_selection(selected_scan, figure_type, [], False)
        selected_scan, normalized = prepare_workflow_selection(selected_scan, figure_type, bindings, True)
        bindings = normalized
    else:
        selected_scan, bindings = prepare_workflow_selection(selected_scan, figure_type, [], False)

    requires_hic = _workflow_requires_hic(figure_type)
    if not requires_hic:
        selected_scan = replace(
            selected_scan,
            missing=[message for message in selected_scan.missing if "cool" not in message.lower() and "mcool" not in message.lower()],
        )
        spec = build_workflow_spec(
            selected_scan, session.session_id, figure_type, service.references,
            gene=request.get("gene"), build="hg38", resolution=request.get("resolution"),
        )
    else:
        spec = build_integrated_spec(
            selected_scan, session.session_id, service.references,
            gene=request.get("gene"), build="hg38", resolution=request.get("resolution"),
        )
    if bindings:
        spec.setdefault("metadata", {})["workflow_bindings"] = bindings
    if request.get("region"):
        chrom, start, end = request["region"]
        spec.setdefault("viewport", {}).update({
            "chrom": chrom, "start": int(start), "end": int(end),
            "focus_label": f"{chrom}:{int(start):,}-{int(end):,}",
        })
        spec.setdefault("metadata", {})["viewport_selection"] = "explicit_user_region"
    elif requires_hic and not request.get("gene"):
        _auto_default_viewport(spec, figure_type, service.inspector)

    working = FigureSession(f"{session.session_id}_chat_workflow", spec, validator=service.validator)
    source_ids = [str(source.get("id")) for source in spec.get("data_sources", []) if source.get("id")]
    workflow_intent = IntentResult(
        "workflow", f"准备执行 CFIZZ 工作流：{item['label']}。",
        {"workflow_request": {"figure_type": figure_type, "source_ids": source_ids, "options": {}}},
        planner="chat:workflow",
    )
    resolved = _resolve_ai_workflow_intent(working, workflow_intent)
    if resolved.action != "patch" or not resolved.patch:
        raise ValueError(resolved.reply)
    patch = _augment_compartment_patch(resolved.patch, working.current_spec, service.inspector)
    working.apply_patch(patch)
    return working.current_spec, selected_paths, bindings, item


def _chat_workflow_confirmation(
    service: WorkspaceService,
    session: FigureSession,
    request: Dict[str, Any],
) -> IntentResult:
    """Prepare, explain, and hold a chat workflow until the user confirms."""
    try:
        spec, selected_paths, bindings, item = _chat_prepare_workflow(service, session, request)
    except (OSError, PermissionError, ValueError) as exc:
        return IntentResult("clarify", f"我识别到了这个绘图请求，但固定 CFIZZ 工作流暂时不能执行：{exc}", planner="chat:workflow")
    viewport = spec.get("viewport") or {}
    region = f"{viewport.get('chrom', '—')}:{int(viewport.get('start', 0)):,}-{int(viewport.get('end', 0)):,}"
    resolution = (spec.get("analysis") or {}).get("resolution")
    names = "、".join(Path(path).name for path in selected_paths)
    pairing_text = ""
    if bindings:
        pairs = []
        for binding in bindings:
            companions = "、".join(Path(value).name for value in (binding.get("companions") or {}).values())
            pairs.append(f"{Path(binding.get('anchor_path', '')).name} → {companions}")
        pairing_text = "\n样本配对：" + "；".join(pairs)
    reply = (
        f"我识别到你的请求：使用“{item['label']}”（CFIZZ：{item['entrypoint']}）。\n"
        f"数据路径：{request.get('dataset_path')}\n"
        f"将使用文件：{names}\n"
        f"区域：{region}；分辨率：{int(resolution):,} bp。"
        f"{pairing_text}\n"
        "请回复“确认”开始调用固定 CFIZZ 工作流；回复“取消”放弃。生成后仍可继续用自然语言修改。"
    )
    service.pending_actions[session.session_id] = {
        "kind": "workflow",
        "spec": deepcopy(spec),
        "summary": f"使用聊天确认的数据生成 {item['label']}",
        "apply_reply": f"已确认，将使用上述文件调用 CFIZZ 官方工作流生成“{item['label']}”。",
    }
    return IntentResult("clarify", reply, planner="chat:workflow")


def _auto_default_viewport(spec: Dict[str, Any], figure_type: str, inspector: DataInspector) -> None:
    """Choose a data-backed default viewport for a new CFIZZ workflow.

    The range precedence is deliberately explicit:

    1. an entered gene or genomic interval (``focus_label``) is authoritative;
    2. exact feature files selected for this figure provide event/anchor
       ranges using the same helpers as the official CFIZZ examples;
    3. when no feature file is selected, keep the neutral 0--2 Mb starter
       range and explain that choice in metadata.

    This prevents a directory-wide companion search from choosing a different
    sample's result and replaces the old one-size-fits-all 20 Mb window.
    """
    viewport = spec.get("viewport") or {}
    if viewport.get("focus_label"):
        metadata = spec.setdefault("metadata", {})
        metadata["viewport_selection"] = "explicit_user_region"
        return
    kind = _viewport_feature_kind(figure_type)
    if kind is None:
        return
    source = next((item for item in spec.get("data_sources", []) if item.get("type") in {"cool", "mcool"}), None)
    if not source:
        return
    chrom = str(viewport.get("chrom") or "")
    start = int(viewport.get("start", 0))
    end = int(viewport.get("end", 2_000_000))
    feature_paths = _selected_feature_paths(spec, kind, inspector)
    # A workflow may select both an event table and the matrix companion that
    # defines its bins (notably differential TAD: boundaries + insulation).
    # Prefer the resolution encoded in those *selected* files over a stale UI
    # default.  ``analysis.resolution`` is intentionally only a fallback: the
    # official CFIZZ readers reject intervals whose length is not divisible by
    # the feature's bin width.
    resolution_candidates: list[int] = []
    for feature_path in feature_paths:
        value = companion_resolution(feature_path)
        if value:
            resolution_candidates.append(int(value))
    if figure_type == "tad_diff_region":
        for feature_path in _selected_feature_paths(spec, "insulation", inspector):
            value = companion_resolution(feature_path)
            if value:
                resolution_candidates.append(int(value))
    analysis_resolution = (spec.get("analysis") or {}).get("resolution")
    try:
        resolution = int(analysis_resolution)
    except (TypeError, ValueError):
        resolution = 10_000
    if resolution_candidates:
        unique_resolutions = sorted(set(resolution_candidates))
        # If multiple selected files disagree, let the adapter emit its more
        # specific pairing error.  For viewport selection, using the finest
        # common-looking value keeps coordinates aligned instead of crashing
        # on ``resolution='auto'``.
        resolution = unique_resolutions[0]
    if not feature_paths:
        metadata = spec.setdefault("metadata", {})
        metadata["viewport_selection"] = "neutral_default"
        metadata["viewport_reason"] = f"未选中可用于 {kind} 范围推断的 CFIZZ 结果，保留 chr1 起始 2 Mb；可在对话中指定基因或范围。"
        return
    suggestion = suggest_selected_viewport(
        feature_paths,
        chrom,
        start,
        end,
        kind=kind,
        figure_type=figure_type,
        resolution=resolution,
    )
    viewport["start"], viewport["end"] = int(suggestion.start), int(suggestion.end)
    spec["viewport"] = viewport
    metadata = spec.setdefault("metadata", {})
    metadata["viewport_selection"] = suggestion.method
    metadata["viewport_reason"] = suggestion.reason
    metadata["viewport_sources"] = list(suggestion.source_paths)
    metadata["viewport_resolution"] = int(resolution)


def _resolve_ai_workflow_intent(session: FigureSession, intent: IntentResult) -> IntentResult:
    """Bind an AI-selected workflow to the current validated FigureSpec.

    This resolver never draws and never accepts an arbitrary entrypoint.  It
    only compiles catalogue IDs into existing FigurePatch operations; the
    render adapter remains the sole gateway to official CFIZZ APIs.
    """
    request = (intent.patch or {}).get("workflow_request", {})
    figure_type = request.get("figure_type")
    item = FIGURE_TYPE_BY_ID.get(str(figure_type))
    if item is None or not item.get("ready"):
        return IntentResult("clarify", "该可视化没有登记为可执行的 CFIZZ 工作流。", planner=intent.planner)

    spec = session.current_spec
    sources = spec.get("data_sources", [])
    hic_count = sum(source.get("type") in {"cool", "mcool"} for source in sources)
    track_count = sum(source.get("type") in {"bigwig", "gtf", "gff", "bed"} for source in sources)
    target_type = None
    source_ids = request.get("source_ids") or []
    known_source_ids = {source.get("id") for source in sources}
    if any(source_id not in known_source_ids for source_id in source_ids):
        return IntentResult(
            "clarify", "AI 选择了当前图中不存在的数据源，请重新提供文件或扫描实验目录。",
            planner=f"{intent.planner}:workflow-inputs",
        )
    selected_sources = [source for source in sources if not source_ids or source.get("id") in source_ids]
    selected_hics = [source for source in selected_sources if source.get("type") in {"cool", "mcool"}]
    minimum_hics = {
        "hic_multi": 2, "compartment_multi": 2, "tad_multi": 2,
        "loop_multi": 2, "loop_apa_multi": 2,
        "compartment_diff_region": 2, "tad_diff_region": 2,
        "loop_diff_region": 2, "tad_diff_pileup": 2, "loop_diff_apa": 2,
    }.get(str(figure_type), 0)
    hic_workflows = {
        "compartment_saddle", "tad_boundary_pileup", "tracks_integrated",
        "tad_diff_region", "loop_diff_region", "tad_diff_pileup", "loop_diff_apa",
    }
    if minimum_hics and len(selected_hics) < minimum_hics:
        return IntentResult(
            "clarify", f"“{item['label']}”至少需要 {minimum_hics} 个已绑定的 cool/mcool 样本。请添加文件或扫描实验目录。",
            planner=f"{intent.planner}:workflow-inputs",
        )
    if figure_type in hic_workflows and not selected_hics:
        return IntentResult(
            "clarify", f"“{item['label']}”需要已绑定的 cool/mcool 数据源。请提供文件或实验目录。",
            planner=f"{intent.planner}:workflow-inputs",
        )
    track_types = {source.get("type") for source in selected_sources}
    required_track_types = {
        "tracks_signal": {"bigwig"}, "tracks_genes": {"gtf", "gff"},
        "tracks_intervals": {"bed"}, "tracks_mixed": {"bigwig", "gtf", "gff", "bed"},
    }
    if figure_type in required_track_types and not (track_types & required_track_types[str(figure_type)]):
        return IntentResult(
            "clarify", f"“{item['label']}”缺少匹配的轨道数据：{'、'.join(item.get('requires') or [])}。",
            planner=f"{intent.planner}:workflow-inputs",
        )
    if figure_type in DIRECT_FIGURE_TYPE_IDS:
        target_type = figure_type
    elif figure_type in FIGURE_TYPE_BY_ID:
        # Conversation workflows are first-class registered FigureSpec types.
        # The adapter performs the final role/sample binding and is the only
        # component allowed to dispatch the public CFIZZ API.
        target_type = figure_type

    if target_type is None:
        requirements = "、".join(item.get("requires") or []) or "相应的 CFIZZ 输入数据"
        return IntentResult(
            "clarify",
            f"可以用 {item['entrypoint']} 生成“{item['label']}”，但当前会话还没有绑定完整输入：{requirements}。"
            "请提供文件或实验目录；Agent 会先验证和匹配样本，再调用该 CFIZZ 接口。",
            planner=f"{intent.planner}:workflow-inputs",
        )

    operations = [{"op": "update", "target_kind": "figure", "field": "figure_type", "value": target_type}]
    if figure_type in {"hic_multi", "tad_multi", "loop_multi", "loop_diff_region", "tad_diff_region"}:
        hic_layers = [layer for panel in spec.get("panels", []) for layer in panel.get("layers", []) if layer.get("kind") == "hic"]
        for index, layer in enumerate(hic_layers):
            operations.extend([
                {"op": "update", "target_kind": "layer", "target_id": layer["id"], "field": "style.triangle_ratio", "value": 1},
                {"op": "update", "target_kind": "layer", "target_id": layer["id"], "field": "style.flip_vertical", "value": bool(index % 2)},
            ])
    operations.extend([
        {"op": "update", "target_kind": "figure", "field": "workflow_source_ids", "value": source_ids},
        {"op": "update", "target_kind": "figure", "field": "workflow_options", "value": request.get("options") or {}},
    ])
    operations.append({"op": "update", "target_kind": "figure", "field": "title", "value": item["label"]})
    return IntentResult(
        "patch", intent.reply,
        {"summary": f"切换到 {item['label']}", "operations": operations},
        planner=f"{intent.planner}:workflow",
    )


def _augment_compartment_patch(patch: Dict[str, Any], spec: Dict[str, Any], inspector: DataInspector) -> Dict[str, Any]:
    """Add data-backed defaults when a conversation changes figure type.

    The name is kept for API compatibility with older callers, but the helper
    now handles every feature-driven type.  Compartment keeps its historical
    broad E1 window when a direct Hi-C session has no explicitly selected E1;
    TAD/Loop switches use the exact selected files and the official range
    algorithms instead of rediscovering an arbitrary neighbour.
    """
    result = deepcopy(patch)
    operations = result.get("operations", [])
    switch_values = [
        operation.get("value")
        for operation in operations
        if operation.get("op") == "update"
        and operation.get("target_kind") == "figure"
        and operation.get("field") == "figure_type"
    ]
    switches = any(value == "compartment" for value in switch_values)
    is_compartment = switches or spec.get("figure_type") == "compartment"
    if not is_compartment:
        # Conversation/UI switches to TAD or Loop should receive the same
        # automatic range as a freshly created workflow.  Use a temporary spec
        # so explicit user regions remain untouched and do not mutate the
        # session before validation.
        for target_type in switch_values:
            if not isinstance(target_type, str) or target_type == "compartment":
                continue
            preview = deepcopy(spec)
            preview["figure_type"] = target_type
            _auto_default_viewport(preview, target_type, inspector)
            old_view = spec.get("viewport") or {}
            new_view = preview.get("viewport") or {}
            if (
                not (old_view.get("focus_label"))
                and (new_view.get("start"), new_view.get("end"))
                != (old_view.get("start"), old_view.get("end"))
            ):
                operations.extend([
                    {"op": "update", "target_kind": "viewport", "field": "start", "value": int(new_view["start"])},
                    {"op": "update", "target_kind": "viewport", "field": "end", "value": int(new_view["end"])},
                ])
            break
        return result

    hic_source = next((source for source in spec.get("data_sources", []) if source.get("type") in {"cool", "mcool"}), None)
    if hic_source is None:
        return result
    feature_paths = _selected_feature_paths(spec, "compartment", inspector)
    companion = None
    if feature_paths:
        companion = Path(feature_paths[0])
    else:
        # A direct ``from-hic`` session may intentionally start with only a
        # matrix.  Keep the legacy convenience fallback for this explicit
        # compartment switch; dataset workflows never reach it because their
        # selected E1 source is already in the FigureSpec.
        discovered = discover_companion(inspector.resolve_path(hic_source["path"]), "compartment")
        companion = discovered.path if discovered is not None else None
    if companion is None:
        return result
    resolved_companion_resolution = None
    try:
        resolved_companion_resolution = companion_resolution(companion)
    except (ImportError, OSError, ValueError):
        resolved_companion_resolution = None
    if switches and resolved_companion_resolution:
        operations.append({"op": "update", "target_kind": "analysis", "field": "resolution", "value": resolved_companion_resolution})
    hic_label = Path(inspector.resolve_path(hic_source["path"])).stem
    expected_title = f"{hic_label} A/B compartment"
    if spec.get("title") != expected_title:
        operations.append({"op": "update", "target_kind": "figure", "field": "title", "value": expected_title})
    viewport = spec.get("viewport", {})
    focus_label = str(viewport.get("focus_label") or "")
    # A gene name and a chromosome/range are both explicit user choices.
    # Automatic broad compartment windows are only appropriate when no focus
    # was supplied at all.
    explicit_region = bool(focus_label)
    if feature_paths:
        suggestion = suggest_selected_viewport(
            feature_paths,
            str(viewport.get("chrom")),
            int(viewport.get("start", 0)),
            int(viewport.get("end", 2_000_000)),
            kind="compartment",
            figure_type="compartment",
            resolution=resolved_companion_resolution or int((spec.get("analysis") or {}).get("resolution") or 100_000),
        )
        start, end = suggestion.start, suggestion.end
    else:
        start, end = suggest_compartment_viewport(
            companion,
            str(viewport.get("chrom")),
            int(viewport.get("start", 0)),
            int(viewport.get("end", 2_000_000)),
        )
    if switches and not explicit_region and (start, end) != (viewport.get("start"), viewport.get("end")):
        operations.extend([
            {"op": "update", "target_kind": "viewport", "field": "start", "value": start},
            {"op": "update", "target_kind": "viewport", "field": "end", "value": end},
        ])
        result["summary"] = f"{result.get('summary', '切换 A/B Compartment')}；自动选择有效 E1 区域"
    return result


def _hic_comparison_intent(
    service: WorkspaceService,
    session: FigureSession,
    message: str,
    history: Optional[list[Dict[str, str]]] = None,
) -> Optional[IntentResult]:
    """Add a second COOL/MCOOL as a real CFIZZ multi-Hi-C comparison."""
    compact = re.sub(r"\s+", "", message).lower()
    comparison = any(token in compact for token in ("对比", "比较", "并排", "两个hic", "两个hi-c", "第二个hic", "第二个hi-c"))
    hic_hint = any(token in compact for token in ("hic", "hi-c", ".cool", ".mcool", "热图", "图层"))
    path = _extract_hic_file(message)

    if path is None and history and comparison:
        for item in reversed(history):
            if item.get("role") != "user":
                continue
            previous_path = _extract_hic_file(str(item.get("content") or ""))
            if previous_path:
                path = previous_path
                break
    if not comparison or not hic_hint:
        return None
    if path is None:
        return IntentResult(
            "clarify",
            "可以使用 CFIZZ 多 Hi-C 接口生成双样本对比图。请提供第二个 .cool 或 .mcool 文件路径。",
            planner="data:hic-comparison",
        )

    platform_path = service.inspector.platform_path(path).expanduser()
    if not platform_path.is_absolute():
        platform_path = service.project_root / platform_path
    if platform_path.is_file():
        service.inspector.authorize_root(str(platform_path.parent))
    inspection = service.inspector.inspect(str(platform_path))
    if not inspection.usable or inspection.type not in {"cool", "mcool"}:
        return IntentResult(
            "answer",
            inspection.error or f"{path} 不是可用的 .cool/.mcool Hi-C 文件。",
            planner="data:hic-comparison",
        )
    resolved_path = str(Path(inspection.path).resolve())
    spec = session.current_spec
    sources = spec.get("data_sources", [])
    existing_paths = {
        str(service.inspector.resolve_path(source["path"]))
        for source in sources
        if source.get("type") in {"cool", "mcool"} and source.get("path")
    }
    if resolved_path in existing_paths:
        return IntentResult("answer", f"当前图已经包含 {Path(resolved_path).stem}，无需重复添加。", planner="data:hic-comparison")

    resolution = int(spec.get("analysis", {}).get("resolution", 0) or 0)
    available_resolutions = [int(value) for value in inspection.metadata.get("resolutions", [])]
    if resolution and available_resolutions and resolution not in available_resolutions:
        choices = "、".join(f"{value / 1000:g}k" for value in available_resolutions)
        return IntentResult(
            "answer",
            f"第二个 Hi-C 文件不包含当前 {resolution / 1000:g}k 分辨率；可用分辨率为 {choices}。请先选择两者共有的分辨率。",
            planner="data:hic-comparison",
        )
    viewport = spec.get("viewport", {})
    supported_chrom = normalize_chromosome(str(viewport.get("chrom") or ""), inspection.metadata.get("chromosomes", []))
    if not supported_chrom:
        return IntentResult(
            "answer",
            f"第二个 Hi-C 文件不包含当前染色体 {viewport.get('chrom')}，无法在同一区域比较。",
            planner="data:hic-comparison",
        )
    if spec.get("analysis", {}).get("balance", True) and inspection.metadata.get("has_balance_weights") is False:
        return IntentResult(
            "answer",
            "第二个 Hi-C 文件没有 balance 权重，而当前图使用平衡矩阵。请先关闭 balance，或提供带权重的文件。",
            planner="data:hic-comparison",
        )

    hic_panel = next(
        (panel for panel in spec.get("panels", []) if any(layer.get("kind") == "hic" for layer in panel.get("layers", []))),
        None,
    )
    if hic_panel is None:
        return IntentResult("answer", "当前图没有 Hi-C 面板，无法添加对比图层。", planner="data:hic-comparison")
    hic_layers = [layer for layer in hic_panel.get("layers", []) if layer.get("kind") == "hic"]
    source_ids = {str(source.get("id")) for source in sources}
    index = 2
    while f"hic_compare_{index}" in source_ids:
        index += 1
    source_id = f"hic_compare_{index}"
    layer_id = f"{source_id}_layer"
    second_label = Path(resolved_path).stem
    first_source = next((source for source in sources if hic_layers and source.get("id") == hic_layers[0].get("source_id")), None)
    first_label = Path(str(first_source.get("path"))).stem if first_source else (hic_layers[0].get("label") if hic_layers else "Hi-C 1")
    base_style = deepcopy(hic_layers[0].get("style", {})) if hic_layers else {}
    base_style.update({"triangle_ratio": 1, "flip_vertical": True})
    operations: list[Dict[str, Any]] = []
    for layer_index, existing_layer in enumerate(hic_layers):
        operations.extend([
            {"op": "update", "target_kind": "layer", "target_id": existing_layer["id"], "field": "style.triangle_ratio", "value": 1},
            {"op": "update", "target_kind": "layer", "target_id": existing_layer["id"], "field": "style.flip_vertical", "value": bool(layer_index % 2)},
        ])
        if layer_index == 0 and existing_layer.get("label") in {None, "", "Hi-C"}:
            operations.append({"op": "update", "target_kind": "layer", "target_id": existing_layer["id"], "field": "label", "value": first_label})
    operations.extend([
        {"op": "add_source", "source": {
            "id": source_id, "type": inspection.type, "path": resolved_path,
            "sample": second_label, "label": second_label,
        }},
        {"op": "add_layer", "panel_id": hic_panel["id"], "layer": {
            "id": layer_id, "kind": "hic", "source_id": source_id,
            "label": second_label, "visible": True, "style": base_style,
        }},
        {"op": "update", "target_kind": "figure", "field": "title", "value": f"{first_label} vs {second_label} Hi-C comparison"},
    ])
    return IntentResult(
        "patch",
        f"已验证 {second_label}，将通过 CFIZZ 多 Hi-C 接口与 {first_label} 在同一区域上下镜像比较；现有基因和信号轨道保留。",
        {"summary": f"添加 {second_label} Hi-C 对比图层", "operations": operations},
        planner="data:hic-comparison",
    )


def _extract_hic_file(message: str) -> Optional[str]:
    """Extract an explicit Windows, POSIX, or project-relative COOL path."""
    quoted = re.search(r"[\"'“”]([^\"'“”]+\.(?:mcool|cool))[\"'“”]", message, re.I)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(
        r"((?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])?[^\s，。；,;\"']+?\.(?:mcool|cool))",
        message,
        re.I,
    )
    return match.group(1).strip() if match else None


def _directory_track_intent(
    service: WorkspaceService,
    session: FigureSession,
    message: str,
    history: Optional[list[Dict[str, str]]] = None,
) -> Optional[IntentResult]:
    """Ground path-based track requests in a real directory scan before AI planning."""
    path = _extract_user_directory(message)
    context_message = message
    # Follow-ups such as “你自己识别一下” inherit the most recent user path.
    # Paths never need to be sent to the external model: the local scanner is
    # the authoritative tool for deciding whether files are BigWig/BED/etc.
    if path is None and history and any(token in message for token in ("识别", "这个目录", "这个路径", "上面的", "刚才的", "路径下")):
        for item in reversed(history):
            if item.get("role") != "user":
                continue
            previous = str(item.get("content") or "")
            previous_path = _extract_user_directory(previous)
            if previous_path:
                path = previous_path
                context_message = f"{previous} {message}"
                break

    compact = re.sub(r"\s+", "", context_message).lower()
    mentions_track = any(token in compact for token in ("bigwig", "big wig", "track", "轨道", "信号", "bed"))
    asks_add = any(token in compact for token in ("添加", "加入", "加到", "加一些", "放到", "画到", "叠加", "显示"))
    if not mentions_track or not asks_add:
        return None
    current_figure_type = session.current_spec.get("figure_type")
    if not supports_integrated_tracks(current_figure_type):
        item = FIGURE_TYPE_BY_ID.get(str(current_figure_type)) or {}
        return IntentResult(
            "answer",
            f"{item.get('label', current_figure_type)} 使用的 CFIZZ 官方接口没有整合轨道面板；"
            "这不是三角形限制。请先切换到 Hi-C 多组学整合图，或支持整合轨道的 TAD 区域图，再添加轨道。",
            planner="data:directory",
        )
    if path is None:
        return None
    platform_path = service.inspector.platform_path(path).expanduser()
    if platform_path.is_file():
        platform_path = platform_path.parent
    if not platform_path.exists() or not platform_path.is_dir():
        return IntentResult("answer", f"没有找到你提供的目录：{path}。请检查路径是否完整。", planner="data:directory")
    try:
        service.inspector.authorize_root(str(platform_path))
        scan = scan_dataset(str(platform_path), service.inspector, service.references)
        requested_roles = {"signal"} if "bigwig" in compact or "big wig" in compact else {"signal", "intervals"}
        patch = build_track_patch(scan, session.current_spec, service.inspector, requested_roles)
    except (OSError, PermissionError, ValueError) as exc:
        return IntentResult("answer", str(exc), planner="data:directory")
    added = patch.pop("added")
    skipped = patch.pop("skipped_incompatible", [])
    already_present = bool(patch.pop("already_present", False))
    summary = patch.pop("summary", "")
    if already_present:
        return IntentResult(
            "answer",
            summary or "所选轨道已经在当前图中，无需重复添加。",
            planner="data:directory",
        )
    parts = []
    if added["bigwig"]:
        parts.append(f"{added['bigwig']} 条 BigWig")
    if added["bed"]:
        parts.append(f"{added['bed']} 条 BED")
    skipped_text = f"；已跳过 {len(skipped)} 个不包含当前染色体的文件" if skipped else ""
    return IntentResult(
        "patch",
        f"已读取该目录，准备把{'、'.join(parts)}作为 CFIZZ 轨道添加到当前 Hi-C 图下方{skipped_text}。",
        patch,
        planner="data:directory",
    )


def _extract_user_directory(message: str) -> Optional[str]:
    """Extract a quoted/Windows/WSL/POSIX directory without sending it to AI."""
    quoted = re.search(r"[\"'“”]([^\"'“”]+)[\"'“”]", message)
    raw = quoted.group(1) if quoted else None
    if raw is None:
        windows = re.search(r"([A-Za-z]:[\\/][^\r\n，。；,;]+)", message)
        posix = re.search(r"((?:/mnt/[A-Za-z]|/data|/home|/tmp)/[^\r\n，。；,;]+)", message)
        match = windows or posix
        raw = match.group(1) if match else None
    if raw is None:
        return None
    value = raw.strip()
    # Natural-language suffixes are not part of an unquoted path. Prefer the
    # longest prefix that really exists, so directory names may still contain spaces.
    candidate = DataInspector.platform_path(value).expanduser()
    if candidate.exists():
        return value
    suffixes = ("这个路径下", "这个目录下", "路径下", "目录下", "里面", "能读取吗", "可以读取吗", "能读到吗", "可以读到吗")
    for suffix in suffixes:
        if suffix in value:
            shortened = value.split(suffix, 1)[0].strip()
            if DataInspector.platform_path(shortened).expanduser().exists():
                return shortened
    return value


def _resolve_ai_reference_intent(
    service: WorkspaceService,
    session: FigureSession,
    intent: IntentResult,
) -> IntentResult:
    """Resolve an AI semantic decision with authoritative local references."""
    payload = intent.patch or {}
    request = payload.get("reference_request") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        return IntentResult(
            "clarify",
            "AI 判断该请求涉及参考基因，但没有给出可验证的基因请求。请说明要定位、标注单个基因，还是显示当前区域全部基因。",
            planner=f"{intent.planner}:reference-invalid",
        )
    kind = request.get("kind")
    symbol = str(request.get("gene_symbol") or "").strip().upper()
    if kind == "annotate_region_genes":
        resolved = _region_gene_annotation_intent(service, session, "标出当前区域所有基因")
    elif kind == "annotate_gene" and symbol:
        resolved = _gene_annotation_intent(service, session, f"标注 {symbol} 基因")
    elif kind == "locate_gene" and symbol:
        resolved = _reference_gene_intent(service, session, f"定位 {symbol} 基因区域")
    else:
        resolved = None
    if resolved is None:
        return IntentResult(
            "clarify",
            "没有得到完整的基因名称或参考操作，请再说明一次。",
            planner=f"{intent.planner}:reference-invalid",
        )
    # Keep an auditable trace that semantic classification came from AI while
    # coordinates and files were resolved by the selected local reference.
    return IntentResult(
        resolved.action,
        resolved.reply,
        resolved.patch,
        requires_confirmation=resolved.requires_confirmation,
        planner=f"{intent.planner}→{resolved.planner}",
    )


def _session_reference_build(session: FigureSession) -> str:
    reference = session.current_spec.get("metadata", {}).get("reference", {})
    return str(reference.get("id") or reference.get("build") or "hg38")


def _reference_annotation_label(service: WorkspaceService, build: str) -> str:
    reference = service.references._build(build)
    return f"{reference.annotation_release or reference.assembly} / {build}"


def _reference_gene_intent(service: WorkspaceService, session: FigureSession, message: str) -> Optional[IntentResult]:
    """Handle gene-location requests from the authoritative reference registry."""
    # Do not use ``\b`` here: Python treats adjacent Chinese characters as
    # word characters, so ``FOXJ1基因`` would otherwise fail to match.
    match = re.search(r"([A-Za-z][A-Za-z0-9-]{1,19})", message)
    if not match:
        return None
    gene = match.group(1).upper()
    compact = re.sub(r"\s+", "", message).lower()
    # A gene name can also be the subject of a style complaint.  Only claim
    # the request when the user explicitly asks to navigate or draw its region;
    # otherwise let the configured AI understand the whole sentence.
    visual_problem = any(token in compact for token in (
        "重叠", "看不清", "不清楚", "太挤", "挤在", "标签", "字体", "字号", "颜色", "遮住",
    ))
    location_request = any(token in compact for token in (
        "定位", "跳到", "跳转", "切换到", "附近区域", "基因区域", "基因位置", "在哪里",
        "可以画吗", "能画吗", "画这个基因", "画基因", "查看基因", "看基因",
    ))
    if visual_problem or not location_request:
        return None
    build = _session_reference_build(session)
    location = service.references.locate_gene(gene, build)
    if location is None:
        suggestions = service.references.suggest_genes(gene, build)
        suggestion = f"。你是否想输入：{'、'.join(suggestions)}？" if suggestions else "。请检查基因符号或 Ensembl gene ID。"
        return IntentResult(
            "clarify",
            f"项目 {build} 注释中没有找到 {gene}{suggestion}",
            planner=f"reference:{build}",
        )

    sources = {
        source.get("id"): source
        for source in session.current_spec.get("data_sources", [])
        if isinstance(source, dict)
    }
    supported_chrom = None
    for source in sources.values():
        if source.get("type") not in {"cool", "mcool"}:
            continue
        inspection = service.inspector.inspect(source.get("path", ""), source.get("type"))
        supported_chrom = normalize_chromosome(location.chrom, inspection.metadata.get("chromosomes", []))
        if supported_chrom:
            break
    if not supported_chrom:
        return IntentResult(
            "answer",
            f"参考注释中 {location.gene} 位于 {location.chrom}:{location.start:,}-{location.end:,}（{build}），但当前 Hi-C 数据不包含 {location.chrom}，因此不能直接切换。请载入包含 {location.chrom} 的 Hi-C 文件。",
            planner=f"reference:{build}",
        )

    window = 500_000
    start = max(0, location.start - window)
    end = location.end + window
    patch = {
        "summary": f"定位 {gene} 基因区域",
        "operations": [
            {"op": "update", "target_kind": "viewport", "field": "chrom", "value": supported_chrom},
            {"op": "update", "target_kind": "viewport", "field": "start", "value": start},
            {"op": "update", "target_kind": "viewport", "field": "end", "value": end},
            {"op": "update", "target_kind": "viewport", "field": "focus_label", "value": gene},
        ],
    }
    service.pending_actions[session.session_id] = {
        "patch": patch,
        "apply_reply": f"已切换到 {location.gene} 附近区域 {supported_chrom}:{start:,}-{end:,}（{build}）；当前图中的 Hi-C 图层保持不变。",
    }
    return IntentResult(
        "clarify",
        f"可以。项目参考 {location.source} 中 {location.gene} 位于 {location.chrom}:{location.start:,}-{location.end:,}（{build}）。我建议切换到 {supported_chrom}:{start:,}-{end:,}，保留当前 Hi-C 图层；回复“确定”后执行。",
        planner=f"reference:{build}",
    )


def _region_gene_annotation_intent(
    service: WorkspaceService,
    session: FigureSession,
    message: str,
) -> Optional[IntentResult]:
    """Switch a single-gene reference track to all genes in the current view."""
    compact = re.sub(r"\s+", "", message).lower()
    asks_region_genes = (
        any(token in compact for token in ("其他基因", "别的基因", "所有基因", "全部基因", "区域内基因", "这段的基因", "这段上其他"))
        and any(token in compact for token in ("标出", "标注", "显示", "画出", "加上", "添加"))
    )
    if not asks_region_genes:
        return None
    spec = session.current_spec
    current_figure_type = spec.get("figure_type")
    if not supports_integrated_tracks(current_figure_type):
        item = FIGURE_TYPE_BY_ID.get(str(current_figure_type)) or {}
        return IntentResult(
            "answer",
            f"当前是“{item.get('label', current_figure_type)}”：它调用的 CFIZZ 官方接口只渲染矩阵，"
            "不能在同一张图中显示基因注释轨道。规格中即使保存了 GTF/基因层，输出图里也不会出现。"
            "请切换到“双样本三角 Hi-C 对比”或“Hi-C 多组学整合图”后再标注基因。",
            planner="reference:renderer-capability",
        )
    viewport = spec.get("viewport", {})
    build = _session_reference_build(session)
    chrom = str(viewport.get("chrom") or "")
    start = int(viewport.get("start", 0))
    end = int(viewport.get("end", 0))
    try:
        track_path = service.references.region_annotation_track(chrom, start, end, build)
    except ValueError as exc:
        return IntentResult("answer", str(exc), planner=f"reference:{build}")
    # Match CFIZZ examples/integrated/7_3_multi_gene.py: multi-gene GTF
    # height is 0.5 cm per row, capped at seven rows.
    gene_count = service.references.annotation_gene_count(track_path)
    gene_track_height = 0.5 * min(max(1, gene_count), 7)

    gene_layer = next(
        (layer for panel in spec.get("panels", []) for layer in panel.get("layers", []) if layer.get("kind") == "genes"),
        None,
    )
    gene_panel = next(
        (panel for panel in spec.get("panels", []) if any(layer.get("kind") == "genes" for layer in panel.get("layers", []))),
        None,
    )
    operations = []
    if gene_layer is None:
        source_id = f"reference_genes_{build}"
        reference_label = _reference_annotation_label(service, build)
        panel = next((panel for panel in spec.get("panels", []) if panel.get("id") == "annotation_panel"), None)
        operations.append({"op": "add_source", "source": {
            "id": source_id, "type": "gtf", "path": track_path, "label": f"区域基因 · {reference_label}",
        }})
        if panel is None:
            operations.append({"op": "add_panel", "panel": {
                "id": "annotation_panel", "kind": "signal_tracks", "label": "Gene annotation",
                "height_cm": gene_track_height, "layers": [],
            }})
        operations.append({"op": "add_layer", "panel_id": "annotation_panel", "layer": {
            "id": "reference_genes_layer", "kind": "genes", "source_id": source_id,
            "label": "区域内全部基因", "visible": True, "height_cm": gene_track_height,
            "style": {"color": "#666666", "show_title": False, "labels": True, "fontsize": 5,
                      "gtf_style": "flybase", "color_utr": "blue", "border_color": "black",
                      "color_backbone": "black", "line_width": 1.0},
        }})
    else:
        source_id = gene_layer.get("source_id")
        source = next((item for item in spec.get("data_sources", []) if item.get("id") == source_id), None)
        if source is None:
            return IntentResult("answer", "当前基因轨道引用的数据源不存在，无法切换为区域注释。", planner=f"reference:{build}")
        operations.extend([
            {"op": "update", "target_kind": "source", "target_id": source_id, "field": "path", "value": track_path},
            {"op": "update", "target_kind": "source", "target_id": source_id, "field": "label", "value": f"区域基因 · {_reference_annotation_label(service, build)}"},
            {"op": "update", "target_kind": "layer", "target_id": gene_layer["id"], "field": "label", "value": "区域内全部基因"},
            {"op": "update", "target_kind": "layer", "target_id": gene_layer["id"], "field": "height_cm", "value": gene_track_height},
        ])
        if gene_panel is not None and gene_panel.get("height_cm") != gene_track_height:
            operations.append({
                "op": "update", "target_kind": "panel", "target_id": gene_panel["id"],
                "field": "height_cm", "value": gene_track_height,
            })
    return IntentResult(
        "patch",
        f"将使用项目完整 {_reference_annotation_label(service, build)} GTF，标出当前区域 {chrom}:{start:,}-{end:,} 内的全部基因；Hi-C 和信号轨道保持不变。",
        {"summary": "显示当前区域全部基因", "operations": operations},
        planner=f"reference:{build}",
    )


def _gene_annotation_intent(service: WorkspaceService, session: FigureSession, message: str) -> Optional[IntentResult]:
    """Add a bundled/reference GTF track when the current figure lacks one."""
    spec = session.current_spec
    build = _session_reference_build(session)
    reference_label = _reference_annotation_label(service, build)
    # Prefer a GTF already attached to the current figure (for example, one
    # discovered from a user's data directory), then fall back to the bundled
    # annotation available for the selected reference build.
    annotation_path = next(
        (
            source.get("path")
            for source in spec.get("data_sources", [])
            if isinstance(source, dict) and source.get("type") in {"gtf", "gff"} and source.get("path")
        ),
        None,
    )
    lowered_message = message.lower()
    explicit = re.search(r"([A-Za-z][A-Za-z0-9-]{1,19})", message)
    if explicit and explicit.group(1).upper() in {"GENE", "TRACK", "ANNOTATION"}:
        explicit = None
    explicit_location = (
        service.references.locate_gene(
            explicit.group(1).upper(),
            build,
            annotation_path=annotation_path,
        )
        if explicit else None
    )
    has_drawing_verb = any(token in lowered_message for token in (
        "画", "显示", "标出", "标注", "标上", "添加", "加上", "改成", "换成", "切换", "定位", "查看", "看",
    ))
    compact = re.sub(r"\s+", "", message)
    short_gene_command = bool(explicit) and bool(re.fullmatch(
        r"(?:请|帮我|给我)*(?:画|显示|标出|标注|改成|换成|切换到?|定位到?|查看|看)(?:一下)?"
        r"[A-Za-z][A-Za-z0-9-]{1,19}(?:基因)?",
        compact,
    ))
    visual_problem = any(token in lowered_message for token in (
        "重叠", "看不清", "不清楚", "太挤", "挤在", "标签", "字体", "字号", "颜色", "遮住", "截断",
    ))
    if visual_problem:
        return None
    capability_question = any(token in lowered_message for token in ("可以画吗", "能画吗", "能否画", "是否能画"))
    # Known reference symbols are safe to execute directly.  An unknown Latin
    # token is treated as a possible typo only in explicit gene context or a
    # short command such as “画MCY”; this prevents assay/style requests such as
    # “把 ATAC normal 改成绿色” from being mistaken for a gene lookup.
    gene_drawing_command = not visual_problem and not capability_question and bool(explicit) and has_drawing_verb and (
        explicit_location is not None or "基因" in lowered_message or short_gene_command
    )
    if not gene_drawing_command and not any(token in lowered_message for token in ("标注", "标上", "基因注释", "基因轨道", "gene annotation", "gene track")):
        return None
    current_figure_type = spec.get("figure_type")
    if not supports_integrated_tracks(current_figure_type):
        item = FIGURE_TYPE_BY_ID.get(str(current_figure_type)) or {}
        return IntentResult(
            "answer",
            f"当前是“{item.get('label', current_figure_type)}”：它调用的 CFIZZ 官方接口没有基因轨道面板。"
            "因此我不能声称基因已经标在图上。请切换到“双样本三角 Hi-C 对比”或“Hi-C 多组学整合图”，"
            "再添加基因注释轨道。",
            planner="reference:renderer-capability",
        )
    gene = str(spec.get("viewport", {}).get("focus_label") or "").strip().upper()
    if explicit_location is not None:
        gene = explicit_location.gene.upper()
    elif explicit:
        unknown = explicit.group(1).upper()
        suggestions = service.references.suggest_genes(unknown, build)
        suggestion = f"你是不是想输入 {'、'.join(suggestions)}？" if suggestions else "请检查拼写。"
        if annotation_path:
            reply = f"当前 {build} 注释文件中没有找到基因 {unknown}；{suggestion}"
        elif service.references.has_complete_annotation(build):
            reply = f"项目完整 {reference_label} 注释中没有找到基因 {unknown}；{suggestion}"
        else:
            reply = (
                f"当前选择 {reference_label}，但项目未内置该版本的完整基因注释，无法按名称查找 {unknown}。"
                f"请导入匹配 {build} 的 GTF/GFF。"
            )
        return IntentResult(
            "clarify",
            reply,
            planner=f"reference:{build}",
        )
    if not gene:
        return IntentResult("clarify", "请告诉我需要标注哪个基因，例如“标注 FOXJ1”。", planner=f"reference:{build}")
    location = service.references.locate_gene(gene, build, annotation_path=annotation_path)
    if location is None or not location.annotation_path:
        if annotation_path:
            reply = f"当前 {build} 注释文件中没有找到 {gene}；请检查基因符号或 Ensembl gene ID。"
        elif service.references.has_complete_annotation(build):
            reply = f"项目 {reference_label} 参考中没有找到 {gene}；请检查基因符号或 Ensembl gene ID。"
        else:
            reply = f"当前选择 {reference_label}；请先导入匹配 {build} 的 GTF/GFF，才能按基因名标注 {gene}。"
        return IntentResult("answer", reply, planner=f"reference:{build}")

    sources = spec.get("data_sources", [])
    has_gene_track = any(layer.get("kind") == "genes" for panel in spec.get("panels", []) for layer in panel.get("layers", []))
    # A previous version could have added the track while leaving the window
    # on chr1:0-2M.  Treat a repeat annotation request as a request to move
    # that existing track into view, rather than returning a misleading
    # "already present" answer.
    source_id = f"reference_genes_{build}"
    reference_source_label = f"Genes · {reference_label}"
    existing_gene_layer = next(
        (
            layer
            for panel in spec.get("panels", [])
            for layer in panel.get("layers", [])
            if layer.get("kind") == "genes"
        ),
        None,
    )
    layer = {
        "id": "reference_genes_layer", "kind": "genes", "source_id": source_id,
        "label": gene, "visible": True, "height_cm": 0.5,
        "style": {
            "color": "#666666", "show_title": False, "labels": True,
            "fontsize": 5, "gtf_style": "flybase", "color_utr": "blue",
            "border_color": "black", "color_backbone": "black", "line_width": 1.0,
        },
    }
    annotation_panel = next((panel for panel in spec.get("panels", []) if panel.get("kind") == "signal_tracks" and panel.get("id") == "annotation_panel"), None)
    operations = []
    current_viewport = spec.get("viewport", {})
    target_start = int(current_viewport.get("start", 0))
    target_end = int(current_viewport.get("end", 0))
    operations.append({"op": "update", "target_kind": "viewport", "field": "focus_label", "value": gene})
    if not _region_overlaps(current_viewport, location.chrom, location.start, location.end):
        supported_chrom = None
        for source in spec.get("data_sources", []):
            if not isinstance(source, dict) or source.get("type") not in {"cool", "mcool"}:
                continue
            inspection = service.inspector.inspect(source.get("path", ""), source.get("type"))
            supported_chrom = normalize_chromosome(location.chrom, inspection.metadata.get("chromosomes", []))
            if supported_chrom:
                break
        if not supported_chrom:
            return IntentResult(
                "answer",
                f"{gene} 位于 {location.chrom}:{location.start:,}-{location.end:,}（{location.assembly}），但当前 Hi-C 文件不包含 {location.chrom}，无法在当前数据上标注。",
                planner=f"reference:{build}",
            )
        flank = 500_000
        chrom_sizes = inspection.metadata.get("chromsizes", {})
        start = max(0, location.start - flank)
        end = location.end + flank
        if supported_chrom in chrom_sizes:
            end = min(end, int(chrom_sizes[supported_chrom]))
        target_start, target_end = start, end
        operations.extend([
            {"op": "update", "target_kind": "viewport", "field": "chrom", "value": supported_chrom},
            {"op": "update", "target_kind": "viewport", "field": "start", "value": start},
            {"op": "update", "target_kind": "viewport", "field": "end", "value": end},
        ])
    try:
        track_path = service.references.annotation_track(location, target_start, target_end)
    except ValueError as exc:
        return IntentResult("answer", str(exc), planner=f"reference:{build}")
    if not has_gene_track:
        operations.extend([
            {"op": "add_source", "source": {"id": source_id, "type": "gtf", "path": track_path, "label": reference_source_label}},
        ])
        if annotation_panel is None:
            operations.append({"op": "add_panel", "panel": {"id": "annotation_panel", "kind": "signal_tracks", "label": "Gene annotation", "height_cm": 0.5, "layers": []}})
        operations.append({"op": "add_layer", "panel_id": "annotation_panel", "layer": layer})
    elif existing_gene_layer is not None:
        existing_source_id = existing_gene_layer.get("source_id")
        existing_source = next((source for source in sources if source.get("id") == existing_source_id), None)
        # Region annotations deliberately reserve up to 3.5 cm for several
        # packed gene rows.  Do not carry that layout default into a later
        # single-gene request: CFIZZ's official single-gene example uses a
        # compact 0.5 cm track.  Only reset on this mode transition so a
        # user's explicit single-gene height adjustment survives when they
        # switch from one individual gene to another.
        existing_path = str(existing_source.get("path", "")).replace("\\", "/") if existing_source else ""
        switching_from_region = (
            existing_gene_layer.get("label") == "区域内全部基因"
            or "/regions/" in existing_path
            or str(existing_source.get("label", "")).startswith("区域基因") if existing_source else False
        )
        if existing_source and existing_source.get("type") in {"gtf", "gff"} and str(existing_source.get("path")) != str(track_path):
            operations.append({"op": "update", "target_kind": "source", "target_id": existing_source_id, "field": "path", "value": track_path})
        if existing_source and existing_source.get("label") != reference_source_label:
            operations.append({"op": "update", "target_kind": "source", "target_id": existing_source_id, "field": "label", "value": reference_source_label})
        if existing_gene_layer.get("label") != gene:
            operations.append({"op": "update", "target_kind": "layer", "target_id": existing_gene_layer.get("id"), "field": "label", "value": gene})
        if switching_from_region:
            operations.append({"op": "update", "target_kind": "layer", "target_id": existing_gene_layer.get("id"), "field": "height_cm", "value": 0.5})
            if annotation_panel is not None:
                operations.append({"op": "update", "target_kind": "panel", "target_id": annotation_panel.get("id"), "field": "height_cm", "value": 0.5})
    moved = not _region_overlaps(current_viewport, location.chrom, location.start, location.end)
    if moved:
        if has_gene_track:
            reply = f"已切换到 {location.chrom}:{max(0, location.start - 500_000):,}-{location.end + 500_000:,}，并更新为 {gene} 基因注释轨道。"
        else:
            reply = f"已切换到 {location.chrom}:{max(0, location.start - 500_000):,}-{location.end + 500_000:,}，并添加 {gene} 的 {location.assembly} 基因注释轨道；原 Hi-C 图层保留。"
    else:
        reply = f"将添加 {gene} 的 {location.assembly} 基因注释轨道，并保留当前 Hi-C 图层。"
    if any(token in message for token in ("还有其他", "还有别的", "其他可以标注", "别的可以标注")):
        gene_count = service.references.complete_gene_count(build)
        if gene_count:
            reply += f" 项目公共 {reference_label} 参考包含 {gene_count:,} 条基因记录，可继续输入其他基因符号或 Ensembl gene ID。"
        elif annotation_path:
            reply += f" 当前使用已导入的 {build} 注释；可继续输入该 GTF/GFF 中的基因符号或 gene ID。"
    return IntentResult(
        "patch",
        reply,
        {"summary": f"添加 {gene} 基因注释", "operations": operations},
        planner=f"reference:{build}",
    )


def _annotation_question_intent(
    service: WorkspaceService,
    session: FigureSession,
    message: str,
) -> Optional[IntentResult]:
    """Answer annotation capability questions without accidentally editing the figure."""
    compact = re.sub(r"\s+", "", message).lower()
    asks_other = any(token in compact for token in (
        "还有其他", "还有别的", "其他可以", "别的可以", "还能标注", "可以标注哪些", "可以标注什么",
    ))
    asks_beyond_genes = any(token in compact for token in (
        "除了基因", "除基因", "其他轨道", "别的轨道", "还有什么轨道", "支持哪些轨道",
    ))
    if not (asks_other or asks_beyond_genes):
        return None

    # A compound imperative such as “添加基因轨道，标注 FOXJ1 吧，还有其他吗”
    # should still execute the named edit; the mutation reply will also list
    # the other available genes.
    explicit = re.search(r"([A-Za-z][A-Za-z0-9-]{1,19})", message)
    explicit_gene = explicit.group(1).upper() if explicit else None
    begins_with_action = compact.startswith(("添加", "标注", "画", "显示", "加入"))
    if explicit_gene and begins_with_action:
        return None

    spec = session.current_spec
    build = _session_reference_build(session)
    if asks_beyond_genes or "轨道" in compact:
        layer_counts: Dict[str, int] = {}
        for panel in spec.get("panels", []):
            for layer in panel.get("layers", []):
                if layer.get("visible", True):
                    kind = str(layer.get("kind") or "unknown")
                    layer_counts[kind] = layer_counts.get(kind, 0) + 1
        current = []
        for kind, label in (("hic", "Hi-C"), ("genes", "基因"), ("bigwig", "连续信号"), ("intervals", "区间"), ("loops", "Loop"), ("compartment", "Compartment")):
            if layer_counts.get(kind):
                current.append(f"{label}×{layer_counts[kind]}")
        inventory = spec.get("metadata", {}).get("dataset_inventory", {})
        detected = []
        for role, label in (("signal", "BigWig"), ("intervals", "BED 区间"), ("loops", "Loop"), ("compartment", "E1/Compartment")):
            if inventory.get(role):
                detected.append(f"{label}×{inventory[role]}")
        detected_text = f" 数据目录还识别到：{'、'.join(detected)}。" if detected else ""
        return IntentResult(
            "answer",
            f"不只基因注释。当前图已加载：{'、'.join(current) if current else '尚无可见轨道'}。"
            "CFIZZ 还可以叠加 BigWig 连续信号（ATAC、RNA、ChIP/CUT&Tag 等）、BED 区间/增强子、"
            "BEDPE/TSV Loop、TAD/Insulation 和 E1/A/B Compartment；每一类都必须有对应数据，不能从 Hi-C 热图凭空生成。"
            f"{detected_text}",
            planner="reference:capabilities",
        )

    user_annotation = next(
        (
            source
            for source in spec.get("data_sources", [])
            if isinstance(source, dict) and source.get("type") in {"gtf", "gff"} and source.get("path")
        ),
        None,
    )
    if service.references.has_complete_annotation(build):
        reference = service.references._build(build)
        return IntentResult(
            "answer",
            f"可以。项目已配置 {reference.annotation} 完整注释，共 {service.references.complete_gene_count(build):,} 条基因记录。"
            "直接输入基因符号（如 TP53、MYC）或 Ensembl gene ID，Agent 会定位区域并绘制基因轨道，无需再提供 GTF。",
            planner="reference:capabilities",
        )
    if user_annotation:
        return IntentResult(
            "answer",
            f"当前选择 {build}，并已载入 {user_annotation.get('label') or '用户 GTF/GFF'}。"
            "可以定位和标注该注释文件中存在的基因；请确保文件版本与数据一致。",
            planner="reference:capabilities",
        )
    genes = service.references.bundled_gene_names(build)
    if genes:
        return IntentResult("answer", f"当前可直接标注：{'、'.join(genes)}。", planner="reference:capabilities")
    return IntentResult(
        "answer",
        f"当前选择 {build}；坐标区域绘图可用，但项目未内置该版本的完整基因注释。"
        f"请导入匹配 {build} 的 GTF/GFF 后，再使用基因名定位或标注。",
        planner="reference:capabilities",
    )


def _region_overlaps(viewport: Dict[str, Any], chrom: str, start: int, end: int) -> bool:
    current_chrom = str(viewport.get("chrom", ""))
    if current_chrom.lower().removeprefix("chr") != chrom.lower().removeprefix("chr"):
        return False
    return int(viewport.get("start", 0)) < end and int(viewport.get("end", 0)) > start


def _confirmation_text(message: str) -> bool:
    compact = re.sub(r"\s+", "", message).lower()
    if compact in {
        "确定", "确认", "确定一下", "确认一下", "我确认", "我确定", "可以", "好的", "好",
        "是", "是的", "没问题", "开始吧", "就这样", "ok", "yes", "继续", "执行",
        "a", "选择a", "选a",
    }:
        return True
    return bool(re.fullmatch(r"(?:确定|确认)(?:一下|执行|生成|绘图|画图|开始)?", compact))


def _cancellation_text(message: str) -> bool:
    compact = re.sub(r"\s+", "", str(message or "")).lower()
    if compact in {"取消", "不用", "不要", "否", "不", "放弃", "先不画", "不画", "取消生成", "取消执行", "no", "cancel"}:
        return True
    return bool(re.fullmatch(r"(?:取消|不要|不用)(?:生成|执行|画图)?", compact))


def _blank_chat_spec(session_id: str) -> Dict[str, Any]:
    """Return a valid, non-renderable FigureSpec for a fresh chat.

    Keeping this as a FigureSpec (rather than using a separate UI-only state)
    means the existing chat/history APIs can be used immediately.  The draft
    marker is removed when a data-backed workflow is confirmed.
    """
    return {
        "schema_version": "0.1",
        "figure_type": "hic_triangle",
        "figure_id": f"{session_id}_figure",
        "title": "尚未载入图形",
        "data_sources": [],
        "viewport": {
            "chrom": "chr1",
            "start": 0,
            "end": 2_000_000,
            "coordinate_system": "0-based-half-open",
        },
        "analysis": {
            "resolution": "auto",
            "balance": True,
            "normalization": "raw",
            "shared_color_scale": True,
        },
        "panels": [{
            "id": "draft_panel",
            "kind": "hic_heatmap",
            "label": "Hi-C",
            "height_cm": "auto",
            "layers": [],
        }],
        "layout": {
            "width_cm": 12,
            "gap_cm": 0.15,
            "left_margin_cm": 1.5,
            "right_margin_cm": 2,
            "font_size": 5,
        },
        "export": {
            "formats": ["svg", "png", "pdf"],
            "dpi": 300,
            "output_basename": f"{session_id}_figure",
        },
        "metadata": {"draft": True},
    }


def _single_hic_spec(
    session_id: str,
    title: Optional[str],
    hic_path: str,
    data_type: str,
    chrom: str,
    start: int,
    end: int,
    resolution: int,
    figure_type: str = "hic_triangle",
    reference: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    labels = {
        "hic_triangle": "Hi-C triangle heatmap",
        "hic_square": "Hi-C square heatmap",
        "hic_oe": "Hi-C O/E heatmap",
        "tad_insulation": "TAD / Insulation",
        "tad_insulation_track": "insulation score track",
        "tad_boundary_square": "square Hi-C + TAD boundaries",
        "compartment": "A/B compartment",
        "loop_heatmap": "loop-annotated Hi-C heatmap",
        "loop_apa": "loop APA",
        "compartment_eigenvector": "E1 eigenvector track",
        "compartment_saddle": "compartment saddle",
        "tad_boundary_pileup": "TAD boundary pileup",
    }
    return {
        "schema_version": "0.1",
        "figure_type": figure_type,
        "figure_id": f"{session_id}_figure",
        "title": title or f"{Path(hic_path).stem} {labels[figure_type]}",
        "data_sources": [{"id": "hic_main", "type": data_type, "path": hic_path, "label": "Hi-C"}],
        "viewport": {
            "chrom": chrom,
            "start": start,
            "end": end,
            "coordinate_system": "0-based-half-open",
        },
        "analysis": {"resolution": resolution, "balance": True, "normalization": "raw", "shared_color_scale": True},
        "panels": [{
            "id": "hic_panel",
            "kind": "hic_heatmap",
            "label": "Hi-C",
            "height_cm": "auto",
            "layers": [{
                "id": "hic_main_layer",
                "kind": "hic",
                "source_id": "hic_main",
                "label": "Hi-C",
                "visible": True,
                "style": {"cmap": "Reds", "triangle_ratio": 0.5, "flip_vertical": False},
            }],
        }],
        "layout": {"width_cm": 12, "gap_cm": 0.15, "left_margin_cm": 1.5, "right_margin_cm": 2},
        "export": {"formats": ["svg", "png", "pdf"], "dpi": 300, "output_basename": f"{session_id}_figure"},
        "metadata": {"reference": reference or {"id": "hg38", "assembly": "GRCh38"}},
    }


app = create_app()
