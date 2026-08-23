"""Versioned FigureSpec sessions with undo, redo, and atomic persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Mapping, Optional

from .figure_spec import FigureSpecValidator
from .patching import FigurePatchEngine, PatchResult


@dataclass(frozen=True)
class FigureRevision:
    version_id: str
    parent_version_id: Optional[str]
    created_at: str
    summary: str
    impact: str
    spec: Dict[str, Any]
    patch: Optional[Dict[str, Any]] = None
    changes: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FigureSession:
    """Hold an immutable revision history for one conversational figure."""

    def __init__(
        self,
        session_id: str,
        initial_spec: Mapping[str, Any],
        validator: Optional[FigureSpecValidator] = None,
        patch_engine: Optional[FigurePatchEngine] = None,
    ):
        self.session_id = self._validate_id(session_id)
        self.validator = validator or FigureSpecValidator()
        self.patch_engine = patch_engine or FigurePatchEngine(self.validator)
        validation = self.validator.validate(initial_spec, inspect_files=False)
        if not validation.valid:
            messages = "; ".join(issue.message for issue in validation.errors)
            raise ValueError(f"初始 FigureSpec 无效：{messages}")
        self._revisions = [
            FigureRevision(
                version_id="v0001",
                parent_version_id=None,
                created_at=self._now(),
                summary="创建图形",
                impact="data_reload",
                spec=deepcopy(validation.resolved_spec),
            )
        ]
        self._cursor = 0

    @property
    def current(self) -> FigureRevision:
        return self._revisions[self._cursor]

    @property
    def current_spec(self) -> Dict[str, Any]:
        return deepcopy(self.current.spec)

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor < len(self._revisions) - 1

    def apply_patch(self, patch: Mapping[str, Any]) -> FigureRevision:
        result = self.patch_engine.apply(self.current.spec, patch)
        if self.can_redo:
            self._revisions = self._revisions[: self._cursor + 1]
        revision = self._revision_from_result(result, dict(patch))
        self._revisions.append(revision)
        self._cursor = len(self._revisions) - 1
        return revision

    def replace_spec(self, spec: Mapping[str, Any], summary: str = "重新绑定工作流数据") -> FigureRevision:
        """Append a validated full-spec revision.

        Dataset-backed workflows may choose files that were not attached to
        the figure that happened to be on screen.  Replacing the current spec
        as a normal revision keeps undo/history semantics while allowing the
        workflow to use exactly the files selected in its input chooser.
        """
        validation = self.validator.validate(spec, inspect_files=False)
        if not validation.valid:
            messages = "; ".join(issue.message for issue in validation.errors)
            raise ValueError(f"新的 FigureSpec 无效：{messages}")
        if self.can_redo:
            self._revisions = self._revisions[: self._cursor + 1]
        next_number = int(self._revisions[-1].version_id[1:]) + 1
        revision = FigureRevision(
            version_id=f"v{next_number:04d}",
            parent_version_id=self.current.version_id,
            created_at=self._now(),
            summary=summary,
            impact="data_reload",
            spec=deepcopy(validation.resolved_spec),
        )
        self._revisions.append(revision)
        self._cursor = len(self._revisions) - 1
        return revision

    def undo(self) -> FigureRevision:
        if not self.can_undo:
            raise ValueError("已经是最早版本，无法继续撤销。")
        self._cursor -= 1
        return self.current

    def redo(self) -> FigureRevision:
        if not self.can_redo:
            raise ValueError("没有可以重做的版本。")
        self._cursor += 1
        return self.current

    def restore(self, version_id: str) -> FigureRevision:
        for index, revision in enumerate(self._revisions):
            if revision.version_id == version_id:
                self._cursor = index
                return revision
        raise ValueError(f"找不到版本 {version_id!r}。")

    def history(self) -> List[Dict[str, Any]]:
        return [
            {
                "version_id": revision.version_id,
                "parent_version_id": revision.parent_version_id,
                "created_at": revision.created_at,
                "summary": revision.summary,
                "impact": revision.impact,
                "current": index == self._cursor,
            }
            for index, revision in enumerate(self._revisions)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cursor": self._cursor,
            "revisions": [revision.to_dict() for revision in self._revisions],
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        validator: Optional[FigureSpecValidator] = None,
        patch_engine: Optional[FigurePatchEngine] = None,
    ) -> "FigureSession":
        revisions_data = data.get("revisions")
        if not isinstance(revisions_data, list) or not revisions_data:
            raise ValueError("会话文件中没有有效版本。")
        instance = cls(
            str(data.get("session_id")),
            revisions_data[0]["spec"],
            validator=validator,
            patch_engine=patch_engine,
        )
        revisions = []
        for item in revisions_data:
            validation = instance.validator.validate(item.get("spec", {}), inspect_files=False)
            if not validation.valid:
                raise ValueError(f"会话版本 {item.get('version_id')!r} 中的 FigureSpec 无效。")
            revision_data = deepcopy(item)
            revision_data["spec"] = validation.resolved_spec
            revisions.append(FigureRevision(**revision_data))
        instance._revisions = revisions
        cursor = data.get("cursor", len(instance._revisions) - 1)
        if not isinstance(cursor, int) or not 0 <= cursor < len(instance._revisions):
            raise ValueError("会话 cursor 无效。")
        instance._cursor = cursor
        return instance

    def _revision_from_result(self, result: PatchResult, patch: Dict[str, Any]) -> FigureRevision:
        next_number = int(self._revisions[-1].version_id[1:]) + 1
        return FigureRevision(
            version_id=f"v{next_number:04d}",
            parent_version_id=self.current.version_id,
            created_at=self._now(),
            summary=result.summary,
            impact=result.impact,
            spec=deepcopy(result.spec),
            patch=deepcopy(patch),
            changes=[change.to_dict() for change in result.changes],
        )

    @staticmethod
    def _validate_id(value: str) -> str:
        if not value or any(not (char.isalnum() or char in {"-", "_"}) for char in value):
            raise ValueError("session_id 只能包含字母、数字、连字符和下划线。")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


class FileFigureSessionStore:
    """Persist sessions as atomic JSON snapshots under a server-controlled root."""

    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve()

    def save(self, session: FigureSession) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{session.session_id}.json"
        payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{session.session_id}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def load(
        self,
        session_id: str,
        validator: Optional[FigureSpecValidator] = None,
        patch_engine: Optional[FigurePatchEngine] = None,
    ) -> FigureSession:
        safe_id = FigureSession._validate_id(session_id)
        path = self.root / f"{safe_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"找不到会话 {safe_id!r}。")
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return FigureSession.from_dict(data, validator=validator, patch_engine=patch_engine)
