"""Runtime parameter catalogue for safe, model-driven figure edits.

The catalogue is the contract between the renderer and a language model.  It
only advertises parameters that the current FigureSpec can actually use, and
compiles generic set/increase/decrease/toggle requests into FigurePatch
operations.  This avoids growing one intent name for every phrasing while
keeping arbitrary object paths out of model control.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


ParameterOperation = Literal["set", "increase", "decrease", "toggle"]
ParameterTarget = Literal["figure", "analysis", "layout", "panel", "layer"]


class ParameterEdit(BaseModel):
    """A generic edit against one entry in the runtime parameter catalogue."""

    model_config = ConfigDict(extra="forbid")

    target_kind: ParameterTarget
    parameter: str
    operation: ParameterOperation = "set"
    target_id: Optional[str] = None
    value: Optional[Union[str, float, int, bool]] = None


@dataclass(frozen=True)
class ParameterDefinition:
    target_kind: str
    parameter: str
    value_type: str
    description: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum: Optional[tuple] = None
    scientific: bool = False
    layer_kinds: Optional[frozenset] = None
    default: Any = None
    # Limit a definition to a particular FigureSpec family.  A missing value
    # keeps the historical behaviour (the definition applies to every figure
    # with a matching layer kind).  This lets us expose workflow arguments at
    # the figure level without advertising, for example, Loop parameters on
    # an ordinary Hi-C heatmap.
    figure_types: Optional[frozenset] = None


_BASE_DEFINITIONS = (
    ParameterDefinition("figure", "title", "string", "图标题"),
    ParameterDefinition("analysis", "resolution", "integer", "Hi-C 分辨率（bp）", 1, 100_000_000, scientific=True),
    ParameterDefinition("analysis", "balance", "boolean", "是否使用平衡后的 Hi-C 矩阵", scientific=True),
    ParameterDefinition("analysis", "normalization", "enum", "归一化方式", enum=("raw", "oe", "log2_oe"), scientific=True),
    ParameterDefinition("analysis", "shared_color_scale", "boolean", "多个 Hi-C 图层是否共享色标"),
    ParameterDefinition("layout", "width_cm", "number", "整张图宽度（厘米）", 3, 100),
    ParameterDefinition("layout", "gap_cm", "number", "面板或轨道间距（厘米）", 0, 10),
    ParameterDefinition("layout", "left_margin_cm", "number", "左侧留白（厘米）", 0, 20),
    ParameterDefinition("layout", "right_margin_cm", "number", "右侧留白（厘米）", 0, 20),
    ParameterDefinition("layout", "font_size", "number", "整张图基础字体（pt）", 3, 24),
    ParameterDefinition("panel", "label", "string", "面板标题"),
    ParameterDefinition("panel", "height_cm", "number", "面板高度（厘米）", 0.2, 50),
    ParameterDefinition("layer", "label", "string", "图层名称"),
    ParameterDefinition("layer", "visible", "boolean", "图层是否显示", default=True),
    ParameterDefinition("layer", "height_cm", "number", "实际绘制轨道高度（厘米）", 0.2, 50),
)

_LAYER_STYLE_DEFINITIONS = (
    ParameterDefinition("layer", "style.cmap", "string", "Hi-C 色图名称", layer_kinds=frozenset({"hic"}), default="Reds"),
    ParameterDefinition(
        "layer", "style.triangle_ratio", "number", "三角热图的相对高度（不是 Loop 圈大小）",
        0.05, 1.5, layer_kinds=frozenset({"hic"}), default=0.5,
        figure_types=frozenset({"hic_triangle", "hic_triangle_multi", "tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.flip_vertical", "boolean", "是否上下翻转三角热图",
        layer_kinds=frozenset({"hic"}), default=False,
        figure_types=frozenset({"hic_triangle", "hic_triangle_multi", "tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition("layer", "style.color_scale", "enum", "Hi-C 色标缩放", enum=("linear", "log"), layer_kinds=frozenset({"hic"}), default="linear"),
    ParameterDefinition(
        "layer", "style.window_size", "integer", "TAD insulation 窗口（bp）", 1_000, 10_000_000,
        scientific=True, layer_kinds=frozenset({"hic"}), default=100_000,
        figure_types=frozenset({"tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.boundary_cmap", "string", "TAD 边界色图", layer_kinds=frozenset({"hic"}), default="Blues_r",
        figure_types=frozenset({"tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.boundary_alpha", "number", "TAD 边界透明度", 0, 1, layer_kinds=frozenset({"hic"}), default=0.9,
        figure_types=frozenset({"tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.boundary_color", "string", "方形 TAD 边界线颜色", layer_kinds=frozenset({"hic"}), default="#d62728",
        figure_types=frozenset({"tad_boundary_square"}),
    ),
    ParameterDefinition(
        "layer", "style.boundary_width", "integer", "方形 TAD 边界线宽", 1, 20, layer_kinds=frozenset({"hic"}), default=2,
        figure_types=frozenset({"tad_boundary_square"}),
    ),
    # These are direct CFIZZ single-figure arguments.  They are deliberately
    # scoped so a marker-size request cannot be confused with triangle height
    # on an unrelated Hi-C figure.
    ParameterDefinition(
        "layer", "style.loop_color", "string", "Loop 圈颜色", layer_kinds=frozenset({"hic"}), default="blue",
        figure_types=frozenset({"loop_heatmap", "hic_triangle", "hic_triangle_multi", "tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.loop_alpha", "number", "Loop 圈透明度", 0, 1, layer_kinds=frozenset({"hic"}), default=0.6,
        figure_types=frozenset({"loop_heatmap", "hic_triangle", "hic_triangle_multi", "tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition(
        "layer", "style.loop_size", "number", "Loop 圈/marker 大小（不是热图高度）", 0.1, 1000,
        layer_kinds=frozenset({"hic"}), default=2,
        figure_types=frozenset({"loop_heatmap", "hic_triangle", "hic_triangle_multi", "tad_insulation", "tad_diff_region", "tracks_integrated"}),
    ),
    ParameterDefinition("layer", "style.positive_color", "string", "Compartment 正值颜色", layer_kinds=frozenset({"hic"}), default="red", figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.negative_color", "string", "Compartment 负值颜色", layer_kinds=frozenset({"hic"}), default="blue", figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.vmin", "number", "Compartment 色标下限", -100, 100, layer_kinds=frozenset({"hic"}), default=-2, figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.vmax", "number", "Compartment 色标上限", -100, 100, layer_kinds=frozenset({"hic"}), default=2, figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.plot_size", "number", "Compartment 热图尺寸", 0.5, 50, layer_kinds=frozenset({"hic"}), default=4.0, figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.bar_height_ratio", "number", "Compartment E1 轨道高度比例", 0.05, 2, layer_kinds=frozenset({"hic"}), default=0.3, figure_types=frozenset({"compartment"})),
    ParameterDefinition("layer", "style.color", "string", "轨道颜色", layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default="#333333"),
    ParameterDefinition("layer", "style.alpha", "number", "轨道透明度", 0, 1, layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=1.0),
    ParameterDefinition("layer", "style.show_title", "boolean", "是否显示轨道标题", layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=True),
    ParameterDefinition("layer", "style.fontsize", "number", "轨道标签字体（pt）", 3, 24, layer_kinds=frozenset({"bigwig", "genes", "intervals"})),
    ParameterDefinition("layer", "style.labels", "boolean", "是否显示注释标签", layer_kinds=frozenset({"genes", "intervals"}), default=True),
    ParameterDefinition("layer", "style.line_width", "number", "轨道线宽", 0.1, 10, layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=1.0),
    ParameterDefinition("layer", "style.min_value", "number", "信号轨道 y 轴下限", -1e12, 1e12, layer_kinds=frozenset({"bigwig"})),
    ParameterDefinition("layer", "style.max_value", "number", "信号轨道 y 轴上限", -1e12, 1e12, layer_kinds=frozenset({"bigwig"})),
    ParameterDefinition("layer", "style.y_scale_group", "string_or_null", "共享 y 轴组；null 表示独立", layer_kinds=frozenset({"bigwig"})),
)


# Parameters below are the *official* keyword arguments accepted by the
# public CFIZZ workflow entrypoints.  They live on ``figure.workflow_options``
# rather than on an individual layer because the multi-sample renderers use
# one shared value for all samples.  Keeping this registry next to the runtime
# catalog gives the model the same contract that the adapter validates.
_WORKFLOW_DEFINITIONS = (
    # Multiple Hi-C matrices.
    ParameterDefinition("figure", "workflow_options.cmap", "string", "多样本 Hi-C 色图", default="Reds", figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "多样本 Hi-C 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "多样本 Hi-C 色标下限", -1e12, 1e12, figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "多样本 Hi-C 色标上限", -1e12, 1e12, figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "多样本 Hi-C 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"hic_multi"})),

    # A/B compartment regional comparison.
    ParameterDefinition("figure", "workflow_options.vmin", "number", "Compartment 色标下限", -100, 100, default=-2, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "Compartment 色标上限", -100, 100, default=2, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "Compartment 子图尺寸", 0.5, 50, default=3.0, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.bar_height_ratio", "number", "E1 轨道高度比例", 0.05, 2, default=0.3, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),

    # Saddle plots calculate bins and contact summaries before rendering.
    ParameterDefinition("figure", "workflow_options.n_bins", "integer", "Saddle 分箱数量", 2, 1000, default=98, scientific=True, figure_types=frozenset({"compartment_saddle"})),
    ParameterDefinition("figure", "workflow_options.contact_type", "enum", "Saddle 接触类型", enum=("cis", "trans", "all"), default="cis", scientific=True, figure_types=frozenset({"compartment_saddle"})),
    ParameterDefinition("figure", "workflow_options.heatmap_size", "number", "Saddle 热图尺寸", 0.5, 50, default=1.8, figure_types=frozenset({"compartment_saddle"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "Saddle 色标下限", -100, 100, default=-2, figure_types=frozenset({"compartment_saddle"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "Saddle 色标上限", -100, 100, default=2, figure_types=frozenset({"compartment_saddle"})),

    # TAD/insulation workflows.  ``window_size`` changes the insulation
    # calculation; the remaining values are visual controls.
    ParameterDefinition("figure", "workflow_options.window_size", "integer", "Insulation 窗口（bp）", 1_000, 10_000_000, default=100_000, scientific=True, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "TAD Hi-C 色图", default="Reds", figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "TAD 色标下限", -1e12, 1e12, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "TAD 色标上限", -1e12, 1e12, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "TAD 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "TAD 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.triangle_ratio", "number", "TAD 三角热图高度比例", 0.05, 1.5, default=1, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.boundary_cmap", "string", "TAD 边界色图", default="Greys", figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.boundary_alpha", "number", "TAD 边界透明度", 0, 1, default=0.6, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.flank", "integer", "TAD pileup 两侧范围（bin）", 1, 10000, scientific=True, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.method", "string", "TAD pileup 聚合方法", default="mean", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.top_n", "integer", "TAD pileup 使用的边界数量", 1, 10_000_000, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "TAD pileup 色图", default="Reds", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "TAD pileup 色标下限", -1e12, 1e12, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "TAD pileup 色标上限", -1e12, 1e12, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "TAD pileup 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "TAD pileup 尺寸", 0.5, 50, default=4, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),

    # Loop regional comparison and APA.  ``loop_size`` is the marker area
    # passed by CFIZZ's scatter call; it is intentionally not triangle_ratio.
    ParameterDefinition("figure", "workflow_options.cmap", "string", "Loop 热图色图", default="Reds", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "Loop 热图色标下限", -1e12, 1e12, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "Loop 热图色标上限", -1e12, 1e12, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "Loop 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_color", "string", "Loop 圈颜色", default="blue", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_alpha", "number", "Loop 圈透明度", 0, 1, default=0.6, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_size", "number", "Loop 圈大小（CFIZZ marker size）", 0.1, 1000, default=50, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "Loop 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.window", "integer", "APA 窗口（bin）", 1, 1000, default=5, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.corner_size", "integer", "APA 角落尺寸（bin）", 0, 1000, default=3, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.min_distance", "integer", "APA 最小 loop 距离（bin）", 0, 100000, default=10, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "APA 色图", default="Reds", figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number", "APA 色标下限", -1e12, 1e12, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "APA 色标上限", -1e12, 1e12, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "APA 子图尺寸", 0.5, 50, default=2, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
)


class ParameterCatalog:
    """Expose and compile the editable parameters of one current figure."""

    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec
        self._entries = self._build_entries()

    def model_catalog(self) -> List[Dict[str, Any]]:
        result = []
        for entry in self._entries:
            definition = entry["definition"]
            item = {
                "target_kind": definition.target_kind,
                "target_id": entry.get("target_id"),
                "parameter": definition.parameter,
                "type": definition.value_type,
                "description": definition.description,
                "current_value": entry.get("current_value"),
                "operations": self._operations(definition),
                "scientific": definition.scientific,
            }
            if definition.minimum is not None:
                item["minimum"] = definition.minimum
            if definition.maximum is not None:
                item["maximum"] = definition.maximum
            if definition.enum is not None:
                item["enum"] = list(definition.enum)
            result.append(item)
        return result

    def compile(self, edit: ParameterEdit) -> tuple[Dict[str, Any], bool]:
        entry = self._find_entry(edit)
        definition: ParameterDefinition = entry["definition"]
        current = entry.get("current_value")

        if edit.operation == "toggle":
            if definition.value_type != "boolean" or not isinstance(current, bool):
                raise ValueError(f"参数 {self._label(edit)} 不支持 toggle。")
            value = not current
        elif edit.operation in {"increase", "decrease"}:
            if definition.value_type not in {"number", "integer"} or not self._is_number(current):
                raise ValueError(f"参数 {self._label(edit)} 不支持相对增减。")
            if not self._is_number(edit.value) or float(edit.value) <= 0:
                raise ValueError(f"参数 {self._label(edit)} 的增减量必须是正数。")
            delta = float(edit.value) * (1 if edit.operation == "increase" else -1)
            value = float(current) + delta
        else:
            value = edit.value

        value = self._validate_value(definition, value, edit)
        if edit.target_kind == "layer" and edit.parameter == "visible" and value is False:
            layer = self._layer(edit.target_id)
            visible_hics = [
                item for panel in self.spec.get("panels", []) for item in panel.get("layers", [])
                if item.get("kind") == "hic" and item.get("visible", True)
            ]
            if layer.get("kind") == "hic" and len(visible_hics) <= 1:
                raise ValueError("当前渲染器至少需要一个可见 Hi-C 图层；不能隐藏唯一的 Hi-C 图层。")
        operation = {
            "op": "update",
            "target_kind": edit.target_kind,
            "field": edit.parameter,
            "value": value,
        }
        if edit.target_kind in {"panel", "layer"}:
            operation["target_id"] = edit.target_id
        return operation, definition.scientific

    def _build_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        figure_type = self.spec.get("figure_type")
        for definition in _BASE_DEFINITIONS:
            if definition.target_kind in {"panel", "layer"}:
                continue
            entries.append(self._entry(definition, None, self._root(definition.target_kind)))
        # Workflow arguments are figure-level values.  They are advertised
        # only for the active FigureSpec type, so the model cannot select a
        # TAD/Loop parameter while editing a plain Hi-C figure.
        for definition in _WORKFLOW_DEFINITIONS:
            if self._applies(definition, figure_type):
                entries.append(self._entry(definition, None, self.spec))
        for panel in self.spec.get("panels", []):
            for definition in _BASE_DEFINITIONS:
                if definition.target_kind == "panel":
                    if (
                        definition.parameter == "height_cm"
                        and panel.get("kind") == "signal_tracks"
                        and any(layer.get("height_cm") is not None for layer in panel.get("layers", []))
                    ):
                        continue
                    entries.append(self._entry(definition, panel.get("id"), panel))
            for layer in panel.get("layers", []):
                for definition in _BASE_DEFINITIONS:
                    if definition.target_kind == "layer":
                        entries.append(self._entry(definition, layer.get("id"), layer))
                for definition in _LAYER_STYLE_DEFINITIONS:
                    if (
                        layer.get("kind") in (definition.layer_kinds or frozenset())
                        and self._applies(definition, figure_type)
                    ):
                        entries.append(self._entry(definition, layer.get("id"), layer))
        return entries

    @staticmethod
    def _applies(definition: ParameterDefinition, figure_type: Optional[str]) -> bool:
        return definition.figure_types is None or figure_type in definition.figure_types

    def _find_entry(self, edit: ParameterEdit) -> Dict[str, Any]:
        matches = [
            entry for entry in self._entries
            if entry["definition"].target_kind == edit.target_kind
            and entry["definition"].parameter == edit.parameter
            and entry.get("target_id") == (edit.target_id if edit.target_kind in {"panel", "layer"} else None)
        ]
        if not matches:
            raise ValueError(f"当前图不存在可编辑参数 {self._label(edit)}。")
        return matches[0]

    def _entry(self, definition: ParameterDefinition, target_id: Optional[str], target: Dict[str, Any]) -> Dict[str, Any]:
        value: Any = target
        for part in definition.parameter.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None and definition.parameter == "style.fontsize":
            value = self.spec.get("layout", {}).get("font_size", 5)
        elif value is None and definition.default is not None:
            value = definition.default
        return {"definition": definition, "target_id": target_id, "current_value": value}

    def _root(self, target_kind: str) -> Dict[str, Any]:
        return self.spec if target_kind == "figure" else self.spec.get(target_kind, {})

    def _layer(self, layer_id: Optional[str]) -> Dict[str, Any]:
        for panel in self.spec.get("panels", []):
            for layer in panel.get("layers", []):
                if layer.get("id") == layer_id:
                    return layer
        return {}

    @staticmethod
    def _operations(definition: ParameterDefinition) -> List[str]:
        if definition.value_type == "boolean":
            return ["set", "toggle"]
        if definition.value_type in {"number", "integer"}:
            return ["set", "increase", "decrease"]
        return ["set"]

    def _validate_value(self, definition: ParameterDefinition, value: Any, edit: ParameterEdit) -> Any:
        if definition.value_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"参数 {self._label(edit)} 必须是布尔值。")
        elif definition.value_type in {"number", "integer"}:
            if not self._is_number(value) or not math.isfinite(float(value)):
                raise ValueError(f"参数 {self._label(edit)} 必须是数字。")
            numeric = float(value)
            if definition.minimum is not None and numeric < definition.minimum:
                raise ValueError(f"参数 {self._label(edit)} 不能小于 {definition.minimum:g}。")
            if definition.maximum is not None and numeric > definition.maximum:
                raise ValueError(f"参数 {self._label(edit)} 不能大于 {definition.maximum:g}。")
            if definition.value_type == "integer":
                if not numeric.is_integer():
                    raise ValueError(f"参数 {self._label(edit)} 必须是整数。")
                value = int(numeric)
            else:
                value = numeric
        elif definition.value_type == "enum":
            if value not in (definition.enum or ()):
                raise ValueError(f"参数 {self._label(edit)} 只能是 {list(definition.enum or ())}。")
        elif definition.value_type == "string_or_null" and value is None:
            return None
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 120 or re.search(r"[\x00-\x1f]", value):
                raise ValueError(f"参数 {self._label(edit)} 必须是有效的短文本。")
        return value

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _label(edit: ParameterEdit) -> str:
        target = f"[{edit.target_id}]" if edit.target_id else ""
        return f"{edit.target_kind}{target}.{edit.parameter}"
