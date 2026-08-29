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
from typing import Any, Dict, Iterable, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from .figure_types import FIGURE_TYPES, FIGURE_TYPE_BY_ID


ParameterOperation = Literal["set", "increase", "decrease", "toggle"]
ParameterTarget = Literal["figure", "analysis", "layout", "export", "panel", "layer"]


_ALL_FIGURE_TYPES = frozenset(item["id"] for item in FIGURE_TYPES if item.get("ready"))
_HIC_FIGURE_TYPES = frozenset(
    item["id"] for item in FIGURE_TYPES
    if "hic" in (item.get("input_contract", {}).get("roles") or {})
)
_RESOLUTION_FIGURE_TYPES = _HIC_FIGURE_TYPES - frozenset({"tad_insulation_track"})
_BALANCE_FIGURE_TYPES = _HIC_FIGURE_TYPES - frozenset({
    "compartment_multi", "compartment_diff_region", "compartment_eigenvector",
    "compartment_saddle", "tad_insulation_track",
})
_INTEGRATED_FIGURE_TYPES = frozenset(
    item["id"] for item in FIGURE_TYPES if item.get("track_mode") == "integrated"
)
_STANDALONE_TRACK_FIGURE_TYPES = frozenset({
    "tracks_signal", "tracks_genes", "tracks_intervals", "tracks_mixed",
})
_COMPOSITE_FIGURE_TYPES = _INTEGRATED_FIGURE_TYPES | _STANDALONE_TRACK_FIGURE_TYPES
_LAYOUT_FIGURE_TYPES = _COMPOSITE_FIGURE_TYPES
_DPI_FIGURE_TYPES = _ALL_FIGURE_TYPES - frozenset({
    "compartment_multi", "compartment_diff_region", "compartment_saddle",
})


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


def _group_for(parameter: str) -> str:
    """Return a stable UI group for a registered parameter.

    The browser and API clients use these groups to present a compact editor
    instead of one undifferentiated list.  Keep the grouping here so a new
    renderer option cannot require a second frontend-only catalogue.
    """
    name = parameter.rsplit(".", 1)[-1]
    if name in {"cmap", "color", "positive_color", "negative_color"} or name.endswith(("_color", "_cmap")):
        return "color"
    if name in {"vmin", "vmax", "min_value", "max_value", "color_scale", "shared_color_scale"}:
        return "scale"
    if name in {"font_size", "fontsize"}:
        return "typography"
    if name in {"visible", "show_title", "labels", "show_boundaries", "show_counts", "flip_vertical", "arrowhead_included"}:
        return "visibility"
    if name in {"dpi"}:
        return "export"
    if any(token in name for token in ("width", "height", "margin", "gap", "size", "ratio")):
        return "layout"
    if name in {"resolution", "balance", "normalization", "window", "window_size", "flank", "corner_size", "min_distance", "n_bins", "top_n", "method", "contact_type", "number_of_bins", "summary_method"}:
        return "analysis"
    if name in {"label", "title", "name", "prefered_name", "plot_type", "gtf_style"}:
        return "content"
    return "style"


