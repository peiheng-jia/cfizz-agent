"""Declarative plotting capabilities and safe target resolution.

The language model selects a capability and a target selector. This module is
the trusted boundary that validates both before compiling FigurePatch ops.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TargetSelector(BaseModel):
    """A model-friendly selector resolved only against the current figure."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["figure", "viewport", "analysis", "layout", "panel", "layer"]
    ids: List[str] = Field(default_factory=list)
    panel_id: Optional[str] = None
    position: Literal["explicit", "all", "first", "last"] = "explicit"
    count: Optional[int] = None
    kinds: List[str] = Field(default_factory=list)


class CapabilityCall(BaseModel):
    """Generic structured-output envelope; arguments are validated server-side."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    target: TargetSelector
    arguments_json: str


@dataclass(frozen=True)
class CompiledCapability:
    operations: List[Dict[str, Any]]
    requires_confirmation: bool = False


class FigureTargetResolver:
    """Resolve positional language such as 'the bottom four' deterministically."""

    def resolve(self, selector: TargetSelector, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        if selector.scope == "layer":
            return self._resolve_layers(selector, spec)
        if selector.scope == "panel":
            return self._resolve_panels(selector, spec)
        if selector.ids or selector.panel_id or selector.count is not None or selector.kinds:
            raise ValueError(f"{selector.scope} 目标不接受图层或面板筛选条件。")
        return [{"id": selector.scope, "kind": selector.scope}]

    def _resolve_layers(self, selector: TargetSelector, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        panels = spec.get("panels", [])
        if selector.panel_id:
            panels = [panel for panel in panels if panel.get("id") == selector.panel_id]
            if not panels:
                raise ValueError(f"目标面板不存在：{selector.panel_id!r}。")
        candidates = [layer for panel in panels for layer in panel.get("layers", [])]
        if selector.kinds:
            candidates = [layer for layer in candidates if layer.get("kind") in selector.kinds]
        return self._choose(selector, candidates, "图层")

    def _resolve_panels(self, selector: TargetSelector, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        if selector.panel_id and not selector.ids:
            selector = selector.model_copy(update={"ids": [selector.panel_id], "position": "explicit"})
        return self._choose(selector, list(spec.get("panels", [])), "面板")

    @staticmethod
    def _choose(selector: TargetSelector, candidates: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
        by_id = {item.get("id"): item for item in candidates}
        if selector.ids:
            missing = [item_id for item_id in selector.ids if item_id not in by_id]
            if missing:
                raise ValueError(f"目标{label}不存在或不符合筛选条件：{', '.join(missing)}。")
            chosen = [by_id[item_id] for item_id in selector.ids]
        elif selector.position == "all":
            chosen = candidates
        elif selector.position in {"first", "last"}:
            if selector.count is None or selector.count <= 0:
                raise ValueError("first/last 目标必须提供正整数 count。")
            chosen = candidates[: selector.count] if selector.position == "first" else candidates[-selector.count :]
        else:
            raise ValueError(f"请明确要修改哪些{label}。")
        if not chosen:
            raise ValueError(f"没有找到符合条件的{label}。")
        if selector.count is not None and len(chosen) < selector.count:
            raise ValueError(f"只找到 {len(chosen)} 个{label}，少于请求的 {selector.count} 个。")
        return chosen


class CapabilityRegistry:
    """Load, advertise, validate and compile allow-listed capabilities."""

    def __init__(self, definitions: Optional[List[Dict[str, Any]]] = None):
        if definitions is None:
            with resources.files("cfizz.agent").joinpath("capabilities.json").open(encoding="utf-8") as handle:
                definitions = json.load(handle)
        self._definitions = {item["id"]: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("能力注册表包含重复 ID。")
        self.resolver = FigureTargetResolver()

    def model_catalog(self) -> List[Dict[str, Any]]:
        return list(self._definitions.values())

    def compile(self, call: CapabilityCall, spec: Dict[str, Any]) -> CompiledCapability:
        definition = self._definitions.get(call.capability_id)
        if definition is None:
            raise ValueError(f"不支持的绘图能力：{call.capability_id!r}。")
        if call.target.scope != definition["target_scope"]:
            raise ValueError(f"{call.capability_id} 的目标必须是 {definition['target_scope']}。")
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("能力参数不是合法 JSON。") from exc
        if not isinstance(arguments, dict):
            raise ValueError("能力参数必须是 JSON 对象。")
        self._validate_arguments(arguments, definition.get("arguments", {}))
        targets = self.resolver.resolve(call.target, spec)
        allowed_kinds = definition.get("allowed_kinds", [])
        if allowed_kinds:
            invalid = [item.get("id") for item in targets if item.get("kind") not in allowed_kinds]
            if invalid:
                raise ValueError(f"{call.capability_id} 不支持这些图层：{', '.join(invalid)}。")
        compiler = getattr(self, f"_compile_{call.capability_id.replace('.', '_')}", None)
        if compiler is None:
            raise ValueError(f"能力尚未接入编译器：{call.capability_id}。")
        return CompiledCapability(compiler(arguments, targets), bool(definition.get("requires_confirmation")))

    @staticmethod
    def _validate_arguments(arguments: Dict[str, Any], schema: Dict[str, Dict[str, Any]]) -> None:
        unknown = set(arguments) - set(schema)
        if unknown:
            raise ValueError(f"能力包含未知参数：{', '.join(sorted(unknown))}。")
        for name, rules in schema.items():
            if rules.get("required") and name not in arguments:
                raise ValueError(f"能力缺少参数：{name}。")
            if name not in arguments or arguments[name] is None:
                continue
            value = arguments[name]
            expected = rules.get("type")
            valid = (
                expected == "string" and isinstance(value, str)
                or expected == "integer" and isinstance(value, int) and not isinstance(value, bool)
                or expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
            )
            if not valid:
                raise ValueError(f"参数 {name} 类型应为 {expected}。")
            if "enum" in rules and value not in rules["enum"]:
                raise ValueError(f"参数 {name} 必须是 {rules['enum']} 之一。")
            if "minimum" in rules and value < rules["minimum"]:
                raise ValueError(f"参数 {name} 不能小于 {rules['minimum']}。")
            if "maximum" in rules and value > rules["maximum"]:
                raise ValueError(f"参数 {name} 不能大于 {rules['maximum']}。")

    @staticmethod
    def _update(kind: str, field: str, value: Any, target_id: Optional[str] = None) -> Dict[str, Any]:
        item = {"op": "update", "target_kind": kind, "field": field, "value": value}
        if target_id:
            item["target_id"] = target_id
        return item

    def _compile_tracks_sync_y_axis(self, args, targets):
        if len(targets) < 2:
            raise ValueError("共享 y 轴至少需要两条数值轨道。")
        mode = args["mode"]
        if mode == "fixed":
            if args.get("min_value") is None or args.get("max_value") is None:
                raise ValueError("固定 y 轴需要 min_value 和 max_value。")
            if args["max_value"] <= args["min_value"]:
                raise ValueError("max_value 必须大于 min_value。")
        if mode == "independent":
            groups = {item["id"]: None for item in targets}
        elif mode == "by_assay":
            groups = {item["id"]: f"assay_y__{self._assay_group(item)}" for item in targets}
        else:
            shared = "shared_y__" + "__".join(item["id"] for item in targets)
            groups = {item["id"]: shared for item in targets}
        operations = [self._update("layer", "style.y_scale_group", groups[item["id"]], item["id"]) for item in targets]
        if mode == "fixed":
            for item in targets:
                operations.append(self._update("layer", "style.min_value", args["min_value"], item["id"]))
                operations.append(self._update("layer", "style.max_value", args["max_value"], item["id"]))
        return operations

    def _compile_tracks_reorder(self, args, targets):
        if len(targets) != 1:
            raise ValueError("一次只能移动一条轨道；请明确要调整哪条轨道。")
        layer_id = targets[0]["id"]
        before_layer_id = args.get("before_layer_id")
        if before_layer_id == layer_id:
            raise ValueError("目标轨道不能移动到自己之前。")
        operation = {"op": "move_layer", "layer_id": layer_id}
        if before_layer_id:
            operation["before_layer_id"] = before_layer_id
        return [operation]

    @staticmethod
    def _assay_group(layer: Dict[str, Any]) -> str:
        explicit = str(layer.get("style", {}).get("assay_group") or "").strip()
        if explicit:
            return re.sub(r"[^a-z0-9]+", "_", explicit.lower()).strip("_") or "signal"
        label = str(layer.get("label") or layer.get("id") or "signal").lower()
        for group, tokens in (
            ("atac", ("atac",)),
            ("ctcf", ("ctcf",)),
            ("h3k", ("h3k", "histone")),
            ("rna", ("rna", "rnaseq", "rna-seq")),
        ):
            if any(token in label for token in tokens):
                return group
        return "signal"

    def _compile_figure_set_font_size(self, args, targets):
        return [self._update("layout", "font_size", float(args["value"]))]

    def _compile_figure_set_type(self, args, targets):
        return [self._update("figure", "figure_type", args["value"])]

    def _compile_viewport_set_region(self, args, targets):
        if args["end"] <= args["start"]:
            raise ValueError("区域终点必须大于起点。")
        return [self._update("viewport", key, args[key]) for key in ("chrom", "start", "end")]

    def _compile_analysis_set_resolution(self, args, targets):
        return [self._update("analysis", "resolution", args["value"])]

    def _compile_layer_set_color(self, args, targets):
        return [self._update("layer", "style.cmap" if item.get("kind") == "hic" else "style.color", args["value"], item["id"]) for item in targets]

    def _compile_layer_set_diverging_palette(self, args, targets):
        operations = []
        for item in targets:
            operations.extend([
                self._update("layer", "style.cmap", args["cmap"], item["id"]),
                self._update("layer", "style.positive_color", args["positive_color"], item["id"]),
                self._update("layer", "style.negative_color", args["negative_color"], item["id"]),
            ])
        return operations

    def _compile_panel_set_height(self, args, targets):
        return [self._update("panel", "height_cm", float(args["value"]), item["id"]) for item in targets]
