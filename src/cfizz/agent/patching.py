"""Transactional, ID-addressed edits for conversational FigureSpec updates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .figure_spec import FigureSpecValidator


_IMPACT_ORDER = {
    "render_only": 0,
    "data_reload": 1,
    "scientific_recompute": 2,
}

_UPDATABLE_FIELDS = {
    "figure": {"title", "intent_summary", "figure_type", "workflow_source_ids", "workflow_options"},
    "viewport": {"chrom", "start", "end", "focus_label"},
    "analysis": {"resolution", "balance", "normalization", "shared_color_scale"},
    "layout": {"width_cm", "gap_cm", "left_margin_cm", "right_margin_cm", "font_size"},
    "export": {"formats", "dpi", "output_basename"},
    "source": {"path", "sample", "label"},
    "panel": {"label", "height_cm"},
    "layer": {"label", "visible", "height_cm", "style"},
}

_CMAP_ALIASES = {
    # Matplotlib/CFIZZ use ``plasma`` for the requested purple↔yellow
    # gradient.  Keep common spellings (including the invalid ``YlPur``
    # token sometimes emitted by an LLM) out of the renderer.
    "purple_yellow": "plasma",
    "purple-yellow": "plasma",
    "purple yellow": "plasma",
    "purple to yellow": "plasma",
    "purple-to-yellow": "plasma",
    "ylpur": "plasma",
    "yl-pur": "plasma",
    "yellow_purple": "plasma",
    "yellow-purple": "plasma",
    "yellow purple": "plasma",
    "yellow to purple": "plasma",
    "yellow-to-purple": "plasma",
    "紫黄": "plasma",
    "紫色到黄色": "plasma",
    "黄色到紫色": "plasma",
}


class FigurePatchError(ValueError):
    """Raised when a patch is unsafe, malformed, or makes the spec invalid."""


@dataclass(frozen=True)
class AppliedChange:
    operation: str
    path: str
    old_value: Any = None
    new_value: Any = None
    impact: str = "render_only"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "path": self.path,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "impact": self.impact,
        }


@dataclass
class PatchResult:
    spec: Dict[str, Any]
    changes: List[AppliedChange] = field(default_factory=list)
    summary: str = ""

    @property
    def impact(self) -> str:
        if not self.changes:
            return "render_only"
        return max(self.changes, key=lambda item: _IMPACT_ORDER[item.impact]).impact

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec,
            "changes": [change.to_dict() for change in self.changes],
            "summary": self.summary,
            "impact": self.impact,
        }


class FigurePatchEngine:
    """Apply a semantic patch atomically and validate the resulting FigureSpec."""

    def __init__(self, validator: Optional[FigureSpecValidator] = None):
        self.validator = validator or FigureSpecValidator()

    def apply(self, spec: Mapping[str, Any], patch: Mapping[str, Any]) -> PatchResult:
        operations = patch.get("operations")
        if not isinstance(operations, list) or not operations:
            raise FigurePatchError("FigurePatch 至少需要一个 operation。")

        candidate = deepcopy(dict(spec))
        changes: List[AppliedChange] = []
        for index, operation in enumerate(operations):
            if not isinstance(operation, Mapping):
                raise FigurePatchError(f"operations[{index}] 必须是对象。")
            changes.extend(self._apply_operation(candidate, operation, index))

        validation = self.validator.validate(candidate, inspect_files=False)
        if not validation.valid:
            details = "; ".join(f"{issue.path}: {issue.message}" for issue in validation.errors)
            raise FigurePatchError(f"修改后的 FigureSpec 无效，未保存任何修改：{details}")

        return PatchResult(
            spec=validation.resolved_spec,
            changes=changes,
            summary=str(patch.get("summary") or self._default_summary(changes)),
        )

    def _apply_operation(
        self,
        spec: Dict[str, Any],
        operation: Mapping[str, Any],
        index: int,
    ) -> List[AppliedChange]:
        op = operation.get("op")
        if op == "update":
            return [self._update(spec, operation, index)]
        if op == "add_source":
            return [self._add_source(spec, operation, index)]
        if op == "add_panel":
            return [self._add_panel(spec, operation, index)]
        if op == "remove_source":
            return self._remove_source(spec, operation, index)
        if op == "add_layer":
            return [self._add_layer(spec, operation, index)]
        if op == "remove_layer":
            return [self._remove_layer(spec, operation, index)]
        if op == "move_layer":
            return [self._move_layer(spec, operation, index)]
        raise FigurePatchError(f"operations[{index}].op 不受支持：{op!r}。")

    def _update(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        target_kind = operation.get("target_kind")
        target_id = operation.get("target_id")
        field_path = operation.get("field")
        if target_kind not in _UPDATABLE_FIELDS:
            raise FigurePatchError(f"operations[{index}].target_kind 不受支持。")
        if not isinstance(field_path, str) or not field_path:
            raise FigurePatchError(f"operations[{index}].field 不能为空。")
        if "value" not in operation:
            raise FigurePatchError(f"operations[{index}].value 不能为空。")

        parts = field_path.split(".")
        if any(not part or part.startswith("_") for part in parts):
            raise FigurePatchError(f"operations[{index}].field 包含非法字段。")
        if parts[0] not in _UPDATABLE_FIELDS[target_kind]:
            raise FigurePatchError(f"不允许修改 {target_kind}.{field_path}。")
        if len(parts) > 1:
            nested_root = {
                "layer": "style",
                "figure": "workflow_options",
            }.get(target_kind)
            if parts[0] != nested_root:
                raise FigurePatchError("只允许 layer.style 或 figure.workflow_options 的嵌套字段更新。")

        target, target_path = self._find_target(spec, target_kind, target_id)
        parent = target
        for part in parts[:-1]:
            value = parent.get(part)
            if value is None:
                value = {}
                parent[part] = value
            if not isinstance(value, dict):
                raise FigurePatchError(f"字段 {target_path}.{part} 不是对象，无法继续更新。")
            parent = value

        field_name = parts[-1]
        old_value = deepcopy(parent.get(field_name))
        new_value = deepcopy(operation.get("value"))
        if target_kind == "layer" and field_path == "style.cmap" and isinstance(new_value, str):
            new_value = _CMAP_ALIASES.get(new_value.strip().lower(), new_value)
        parent[field_name] = new_value
        full_path = f"{target_path}.{field_path}"
        return AppliedChange(
            "update", full_path, old_value, new_value,
            self._impact_for_update(target_kind, field_path, new_value),
        )

    def _add_source(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        source = deepcopy(operation.get("source"))
        if not isinstance(source, dict):
            raise FigurePatchError(f"operations[{index}].source 必须是对象。")
        source_id = source.get("id")
        if not source_id:
            raise FigurePatchError(f"operations[{index}].source.id 不能为空。")
        if any(item.get("id") == source_id for item in spec.get("data_sources", [])):
            raise FigurePatchError(f"数据源 ID {source_id!r} 已存在。")
        spec.setdefault("data_sources", []).append(source)
        return AppliedChange("add_source", f"$.data_sources[id={source_id}]", None, source, "data_reload")

    def _remove_source(
        self,
        spec: Dict[str, Any],
        operation: Mapping[str, Any],
        index: int,
    ) -> List[AppliedChange]:
        source_id = operation.get("source_id")
        cascade = operation.get("cascade", False)
        sources = spec.get("data_sources", [])
        source_index = next((i for i, source in enumerate(sources) if source.get("id") == source_id), None)
        if source_index is None:
            raise FigurePatchError(f"找不到数据源 {source_id!r}。")

        references: List[Tuple[Dict[str, Any], int, Dict[str, Any], str]] = []
        for panel in spec.get("panels", []):
            for layer_index, layer in enumerate(panel.get("layers", [])):
                if layer.get("source_id") == source_id:
                    references.append((panel, layer_index, layer, f"$.panels[id={panel.get('id')}].layers[id={layer.get('id')}]"))
        if references and not cascade:
            layer_ids = ", ".join(str(item[2].get("id")) for item in references)
            raise FigurePatchError(f"数据源仍被图层引用：{layer_ids}。如需一起删除，请设置 cascade=true。")

        changes = []
        for panel in spec.get("panels", []):
            kept = []
            for layer in panel.get("layers", []):
                if layer.get("source_id") == source_id:
                    changes.append(
                        AppliedChange(
                            "remove_layer",
                            f"$.panels[id={panel.get('id')}].layers[id={layer.get('id')}]",
                            deepcopy(layer),
                            None,
                            "data_reload",
                        )
                    )
                else:
                    kept.append(layer)
            panel["layers"] = kept

        removed = sources.pop(source_index)
        changes.append(AppliedChange("remove_source", f"$.data_sources[id={source_id}]", removed, None, "data_reload"))
        return changes

    def _add_layer(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        panel_id = operation.get("panel_id")
        layer = deepcopy(operation.get("layer"))
        if not isinstance(layer, dict):
            raise FigurePatchError(f"operations[{index}].layer 必须是对象。")
        layer_id = layer.get("id")
        if not layer_id:
            raise FigurePatchError(f"operations[{index}].layer.id 不能为空。")
        if self._id_exists(spec, layer_id):
            raise FigurePatchError(f"ID {layer_id!r} 已存在。")
        panel, panel_path = self._find_target(spec, "panel", panel_id)
        layers = panel.setdefault("layers", [])
        before_layer_id = operation.get("before_layer_id")
        if before_layer_id is None:
            layers.append(layer)
        else:
            insert_at = next((i for i, item in enumerate(layers) if item.get("id") == before_layer_id), None)
            if insert_at is None:
                raise FigurePatchError(f"找不到 before_layer_id {before_layer_id!r}。")
            layers.insert(insert_at, layer)
        return AppliedChange("add_layer", f"{panel_path}.layers[id={layer_id}]", None, layer, "data_reload")

    def _add_panel(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        panel = deepcopy(operation.get("panel"))
        if not isinstance(panel, dict):
            raise FigurePatchError(f"operations[{index}].panel 必须是对象。")
        panel_id = panel.get("id")
        if not panel_id:
            raise FigurePatchError(f"operations[{index}].panel.id 不能为空。")
        if self._id_exists(spec, panel_id):
            raise FigurePatchError(f"ID {panel_id!r} 已存在。")
        if panel.get("kind") not in {"hic_heatmap", "compartment", "signal_tracks", "saddle", "tad_pileup", "loop_apa"}:
            raise FigurePatchError(f"operations[{index}].panel.kind 不受支持。")
        if not isinstance(panel.get("layers", []), list):
            raise FigurePatchError(f"operations[{index}].panel.layers 必须是数组。")
        spec.setdefault("panels", []).append(panel)
        return AppliedChange("add_panel", f"$.panels[id={panel_id}]", None, panel, "data_reload")

    def _remove_layer(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        layer_id = operation.get("layer_id")
        panel, layer_index, layer = self._find_layer(spec, layer_id)
        removed = panel["layers"].pop(layer_index)
        return AppliedChange(
            "remove_layer",
            f"$.panels[id={panel.get('id')}].layers[id={layer_id}]",
            removed,
            None,
            "data_reload",
        )

    def _move_layer(self, spec: Dict[str, Any], operation: Mapping[str, Any], index: int) -> AppliedChange:
        layer_id = operation.get("layer_id")
        destination_id = operation.get("panel_id")
        source_panel, source_index, layer = self._find_layer(spec, layer_id)
        # A reorder request normally stays in the layer's current panel.  The
        # panel ID remains optional so an AI capability can safely emit only a
        # stable layer ID and an optional ``before_layer_id`` reference.
        destination = source_panel if destination_id is None else self._find_target(spec, "panel", destination_id)[0]
        source_panel["layers"].pop(source_index)
        destination_layers = destination.setdefault("layers", [])
        before_layer_id = operation.get("before_layer_id")
        if before_layer_id is None:
            destination_layers.append(layer)
        else:
            insert_at = next((i for i, item in enumerate(destination_layers) if item.get("id") == before_layer_id), None)
            if insert_at is None:
                raise FigurePatchError(f"找不到 before_layer_id {before_layer_id!r}。")
            destination_layers.insert(insert_at, layer)
        old_path = f"$.panels[id={source_panel.get('id')}].layers[id={layer_id}]"
        new_path = f"$.panels[id={destination_id}].layers[id={layer_id}]"
        return AppliedChange("move_layer", new_path, old_path, new_path, "render_only")

    @staticmethod
    def _find_target(spec: Dict[str, Any], target_kind: str, target_id: Optional[str]) -> Tuple[Dict[str, Any], str]:
        if target_kind == "figure":
            return spec, "$"
        if target_kind in {"viewport", "analysis", "layout", "export"}:
            return spec.setdefault(target_kind, {}), f"$.{target_kind}"
        collection = "data_sources" if target_kind == "source" else "panels"
        if target_kind in {"source", "panel"}:
            for item in spec.get(collection, []):
                if item.get("id") == target_id:
                    return item, f"$.{collection}[id={target_id}]"
            raise FigurePatchError(f"找不到 {target_kind} {target_id!r}。")
        if target_kind == "layer":
            panel, _, layer = FigurePatchEngine._find_layer(spec, target_id)
            return layer, f"$.panels[id={panel.get('id')}].layers[id={target_id}]"
        raise FigurePatchError(f"不支持 target_kind {target_kind!r}。")

    @staticmethod
    def _find_layer(spec: Dict[str, Any], layer_id: Optional[str]) -> Tuple[Dict[str, Any], int, Dict[str, Any]]:
        for panel in spec.get("panels", []):
            for index, layer in enumerate(panel.get("layers", [])):
                if layer.get("id") == layer_id:
                    return panel, index, layer
        raise FigurePatchError(f"找不到 layer {layer_id!r}。")

    @staticmethod
    def _id_exists(spec: Dict[str, Any], candidate: str) -> bool:
        ids = {spec.get("figure_id")}
        ids.update(source.get("id") for source in spec.get("data_sources", []))
        for panel in spec.get("panels", []):
            ids.add(panel.get("id"))
            ids.update(layer.get("id") for layer in panel.get("layers", []))
        return candidate in ids

    @staticmethod
    def _impact_for_update(target_kind: str, field_path: str, value: Any = None) -> str:
        if target_kind == "analysis" and field_path in {"resolution", "balance", "normalization"}:
            return "scientific_recompute"
        if target_kind == "figure" and field_path == "workflow_options":
            # A workflow option patch can arrive as one dictionary from the
            # workflow picker.  Inspect its keys so calculation-changing
            # options still require the same explicit confirmation as an
            # analysis.resolution edit.
            scientific_keys = {
                "window_size", "window", "corner_size", "min_distance",
                "flank", "n_bins", "contact_type", "method", "top_n",
            }
            if isinstance(value, dict) and scientific_keys.intersection(value):
                return "scientific_recompute"
            return "render_only"
        if target_kind == "figure" and field_path.startswith("workflow_options."):
            key = field_path.split(".", 2)[-1]
            if key in {
                "window_size", "window", "corner_size", "min_distance",
                "flank", "n_bins", "contact_type", "method", "top_n",
            }:
                return "scientific_recompute"
        if target_kind in {"viewport", "source"}:
            return "data_reload"
        if target_kind == "layer" and (
            field_path == "visible"
            or field_path.startswith("style.insulation_")
            or field_path.startswith("style.loops_")
        ):
            return "data_reload"
        return "render_only"

    @staticmethod
    def _default_summary(changes: List[AppliedChange]) -> str:
        if len(changes) == 1:
            return f"{changes[0].operation}: {changes[0].path}"
        return f"应用 {len(changes)} 项图形修改"