_BASE_DEFINITIONS = (
    ParameterDefinition("figure", "title", "string", "工作区与图标题", figure_types=_ALL_FIGURE_TYPES),
    ParameterDefinition(
        "analysis", "resolution", "integer", "Hi-C 分辨率（bp）", 1, 100_000_000,
        scientific=True, figure_types=_RESOLUTION_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "analysis", "balance", "boolean", "是否使用平衡后的 Hi-C 矩阵",
        scientific=True, figure_types=_BALANCE_FIGURE_TYPES,
    ),
    # ``normalization`` is intentionally absent: the adapter currently only
    # accepts raw matrices.  Advertising O/E/log2 O/E here used to create an
    # edit that was accepted by the planner and then rejected by rendering.
    ParameterDefinition(
        "layout", "width_cm", "number", "主绘图区宽度（厘米）", 3, 100,
        figure_types=_LAYOUT_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "layout", "gap_cm", "number", "Hi-C 与轨道间距（厘米）", 0, 10,
        figure_types=_INTEGRATED_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "layout", "left_margin_cm", "number", "左侧留白（厘米）", 0, 20,
        figure_types=_LAYOUT_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "layout", "right_margin_cm", "number", "右侧留白（厘米）", 0, 20,
        figure_types=_LAYOUT_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "layout", "font_size", "number", "整张图基础字体（pt）", 3, 24,
        figure_types=_INTEGRATED_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "export", "dpi", "integer", "PNG 导出清晰度（DPI）", 72, 2400,
        default=300, figure_types=_DPI_FIGURE_TYPES,
    ),
    ParameterDefinition("panel", "label", "string", "面板标题", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition(
        "panel", "height_cm", "number", "面板高度（厘米）", 0.2, 50,
        figure_types=_COMPOSITE_FIGURE_TYPES,
    ),
    ParameterDefinition("layer", "label", "string", "图层名称", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition(
        "layer", "visible", "boolean", "图层是否显示", default=True,
        figure_types=_COMPOSITE_FIGURE_TYPES,
    ),
    ParameterDefinition(
        "layer", "height_cm", "number", "实际绘制轨道高度（厘米）", 0.2, 50,
        figure_types=_COMPOSITE_FIGURE_TYPES,
    ),
)

_LAYER_STYLE_DEFINITIONS = (
    ParameterDefinition(
        "layer", "style.cmap", "string", "Hi-C 色图名称",
        layer_kinds=frozenset({"hic"}), default="Reds",
        figure_types=frozenset({
            "hic_triangle", "hic_triangle_multi", "tracks_integrated",
            "tad_insulation", "hic_square", "loop_heatmap",
            "loop_apa", "tad_boundary_square",
        }),
    ),
    ParameterDefinition(
        "layer", "style.cmap", "string", "O/E 热图色图名称",
        layer_kinds=frozenset({"hic"}), default="RdBu_r",
        figure_types=frozenset({"hic_oe"}),
    ),
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
    ParameterDefinition(
        "layer", "style.color_scale", "enum", "Hi-C 色标缩放",
        enum=("linear", "log"), layer_kinds=frozenset({"hic"}), default="linear",
        figure_types=frozenset({
            "hic_triangle", "hic_triangle_multi", "tracks_integrated",
            "hic_square", "loop_heatmap", "tad_boundary_square",
        }),
    ),
    ParameterDefinition(
        "layer", "style.vmin", "number_or_null", "热图色标下限；null 表示自动", -1e12, 1e12,
        layer_kinds=frozenset({"hic"}),
        figure_types=frozenset({"hic_square", "hic_oe", "loop_heatmap", "loop_apa", "tad_boundary_square"}),
    ),
    ParameterDefinition(
        "layer", "style.vmax", "number_or_null", "热图色标上限；null 表示自动", -1e12, 1e12,
        layer_kinds=frozenset({"hic"}),
        figure_types=frozenset({"hic_square", "hic_oe", "loop_heatmap", "loop_apa", "tad_boundary_square"}),
    ),
    ParameterDefinition(
        "layer", "style.plot_size", "number", "热图尺寸", 0.5, 50,
        layer_kinds=frozenset({"hic"}), default=4.0,
        figure_types=frozenset({"hic_square", "hic_oe", "loop_heatmap", "loop_apa", "tad_boundary_square"}),
    ),
    ParameterDefinition(
        "layer", "style.window", "integer", "APA 窗口（bin）", 1, 1000,
        layer_kinds=frozenset({"hic"}), default=5, scientific=True,
        figure_types=frozenset({"loop_apa"}),
    ),
    ParameterDefinition(
        "layer", "style.corner_size", "integer", "APA 角落尺寸（bin）", 0, 1000,
        layer_kinds=frozenset({"hic"}), default=3, scientific=True,
        figure_types=frozenset({"loop_apa"}),
    ),
    ParameterDefinition(
        "layer", "style.min_distance", "integer", "APA 最小 Loop 距离（bin）", 0, 100_000,
        layer_kinds=frozenset({"hic"}), default=10, scientific=True,
        figure_types=frozenset({"loop_apa"}),
    ),
    ParameterDefinition(
        "layer", "style.window_size", "integer", "TAD insulation 窗口（bp）", 1_000, 10_000_000,
        scientific=True, layer_kinds=frozenset({"hic"}), default=100_000,
        figure_types=frozenset({"tad_insulation", "tad_insulation_track", "tad_diff_region", "tracks_integrated"}),
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
    ParameterDefinition(
        "layer", "style.line_color", "string", "Insulation score 曲线颜色",
        layer_kinds=frozenset({"hic"}), default="#1f77b4",
        figure_types=frozenset({"tad_insulation_track"}),
    ),
    ParameterDefinition(
        "layer", "style.boundary_color", "string", "Insulation 边界标记颜色",
        layer_kinds=frozenset({"hic"}), default="#d62728",
        figure_types=frozenset({"tad_insulation_track"}),
    ),
    ParameterDefinition(
        "layer", "style.show_boundaries", "boolean", "是否显示 Insulation 边界标记",
        layer_kinds=frozenset({"hic"}), default=True,
        figure_types=frozenset({"tad_insulation_track"}),
    ),
    ParameterDefinition(
        "layer", "style.width_cm", "number", "Insulation 轨道宽度（厘米）", 3, 100,
        layer_kinds=frozenset({"hic"}), default=25.4,
        figure_types=frozenset({"tad_insulation_track"}),
    ),
    ParameterDefinition(
        "layer", "style.height_cm", "number", "Insulation 轨道高度（厘米）", 0.5, 50,
        layer_kinds=frozenset({"hic"}), default=5.08,
        figure_types=frozenset({"tad_insulation_track"}),
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
    ParameterDefinition("layer", "style.color", "string", "轨道主颜色", layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default="#333333", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.alpha", "number", "轨道透明度", 0, 1, layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=0.8, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.show_title", "boolean", "是否显示轨道标题", layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=True, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.fontsize", "number", "轨道标签字体（pt）", 3, 24, layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=5, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.labels", "boolean", "是否显示注释标签", layer_kinds=frozenset({"genes", "intervals"}), default=True, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.line_width", "number", "轨道线宽", 0, 10, layer_kinds=frozenset({"bigwig", "genes", "intervals"}), default=0.5, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.min_value", "number_or_null", "信号轨道 y 轴下限；null 表示自动", -1e12, 1e12, layer_kinds=frozenset({"bigwig"}), figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.max_value", "number_or_null", "信号轨道 y 轴上限；null 表示自动", -1e12, 1e12, layer_kinds=frozenset({"bigwig"}), figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.number_of_bins", "integer", "BigWig 显示采样点数", 10, 100_000, layer_kinds=frozenset({"bigwig"}), default=700, scientific=True, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.summary_method", "enum", "BigWig 区间聚合方式", enum=("mean", "min", "max", "sum", "coverage", "std"), layer_kinds=frozenset({"bigwig"}), default="mean", scientific=True, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.plot_type", "enum", "信号轨道绘制方式", enum=("fill", "line", "points"), layer_kinds=frozenset({"bigwig"}), default="fill", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.gtf_style", "enum", "基因结构样式", enum=("flybase", "UCSC", "tssarrow", "exonarrows"), layer_kinds=frozenset({"genes"}), default="flybase", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.border_color", "string", "基因外显子边框颜色", layer_kinds=frozenset({"genes"}), default="black", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.color_utr", "string", "UTR 颜色", layer_kinds=frozenset({"genes"}), default="#666666", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.color_backbone", "string", "基因骨架颜色", layer_kinds=frozenset({"genes"}), default="black", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.color_arrow", "string", "基因方向箭头颜色", layer_kinds=frozenset({"genes"}), default="black", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.height_utr", "number", "UTR 相对高度", 0.05, 5, layer_kinds=frozenset({"genes"}), default=1.0, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.height_intron", "number", "内含子相对高度", 0.05, 5, layer_kinds=frozenset({"genes"}), default=0.5, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.arrowhead_fraction", "number", "方向箭头头部相对宽度", 0.0001, 0.2, layer_kinds=frozenset({"genes"}), default=0.004, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.arrowhead_included", "boolean", "区间端点是否包含箭头头部", layer_kinds=frozenset({"genes"}), default=False, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.arrow_interval", "integer", "基因骨架箭头间隔", 1, 100, layer_kinds=frozenset({"genes"}), default=2, figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.prefered_name", "enum", "优先使用的基因名称字段", enum=("transcript_name", "gene_name"), layer_kinds=frozenset({"genes"}), default="transcript_name", figure_types=_COMPOSITE_FIGURE_TYPES),
    ParameterDefinition("layer", "style.y_scale_group", "string_or_null", "共享 y 轴组；null 表示独立", layer_kinds=frozenset({"bigwig"}), figure_types=_INTEGRATED_FIGURE_TYPES),
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
    ParameterDefinition("figure", "workflow_options.vmin", "number_or_null", "多样本 Hi-C 色标下限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number_or_null", "多样本 Hi-C 色标上限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"hic_multi"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "多样本 Hi-C 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"hic_multi"})),

    # A/B compartment regional comparison.
    ParameterDefinition("figure", "workflow_options.vmin", "number", "Compartment 色标下限", -100, 100, default=-2, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number", "Compartment 色标上限", -100, 100, default=2, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "Compartment 子图尺寸", 0.5, 50, default=3.0, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),
    ParameterDefinition("figure", "workflow_options.bar_height_ratio", "number", "E1 轨道高度比例", 0.05, 2, default=0.3, figure_types=frozenset({"compartment_multi", "compartment_diff_region"})),

    # Differential compartment scatter categories and marginal densities.
    ParameterDefinition("figure", "workflow_options.stable_a_color", "string", "差异散点图 Stable_A 颜色", default="#264653", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.stable_b_color", "string", "差异散点图 Stable_B 颜色", default="#27736F", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.a_to_b_color", "string", "差异散点图 A_to_B 颜色", default="#F2A361", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.b_to_a_color", "string", "差异散点图 B_to_A 颜色", default="#E66F51", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.control_density_color", "string", "差异散点图对照组边缘密度颜色", default="#299D92", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.treatment_density_color", "string", "差异散点图处理组边缘密度颜色", default="#F2A361", figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.point_size", "number", "差异散点大小", 0.1, 100, default=1, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.point_alpha", "number", "差异散点透明度", 0, 1, default=0.5, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.density_alpha", "number", "边缘密度填充透明度", 0, 1, default=0.5, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.density_line_width", "number", "边缘密度曲线线宽", 0, 10, default=0.5, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.width_cm", "number", "差异散点图宽度（厘米）", 3, 50, default=8, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.height_cm", "number", "差异散点图高度（厘米）", 3, 50, default=6.4, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.font_size", "number", "差异散点图基础字体（pt）", 3, 24, default=7, figure_types=frozenset({"compartment_diff_scatter"})),
    ParameterDefinition("figure", "workflow_options.show_counts", "boolean", "图例是否显示数量和百分比", default=True, figure_types=frozenset({"compartment_diff_scatter"})),

    # Standalone E1 track.
    ParameterDefinition("figure", "workflow_options.positive_color", "string", "E1 正值颜色", default="#E41A1C", figure_types=frozenset({"compartment_eigenvector"})),
    ParameterDefinition("figure", "workflow_options.negative_color", "string", "E1 负值颜色", default="#377EB8", figure_types=frozenset({"compartment_eigenvector"})),
    ParameterDefinition("figure", "workflow_options.width_cm", "number", "E1 轨道宽度（厘米）", 3, 100, default=25.4, figure_types=frozenset({"compartment_eigenvector"})),
    ParameterDefinition("figure", "workflow_options.height_cm", "number", "E1 轨道高度（厘米）", 0.5, 50, default=5.08, figure_types=frozenset({"compartment_eigenvector"})),

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
    ParameterDefinition("figure", "workflow_options.vmin", "number_or_null", "TAD 色标下限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number_or_null", "TAD 色标上限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "TAD 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "TAD 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.triangle_ratio", "number", "TAD 三角热图高度比例", 0.05, 1.5, default=1, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.boundary_cmap", "string", "TAD 边界色图", default="Greys", figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.boundary_alpha", "number", "TAD 边界透明度", 0, 1, default=0.6, figure_types=frozenset({"tad_multi"})),
    ParameterDefinition("figure", "workflow_options.flank", "integer", "TAD pileup 两侧范围（bp）", 1_000, 100_000_000, default=300_000, scientific=True, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.method", "enum", "TAD pileup 聚合方法", enum=("mean", "median", "sum"), default="sum", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.top_n", "integer_or_null", "TAD pileup 使用的边界数量；null 表示全部", 1, 10_000_000, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "TAD pileup 色图", default="Reds", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number_or_null", "TAD pileup 色标下限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number_or_null", "TAD pileup 色标上限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "TAD pileup 色标缩放", enum=("linear", "log", "log10_linear"), default="log10_linear", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "TAD pileup 尺寸", 0.5, 50, default=4, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.cbar_label", "string_or_null", "TAD pileup 色标标题；null 表示自动", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.label_orientation", "enum", "色标标题方向", enum=("vertical", "horizontal"), default="vertical", figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.color_scale_for_cbar", "enum_or_null", "色标刻度显示方式；null 表示跟随热图", enum=("linear", "log", "log10_linear"), figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),
    ParameterDefinition("figure", "workflow_options.cbar_height", "number", "色标高度比例", 0.02, 0.8, default=0.15, figure_types=frozenset({"tad_boundary_pileup", "tad_diff_pileup"})),

    # TAD differential regional workflow uses the integrated renderer.
    ParameterDefinition("figure", "workflow_options.window_size", "integer", "差异区域 Insulation 窗口（bp）", 1_000, 10_000_000, default=100_000, scientific=True, figure_types=frozenset({"tad_diff_region"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "差异区域 Hi-C 色图", default="Reds", figure_types=frozenset({"tad_diff_region"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "差异区域 Hi-C 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"tad_diff_region"})),
    ParameterDefinition("figure", "workflow_options.triangle_ratio", "number", "差异区域三角热图高度比例", 0.05, 1.5, default=0.5, figure_types=frozenset({"tad_diff_region"})),
    ParameterDefinition("figure", "workflow_options.boundary_cmap", "string", "差异区域边界色图", default="Blues", figure_types=frozenset({"tad_diff_region"})),
    ParameterDefinition("figure", "workflow_options.boundary_alpha", "number", "差异区域边界透明度", 0, 1, default=0.9, figure_types=frozenset({"tad_diff_region"})),

    # Loop regional comparison and APA.  ``loop_size`` is the marker area
    # passed by CFIZZ's scatter call; it is intentionally not triangle_ratio.
    ParameterDefinition("figure", "workflow_options.cmap", "string", "Loop 热图色图", default="Reds", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number_or_null", "Loop 热图色标下限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number_or_null", "Loop 热图色标上限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.color_scale", "enum", "Loop 色标缩放", enum=("linear", "log"), default="linear", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_color", "string", "Loop 圈颜色", default="blue", figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_alpha", "number", "Loop 圈透明度", 0, 1, default=0.6, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.loop_size", "number", "Loop 圈大小（CFIZZ marker size）", 0.1, 1000, default=50, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "Loop 子图尺寸", 0.5, 50, default=4, figure_types=frozenset({"loop_multi", "loop_diff_region"})),
    ParameterDefinition("figure", "workflow_options.window", "integer", "APA 窗口（bin）", 1, 1000, default=5, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.corner_size", "integer", "APA 角落尺寸（bin）", 0, 1000, default=3, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.min_distance", "integer", "APA 最小 loop 距离（bin）", 0, 100000, default=10, scientific=True, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.cmap", "string", "APA 色图", default="Reds", figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.vmin", "number_or_null", "APA 色标下限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.vmax", "number_or_null", "APA 色标上限；null 表示自动", -1e12, 1e12, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),
    ParameterDefinition("figure", "workflow_options.plot_size", "number", "APA 子图尺寸", 0.5, 50, default=2, figure_types=frozenset({"loop_apa_multi", "loop_diff_apa"})),

    # Differential classification bar charts.
    ParameterDefinition("figure", "workflow_options.window_mult", "enum", "TAD 边界匹配窗口（分辨率倍数）", enum=(5, 10, 50), default=10, scientific=True, figure_types=frozenset({"tad_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.stable_color", "string", "TAD stable 分类颜色", default="#264653", figure_types=frozenset({"tad_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.shift_color", "string", "TAD boundary shift 分类颜色", default="#299D92", figure_types=frozenset({"tad_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.unique_color", "string", "TAD unique 分类颜色", default="#E66F51", figure_types=frozenset({"tad_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.stable_color", "string", "Loop stable 分类颜色", default="#264653", figure_types=frozenset({"loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.low_color", "string", "Loop low-FE 分类颜色", default="#299D92", figure_types=frozenset({"loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.medium_color", "string", "Loop medium-FE 分类颜色", default="#F2A361", figure_types=frozenset({"loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.high_color", "string", "Loop high-FE 分类颜色", default="#E66F51", figure_types=frozenset({"loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.width_cm", "number", "差异分类图宽度（厘米）", 3, 50, default=6.4, figure_types=frozenset({"tad_diff_stacked", "loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.height_cm", "number", "差异分类图高度（厘米）", 3, 50, default=5, figure_types=frozenset({"tad_diff_stacked", "loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.font_size", "number", "差异分类图标签字体（pt）", 3, 24, default=5, figure_types=frozenset({"tad_diff_stacked", "loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.bar_width", "number", "差异分类柱宽", 0.1, 1, default=0.8, figure_types=frozenset({"tad_diff_stacked", "loop_diff_stacked"})),
    ParameterDefinition("figure", "workflow_options.show_counts", "boolean", "柱内是否显示百分比与数量", default=True, figure_types=frozenset({"tad_diff_stacked", "loop_diff_stacked"})),
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
            current_value = entry.get("current_value")
            default_value = definition.default
            has_default = default_value is not None or definition.value_type.endswith("_or_null")
            is_modified = has_default and current_value != default_value
            item = {
                "target_kind": definition.target_kind,
                "target_id": entry.get("target_id"),
                "parameter": definition.parameter,
                "type": definition.value_type,
                "description": definition.description,
                "group": _group_for(definition.parameter),
                "current_value": current_value,
                "default_value": default_value,
                "has_default": has_default,
                "is_overridden": bool(entry.get("is_explicit")),
                "is_modified": is_modified,
                "comparison": {
                    "current": current_value,
                    "default": default_value,
                    "available": has_default,
                    "modified": is_modified,
                },
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

    def normalize_visual_edit(self, edit: ParameterEdit) -> tuple[ParameterEdit, Optional[str]]:
        """Clamp a numeric presentation edit to the nearest advertised bound.

        Scientific parameters remain strict: silently changing a requested
        resolution, analysis window, or normalization-adjacent value could
        alter the meaning of a result.  Presentation-only values such as font
        size, margins, dimensions, opacity, and export DPI are safe to clamp,
        provided the caller tells the user which value will actually be used.
        """
        entry = self._find_entry(edit)
        definition: ParameterDefinition = entry["definition"]
        numeric_types = {"number", "integer", "number_or_null", "integer_or_null"}
        if definition.scientific or definition.value_type not in numeric_types:
            return edit, None

        current = entry.get("current_value")
        if edit.operation in {"increase", "decrease"}:
            if not self._is_number(current) or not self._is_number(edit.value) or float(edit.value) <= 0:
                return edit, None
            direction = 1 if edit.operation == "increase" else -1
            requested = float(current) + direction * float(edit.value)
        elif self._is_number(edit.value):
            requested = float(edit.value)
        else:
            return edit, None

        applied = requested
        if definition.minimum is not None:
            applied = max(applied, definition.minimum)
        if definition.maximum is not None:
            applied = min(applied, definition.maximum)
        if applied == requested:
            return edit, None

        if definition.value_type in {"integer", "integer_or_null"}:
            applied = int(applied)
        normalized = edit.model_copy(update={
            "operation": "set",
            "value": applied,
        })
        bounds = []
        if definition.minimum is not None:
            bounds.append(f"最小 {definition.minimum:g}")
        if definition.maximum is not None:
            bounds.append(f"最大 {definition.maximum:g}")
        allowed = "、".join(bounds)
        note = (
            f"{definition.description}请求值 {requested:g} 超出可用范围"
            f"（{allowed}），实际应用 {float(applied):g}"
        )
        return normalized, note

    def _build_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        figure_type = self.spec.get("figure_type")
        for definition in _BASE_DEFINITIONS:
            if definition.target_kind in {"panel", "layer"} or not self._applies(definition, figure_type):
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
                if definition.target_kind == "panel" and self._applies(definition, figure_type):
                    if (
                        definition.parameter == "height_cm"
                        and panel.get("kind") == "signal_tracks"
                        and any(layer.get("height_cm") is not None for layer in panel.get("layers", []))
                    ):
                        continue
                    entries.append(self._entry(definition, panel.get("id"), panel))
            for layer in panel.get("layers", []):
                for definition in _BASE_DEFINITIONS:
                    if definition.target_kind == "layer" and self._applies(definition, figure_type):
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
        is_explicit = True
        for part in definition.parameter.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                is_explicit = False
                break
            value = value.get(part)
        if value is None and definition.default is not None:
            value = definition.default
        return {
            "definition": definition,
            "target_id": target_id,
            "current_value": value,
            "is_explicit": is_explicit,
        }

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
        if value is None and definition.value_type in {
            "number_or_null", "integer_or_null", "string_or_null", "enum_or_null",
        }:
            return None
        if definition.value_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"参数 {self._label(edit)} 必须是布尔值。")
        elif definition.value_type in {"number", "integer", "number_or_null", "integer_or_null"}:
            if not self._is_number(value) or not math.isfinite(float(value)):
                raise ValueError(f"参数 {self._label(edit)} 必须是数字。")
            numeric = float(value)
            if definition.minimum is not None and numeric < definition.minimum:
                raise ValueError(f"参数 {self._label(edit)} 不能小于 {definition.minimum:g}。")
            if definition.maximum is not None and numeric > definition.maximum:
                raise ValueError(f"参数 {self._label(edit)} 不能大于 {definition.maximum:g}。")
            if definition.value_type in {"integer", "integer_or_null"}:
                if not numeric.is_integer():
                    raise ValueError(f"参数 {self._label(edit)} 必须是整数。")
                value = int(numeric)
            else:
                value = numeric
        elif definition.value_type in {"enum", "enum_or_null"}:
            if value not in (definition.enum or ()):
                raise ValueError(f"参数 {self._label(edit)} 只能是 {list(definition.enum or ())}。")
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


def _figure_layer_kinds(figure_type: str) -> frozenset[str]:
    role_to_layer_kind = {
        "hic": "hic",
        "signal": "bigwig",
        "gene_annotation": "genes",
        "intervals": "intervals",
        "boundaries": "tad_boundaries",
        "insulation": "tad_boundaries",
        "loops": "loops",
        "compartment": "compartment",
    }
    roles = (
        FIGURE_TYPE_BY_ID[figure_type].get("input_contract", {}).get("roles", {})
    )
    return frozenset({
        role_to_layer_kind[role]
        for role in roles
        if role in role_to_layer_kind
    })


def _definitions_for_figure(figure_type: str) -> Iterable[ParameterDefinition]:
    """Yield the single authoritative parameter contract for a figure type."""
    available_layer_kinds = _figure_layer_kinds(figure_type)
    for definition in (*_BASE_DEFINITIONS, *_WORKFLOW_DEFINITIONS, *_LAYER_STYLE_DEFINITIONS):
        if not ParameterCatalog._applies(definition, figure_type):
            continue
        if definition.layer_kinds and not definition.layer_kinds.intersection(available_layer_kinds):
            continue
        yield definition


def workflow_parameter_definitions(figure_type: str) -> tuple[ParameterDefinition, ...]:
    """Definitions forwarded as keyword arguments to a workflow renderer."""
    return tuple(
        definition for definition in _WORKFLOW_DEFINITIONS
        if ParameterCatalog._applies(definition, figure_type)
    )


def workflow_parameter_names(figure_type: str) -> frozenset[str]:
    return frozenset(
        definition.parameter.removeprefix("workflow_options.")
        for definition in workflow_parameter_definitions(figure_type)
    )


def workflow_parameter_defaults(figure_type: str) -> Dict[str, Any]:
    """Default kwargs used by both the catalogue and the render adapter."""
    return {
        definition.parameter.removeprefix("workflow_options."): definition.default
        for definition in workflow_parameter_definitions(figure_type)
    }


def layer_style_parameter_names(figure_type: str, layer_kind: str) -> frozenset[str]:
    """Style keys that are both registered and applicable to one layer."""
    return frozenset(
        definition.parameter.removeprefix("style.")
        for definition in _LAYER_STYLE_DEFINITIONS
        if layer_kind in (definition.layer_kinds or frozenset())
        and ParameterCatalog._applies(definition, figure_type)
    )


def layer_style_defaults(figure_type: str, layer_kind: str) -> Dict[str, Any]:
    """Effective registered defaults for one concrete layer family."""
    return {
        definition.parameter.removeprefix("style."): definition.default
        for definition in _LAYER_STYLE_DEFINITIONS
        if layer_kind in (definition.layer_kinds or frozenset())
        and ParameterCatalog._applies(definition, figure_type)
        and definition.default is not None
    }


def validate_workflow_options(figure_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """Validate workflow kwargs against the same catalogue shown to users.

    This replaces the historical second allow-list in the render adapter.
    Therefore adding a parameter once makes it discoverable to the Agent and
    accepted by the renderer, while an unknown option still cannot become an
    arbitrary Python keyword argument.
    """
    if not isinstance(options, dict):
        raise ValueError("workflow_options 必须是对象。")
    catalog = ParameterCatalog({
        "figure_type": figure_type,
        "title": "",
        "analysis": {},
        "layout": {},
        "export": {},
        "workflow_options": {},
        "panels": [],
    })
    result: Dict[str, Any] = {}
    for key, value in options.items():
        parameter = f"workflow_options.{key}"
        try:
            operation, _ = catalog.compile(ParameterEdit(
                target_kind="figure", parameter=parameter, value=value,
            ))
        except ValueError as exc:
            if key not in workflow_parameter_names(figure_type):
                raise ValueError(f"{figure_type} 不支持工作流参数 {key!r}。") from exc
            raise
        result[key] = operation["value"]
    return result


def effective_workflow_options(figure_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """Merge validated overrides onto the registered renderer defaults."""
    result = workflow_parameter_defaults(figure_type)
    result.update(validate_workflow_options(figure_type, options))
    return result


def visualization_parameter_catalog(figure_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return API-ready parameter templates for every registered figure.

    Runtime target IDs are intentionally omitted here; clients can query a
    session to receive concrete panel/layer targets and current values.
    """
    if figure_type is not None and figure_type not in FIGURE_TYPE_BY_ID:
        raise ValueError(f"未知图类型：{figure_type!r}。")
    figure_ids = [figure_type] if figure_type else [item["id"] for item in FIGURE_TYPES if item.get("ready")]
    catalog: List[Dict[str, Any]] = []
    for figure_id in figure_ids:
        metadata = FIGURE_TYPE_BY_ID[figure_id]
        parameters: List[Dict[str, Any]] = []
        seen = set()
        for definition in _definitions_for_figure(figure_id):
            key = (definition.target_kind, definition.parameter, definition.layer_kinds)
            if key in seen:
                continue
            seen.add(key)
            item: Dict[str, Any] = {
                "target_kind": definition.target_kind,
                "parameter": definition.parameter,
                "type": definition.value_type,
                "description": definition.description,
                "group": _group_for(definition.parameter),
                "default_value": definition.default,
                "has_default": (
                    definition.default is not None
                    or definition.value_type.endswith("_or_null")
                ),
                "scientific": definition.scientific,
                "operations": ParameterCatalog._operations(definition),
            }
            if definition.layer_kinds:
                item["layer_kinds"] = sorted(
                    definition.layer_kinds.intersection(_figure_layer_kinds(figure_id))
                )
            if definition.minimum is not None:
                item["minimum"] = definition.minimum
            if definition.maximum is not None:
                item["maximum"] = definition.maximum
            if definition.enum is not None:
                item["enum"] = list(definition.enum)
            parameters.append(item)
        catalog.append({
            "figure_type": figure_id,
            "label": metadata["label"],
            "category": metadata["category"],
            "entrypoint": metadata["entrypoint"],
            "parameters": parameters,
        })
    return catalog


for _definition in (*_BASE_DEFINITIONS, *_WORKFLOW_DEFINITIONS, *_LAYER_STYLE_DEFINITIONS):
    unknown_types = set(_definition.figure_types or ()) - set(FIGURE_TYPE_BY_ID)
    if unknown_types:  # pragma: no cover - import-time registry invariant
        raise RuntimeError(
            f"parameter {_definition.target_kind}.{_definition.parameter} references "
            f"unknown figure types: {sorted(unknown_types)}"
        )
