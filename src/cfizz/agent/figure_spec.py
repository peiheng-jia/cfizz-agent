"""Validation and default resolution for CFIZZ FigureSpec dictionaries."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .inspection import DataInspector, InspectionResult
from .figure_types import READY_FIGURE_TYPE_IDS


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    path: str
    message: str
    hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    resolved_spec: Dict[str, Any]
    issues: List[ValidationIssue] = field(default_factory=list)
    inspections: Dict[str, InspectionResult] = field(default_factory=dict)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "resolved_spec": self.resolved_spec,
            "issues": [issue.to_dict() for issue in self.issues],
            "inspections": {key: value.to_dict() for key, value in self.inspections.items()},
        }


class FigureSpecValidator:
    """Validate user-visible constraints and resolve safe defaults."""

    SUPPORTED_SCHEMA_VERSION = "0.1"
    SUPPORTED_PANEL_KINDS = {
        "hic_heatmap",
        "compartment",
        "signal_tracks",
        "saddle",
        "tad_pileup",
        "loop_apa",
    }
    SUPPORTED_LAYER_KINDS = {
        "hic",
        "bigwig",
        "genes",
        "intervals",
        "tad_boundaries",
        "loops",
        "compartment",
    }
    LAYER_SOURCE_TYPES = {
        "hic": {"cool", "mcool"},
        "bigwig": {"bigwig"},
        "genes": {"gtf", "gff"},
        "intervals": {"bed"},
        "tad_boundaries": {"insulation_tsv", "tad_tsv", "tsv"},
        "loops": {"bedpe", "loop_tsv", "tsv"},
        "compartment": {"compartment_tsv", "tsv"},
    }

    def __init__(self, inspector: Optional[DataInspector] = None):
        self.inspector = inspector

    def validate(self, spec: Mapping[str, Any], inspect_files: bool = True) -> ValidationResult:
        resolved = deepcopy(dict(spec))
        issues: List[ValidationIssue] = []
        self._apply_defaults(resolved)
        self._validate_shape(resolved, issues)

        sources = resolved.get("data_sources")
        inspections: Dict[str, InspectionResult] = {}
        if isinstance(sources, list) and inspect_files and self.inspector is not None:
            inspections = self.inspector.inspect_many(source for source in sources if isinstance(source, dict))
            self._validate_inspections(resolved, inspections, issues)

        return ValidationResult(resolved_spec=resolved, issues=issues, inspections=inspections)

    @staticmethod
    def choose_resolution(region_size: int, available: Sequence[int], target_bins: int = 300) -> int:
        if not available:
            raise ValueError("没有可用分辨率")
        ideal = max(1, region_size // target_bins)
        return min(sorted(set(int(value) for value in available)), key=lambda value: (abs(value - ideal), value))
    def _apply_defaults(self, spec: Dict[str, Any]) -> None:
        spec.setdefault("figure_type", "hic_triangle")
        analysis = spec.setdefault("analysis", {})
        analysis.setdefault("resolution", "auto")
        analysis.setdefault("balance", True)
        analysis.setdefault("normalization", "raw")
        analysis.setdefault("shared_color_scale", True)

        layout = spec.setdefault("layout", {})
        layout.setdefault("width_cm", 12)
        layout.setdefault("gap_cm", 0.15)
        layout.setdefault("left_margin_cm", 1.5)
        layout.setdefault("right_margin_cm", 2)
        layout.setdefault("font_size", 5)

        export = spec.setdefault("export", {})
        export.setdefault("formats", ["svg", "png", "pdf"])
        export.setdefault("dpi", 300)
        export.setdefault("output_basename", spec.get("figure_id", "cfizz_figure"))

    def _validate_shape(self, spec: Dict[str, Any], issues: List[ValidationIssue]) -> None:
        if spec.get("figure_type") not in READY_FIGURE_TYPE_IDS:
            self._error(issues, "unsupported_figure_type", "$.figure_type", "该图类型尚未接入当前渲染器。")
        if spec.get("schema_version") != self.SUPPORTED_SCHEMA_VERSION:
            self._error(issues, "schema_version", "$.schema_version", "不支持该 FigureSpec 版本。", f"当前支持 {self.SUPPORTED_SCHEMA_VERSION}。")

        for key in ("figure_id", "title", "data_sources", "viewport", "panels"):
            if key not in spec:
                self._error(issues, "missing_field", f"$.{key}", f"缺少必填字段：{key}。")

        viewport = spec.get("viewport", {})
        if isinstance(viewport, dict):
            chrom = viewport.get("chrom")
            start = viewport.get("start")
            end = viewport.get("end")
            if not isinstance(chrom, str) or not chrom:
                self._error(issues, "invalid_chrom", "$.viewport.chrom", "请指定有效的染色体名称。")
            if not isinstance(start, int) or start < 0:
                self._error(issues, "invalid_start", "$.viewport.start", "起点必须是大于或等于 0 的整数。")
            if not isinstance(end, int) or not isinstance(start, int) or end <= start:
                self._error(issues, "invalid_end", "$.viewport.end", "终点必须大于起点。")
        else:
            self._error(issues, "invalid_viewport", "$.viewport", "viewport 必须是对象。")

        sources = spec.get("data_sources", [])
        source_ids = set()
        source_types = {}
        all_ids = set()
        if not isinstance(sources, list):
            self._error(issues, "invalid_sources", "$.data_sources", "data_sources 必须是数组。")
            sources = []
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                self._error(issues, "invalid_source", f"$.data_sources[{index}]", "数据源必须是对象。")
                continue
            source_id = source.get("id")
            if not source_id or not source.get("path") or not source.get("type"):
                self._error(issues, "incomplete_source", f"$.data_sources[{index}]", "每个数据源都需要 id、type 和 path。")
            if source_id in all_ids:
                self._error(issues, "duplicate_id", f"$.data_sources[{index}].id", f"ID {source_id!r} 重复。")
            if source_id:
                source_ids.add(source_id)
                source_types[source_id] = source.get("type")
                all_ids.add(source_id)

        panels = spec.get("panels", [])
        if not isinstance(panels, list) or not panels:
            self._error(issues, "invalid_panels", "$.panels", "至少需要一个绘图面板。")
            return
        for panel_index, panel in enumerate(panels):
            if not isinstance(panel, dict):
                self._error(issues, "invalid_panel", f"$.panels[{panel_index}]", "面板必须是对象。")
                continue
            panel_id = panel.get("id")
            if panel_id in all_ids:
                self._error(issues, "duplicate_id", f"$.panels[{panel_index}].id", f"ID {panel_id!r} 重复。")
            if panel_id:
                all_ids.add(panel_id)
            if panel.get("kind") not in self.SUPPORTED_PANEL_KINDS:
                self._error(issues, "unsupported_panel", f"$.panels[{panel_index}].kind", "暂不支持这种面板类型。")
            layers = panel.get("layers", [])
            if not isinstance(layers, list):
                self._error(issues, "invalid_layers", f"$.panels[{panel_index}].layers", "layers 必须是数组。")
                continue
            for layer_index, layer in enumerate(layers):
                path = f"$.panels[{panel_index}].layers[{layer_index}]"
                if not isinstance(layer, dict):
                    self._error(issues, "invalid_layer", path, "图层必须是对象。")
                    continue
                layer_id = layer.get("id")
                if layer_id in all_ids:
                    self._error(issues, "duplicate_id", f"{path}.id", f"ID {layer_id!r} 重复。")
                if layer_id:
                    all_ids.add(layer_id)
                if layer.get("kind") not in self.SUPPORTED_LAYER_KINDS:
                    self._error(issues, "unsupported_layer", f"{path}.kind", "暂不支持这种图层类型。")
                if layer.get("source_id") not in source_ids:
                    self._error(issues, "missing_source", f"{path}.source_id", "图层引用的数据源不存在。")
                else:
                    allowed_types = self.LAYER_SOURCE_TYPES.get(layer.get("kind"), set())
                    actual_type = source_types.get(layer.get("source_id"))
                    if allowed_types and actual_type not in allowed_types:
                        allowed_label = ", ".join(sorted(allowed_types))
                        self._error(
                            issues,
                            "source_type_mismatch",
                            f"{path}.source_id",
                            f"{layer.get('kind')} 图层不能使用 {actual_type} 数据源。",
                            f"允许的数据类型：{allowed_label}。",
                        )
                layer_height = layer.get("height_cm")
                if layer_height is not None and (
                    not isinstance(layer_height, (int, float)) or isinstance(layer_height, bool) or layer_height <= 0
                ):
                    self._error(issues, "invalid_layer_height", f"{path}.height_cm", "图层高度必须是正数。")
                style = layer.get("style", {})
                y_scale_group = style.get("y_scale_group") if isinstance(style, dict) else None
                if y_scale_group is not None and (not isinstance(y_scale_group, str) or not y_scale_group.strip()):
                    self._error(issues, "invalid_y_scale_group", f"{path}.style.y_scale_group", "共享 y 轴组必须是非空字符串或 null。")
                if y_scale_group is not None and layer.get("kind") != "bigwig":
                    self._error(issues, "unsupported_y_scale_group", f"{path}.style.y_scale_group", "目前只有 BigWig 数值轨道可以共享 y 轴。")

        # A fresh browser session starts as a draft so the user can describe
        # a data path/workflow in chat before any Hi-C file has been chosen.
        # Drafts still go through the structural checks above, but the
        # renderer-specific "at least one visible Hi-C layer" requirement is
        # deferred until the workflow has produced a real spec.
        is_draft = bool((spec.get("metadata") or {}).get("draft"))
        if not is_draft and spec.get("figure_type") in {
            "hic_triangle", "hic_square", "hic_oe", "hic_multi",
            "tad_insulation", "tad_multi", "tad_boundary_pileup",
            "compartment", "compartment_multi", "compartment_saddle",
            "loop_heatmap", "loop_multi", "loop_apa", "loop_apa_multi",
            "tracks_integrated", "tad_diff_region", "loop_diff_region",
            "tad_diff_pileup", "loop_diff_apa",
        }:
            visible_hics = [
                layer for panel in panels if isinstance(panel, dict)
                for layer in panel.get("layers", []) if isinstance(layer, dict)
                and layer.get("kind") == "hic" and layer.get("visible", True)
            ]
            if not visible_hics:
                self._error(issues, "missing_visible_hic", "$.panels", "当前图至少需要一个可见 Hi-C 图层。")

        analysis = spec.get("analysis", {})
        if analysis.get("normalization") not in {"raw", "oe", "log2_oe"}:
            self._error(issues, "invalid_normalization", "$.analysis.normalization", "不支持该归一化方式。")
        resolution = analysis.get("resolution")
        if resolution != "auto" and (not isinstance(resolution, int) or resolution <= 0):
            self._error(issues, "invalid_resolution", "$.analysis.resolution", "分辨率必须是正整数或 auto。")

        font_size = spec.get("layout", {}).get("font_size")
        if not isinstance(font_size, (int, float)) or isinstance(font_size, bool) or not 3 <= font_size <= 24:
            self._error(issues, "invalid_font_size", "$.layout.font_size", "绘图字体必须在 3–24 pt 之间。")

    def _validate_inspections(
        self,
        spec: Dict[str, Any],
        inspections: Dict[str, InspectionResult],
        issues: List[ValidationIssue],
    ) -> None:
        for source_id, result in inspections.items():
            if not result.usable:
                self._error(issues, "unusable_source", f"$.data_sources[{source_id}]", result.error or "数据源不可用。")
            for warning in result.warnings:
                issues.append(ValidationIssue("warning", "inspection_warning", f"$.data_sources[{source_id}]", warning))

        hic_source_ids = {
            layer.get("source_id")
            for panel in spec.get("panels", [])
            if isinstance(panel, dict)
            for layer in panel.get("layers", [])
            if isinstance(layer, dict) and layer.get("kind") == "hic"
        }
        hic_results = [inspections[source_id] for source_id in hic_source_ids if source_id in inspections]
        full_hic_results = [result for result in hic_results if result.metadata.get("inspection_level") == "full"]
        if not full_hic_results:
            return

        viewport = spec.get("viewport", {})
        chrom = viewport.get("chrom")
        end = viewport.get("end")
        for result in full_hic_results:
            chromsizes = result.metadata.get("chromsizes", {})
            if chrom not in chromsizes:
                self._error(issues, "chrom_not_found", "$.viewport.chrom", f"Hi-C 文件 {_path_label(result.path)} 中没有染色体 {chrom}。")
            elif isinstance(end, int) and end > chromsizes[chrom]:
                self._error(issues, "region_out_of_bounds", "$.viewport.end", f"区域终点超过 {chrom} 长度 {chromsizes[chrom]:,}。")

        analysis = spec.get("analysis", {})
        requested = analysis.get("resolution")
        available_sets = [
            {int(value) for value in result.metadata.get("resolutions", []) if str(value).isdigit()}
            for result in full_hic_results
        ]
        shared = common_hic_resolutions(full_hic_results)
        source_details = "; ".join(
            f"{_path_label(result.path)}：{_format_resolutions(values)}"
            for result, values in zip(full_hic_results, available_sets)
        )
        if any(not values for values in available_sets):
            self._error(
                issues,
                "resolution_unknown",
                "$.analysis.resolution",
                "无法从所有 Hi-C 样本读取可用分辨率，暂时不能安全确定共同分辨率。",
                f"请检查 Hi-C 文件；当前读取结果：{source_details or '无'}。",
            )
            return
        if len(available_sets) > 1 and not shared:
            self._error(
                issues,
                "no_common_resolution",
                "$.analysis.resolution",
                "当前选中的 Hi-C 样本没有共同分辨率，无法进行同尺度绘图。",
                f"各样本可用分辨率：{source_details}。请减少样本，或准备包含相同分辨率的文件。",
            )
            return
        if requested == "auto" and shared:
            region_size = viewport.get("end", 0) - viewport.get("start", 0)
            analysis["resolution"] = self.choose_resolution(region_size, shared)
            issues.append(
                ValidationIssue(
                    "warning",
                    "resolution_auto_selected",
                    "$.analysis.resolution",
                    f"已根据区域大小选择 {analysis['resolution']:,} bp 分辨率。",
                )
            )
        elif isinstance(requested, int) and shared and requested not in shared:
            nearest = self.choose_resolution(max(1, requested * 300), shared)
            self._error(
                issues,
                "resolution_unavailable",
                "$.analysis.resolution",
                f"请求的 {requested:,} bp 分辨率并非所有 Hi-C 样本共有。",
                f"可用的共同分辨率：{_format_resolutions(shared)}；可改用最近的 {nearest:,} bp。",
            )

    @staticmethod
    def _error(
        issues: List[ValidationIssue],
        code: str,
        path: str,
        message: str,
        hint: Optional[str] = None,
    ) -> None:
        issues.append(ValidationIssue("error", code, path, message, hint))


def common_hic_resolutions(results: Iterable[InspectionResult]) -> List[int]:
    """Return resolutions supported by every inspected Hi-C input.

    Multi-sample CFIZZ APIs use one matrix grid for all panels. Treating the
    union of resolutions as available makes a request appear valid until the
    renderer opens the second matrix, where it fails with a vague error. This
    helper is shared by the validator and dataset picker so both paths expose
    the same contract.
    """
    sets = [
        {int(value) for value in result.metadata.get("resolutions", []) if str(value).isdigit()}
        for result in results
    ]
    if not sets or any(not values for values in sets):
        return []
    return sorted(set.intersection(*sets))


def _path_label(path: str) -> str:
    """Return a short path label without exposing more of the path than needed."""
    return re.split(r"[\\/]", str(path))[-1]


def _format_resolutions(values: Iterable[int]) -> str:
    ordered = sorted({int(value) for value in values})
    if not ordered:
        return "未读取到"
    return "、".join(f"{value:,} bp" for value in ordered)
