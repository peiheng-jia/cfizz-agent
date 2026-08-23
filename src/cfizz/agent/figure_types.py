"""Authoritative CFIZZ visualization capability catalogue.

The catalogue is deliberately broader than the left-hand selector.  A
``direct`` item can be applied to the current single-sample figure.  A
``conversation`` item needs the planner to bind several files/samples or run
an analysis workflow first.  Both modes must ultimately call the listed
CFIZZ entrypoint; the Agent is not allowed to substitute its own plot.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _item(
    figure_id: str,
    category: str,
    label: str,
    entrypoint: str,
    *,
    mode: str,
    available_from: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    hic_count: tuple[int, int | None] | None = None,
    description: str,
    ready: bool = True,
    track_mode: str = "none",
) -> Dict[str, Any]:
    if track_mode not in {"none", "integrated", "standalone"}:
        raise ValueError(f"未知轨道能力：{track_mode!r}")
    if hic_count is None and mode == "direct" and {"cool", "mcool"}.intersection(available_from):
        hic_count = (1, 1)
    item = {
        "id": figure_id,
        "category": category,
        "label": label,
        "entrypoint": entrypoint,
        "selection_mode": mode,
        "available_from": list(available_from),
        "requires": list(requires),
        "description": description,
        "ready": ready,
        # ``integrated`` means that the listed CFIZZ entrypoint accepts Hi-C
        # and track layers in one figure.  ``standalone`` is a track-only
        # renderer; it must not be treated as an append-to-Hi-C capability.
        "track_mode": track_mode,
    }
    if hic_count is not None:
        item["input_cardinality"] = {
            "hic": {"min": hic_count[0], "max": hic_count[1]},
        }
    return item


# Every user-facing figure documented by CFIZZ is represented here.  Several
# rows intentionally share an entrypoint: they are distinct scientific
# workflows/configurations of the same official CFIZZ renderer.
FIGURE_TYPES: List[Dict[str, Any]] = [
    _item("hic_triangle", "basic_hic", "三角 Hi-C 热图", "cfizz.api.quick_plot_integrated", mode="direct", available_from=("cool", "mcool"), description="使用一个明确选定的 Hi-C 样本绘制局部染色质互作三角热图；可在同一张图下方放置 CFIZZ 轨道。", track_mode="integrated"),
    _item("hic_square", "basic_hic", "方形 Hi-C 热图", "cfizz.api.plot_hic_square", mode="direct", available_from=("cool", "mcool"), description="标准方形接触矩阵。"),
    _item("hic_oe", "basic_hic", "O/E 热图", "cfizz.api.plot_hic_oe", mode="direct", available_from=("cool", "mcool"), description="Observed/Expected 接触富集热图。"),
    _item("hic_multi", "comparison", "双样本方形 Hi-C 对比", "cfizz.api.generate_multi_heatmap", mode="direct", available_from=("cool", "mcool"), requires=("两个 cool/mcool",), hic_count=(2, 2), description="使用 CFIZZ 多样本矩阵接口，将两个明确勾选的 Hi-C 样本按同一区域进行方形矩阵比较；该官方接口不带轨道面板。"),
    _item("hic_triangle_multi", "comparison", "双样本三角 Hi-C 对比", "cfizz.api.quick_plot_integrated", mode="direct", available_from=("cool", "mcool"), requires=("两个 cool/mcool",), hic_count=(2, 2), description="使用 CFIZZ 官方整合接口，将两个明确勾选的 Hi-C 样本上下镜像绘制为 FOXJ1 示例风格的三角热图；可在下方放置轨道。", track_mode="integrated"),

    _item("compartment", "compartment", "A/B Compartment 区域图", "cfizz.api.plot_hic_compartment", mode="direct", available_from=("cool", "mcool"), requires=("E1 TSV",), description="区域 O/E 热图与 E1 轨道。"),
    _item("compartment_multi", "compartment", "多样本 Compartment 对比", "cfizz.api.generate_multi_compartment", mode="conversation", requires=("每个样本的 E1 TSV", "每个样本的 O/E NPY"), hic_count=(2, None), description="多样本 A/B compartment 与 E1 对齐比较。"),
    _item("compartment_eigenvector", "compartment", "E1 特征向量轨道", "cfizz.api.plot_eigenvector_from_file", mode="direct", available_from=("cool", "mcool"), requires=("E1 TSV",), description="独立显示 compartment E1 特征向量。"),
    _item("compartment_saddle", "pileup", "Compartment Saddle", "cfizz.api.generate_multi_saddle", mode="direct", available_from=("cool", "mcool"), requires=("cool/mcool", "E1 TSV", "参考基因组/GC 校正"), description="A/B compartment 交互鞍形图，支持单样本和多样本。"),

    _item("tad_insulation", "tad", "TAD 边界区域图", "cfizz.api.quick_plot_integrated", mode="direct", available_from=("cool", "mcool"), requires=("Insulation TSV",), description="CFIZZ 三角 Hi-C 与 insulation/TAD 边界联合视图；也支持把已选轨道放在下方。", track_mode="integrated"),
    _item("tad_insulation_track", "tad", "Insulation score 轨道", "cfizz.api.plot_tad_insulation_track", mode="direct", available_from=("cool", "mcool"), requires=("Insulation TSV",), description="独立绝缘分数曲线与边界标记。", track_mode="standalone"),
    _item("tad_boundary_square", "tad", "方形 Hi-C + TAD 边界", "cfizz.api.plot_hic_tad_square", mode="direct", available_from=("cool", "mcool"), requires=("Boundaries TSV",), description="在方形接触矩阵中标记 TAD 边界行列。"),
    _item("tad_multi", "tad", "多样本 TAD 边界对比", "cfizz.api.plot_heatmap_with_tad_boundaries", mode="conversation", requires=("多个 cool/mcool", "每个样本的 Insulation TSV"), hic_count=(2, None), description="每个样本绑定各自 insulation 结果的 TAD 区域对比。"),
    _item("tad_boundary_pileup", "pileup", "TAD Boundary Pileup", "cfizz.api.plot_tad_boundary_pileup_from_files", mode="conversation", requires=("cool/mcool", "Boundaries TSV"), description="TAD 边界附近信号聚合，支持单样本和多样本。"),

    _item("loop_heatmap", "loop", "Loop 标注热图", "cfizz.api.plot_hic_loops", mode="direct", available_from=("cool", "mcool"), requires=("Loop BEDPE/TSV",), description="在 Hi-C 热图中标注 loop。"),
    _item("loop_multi", "loop", "多样本 Loop 区域对比", "cfizz.api.plot_multi_heatmap_with_loops", mode="conversation", requires=("多个 cool/mcool", "每个样本的 Loop BEDPE/TSV"), hic_count=(2, None), description="多个样本的 loop 标注热图对比。"),
    _item("loop_apa", "pileup", "Loop APA", "cfizz.api.plot_hic_loop_apa", mode="direct", available_from=("cool", "mcool"), requires=("Loop BEDPE/TSV",), description="单样本 loop 中心聚合峰值图。"),
    _item("loop_apa_multi", "pileup", "多样本 Loop APA", "cfizz.api.plot_multi_apa_heatmap", mode="conversation", requires=("多个 cool/mcool", "Loop BEDPE/TSV"), hic_count=(2, None), description="多个样本的 loop 中心聚合比较。"),

    _item("tracks_integrated", "tracks", "Hi-C 多组学整合图", "cfizz.api.quick_plot_integrated", mode="conversation", requires=("Hi-C", "BigWig/GTF/BED 中至少一种"), hic_count=(1, None), description="CFIZZ 官方整合接口：Hi-C 与 BigWig、基因、区间等轨道联合绘制。", track_mode="integrated"),
    _item("tracks_signal", "tracks", "BigWig 信号轨道图", "cfizz.api.plot_track_files", mode="conversation", requires=("BigWig",), description="一个或多个连续信号轨道。", track_mode="standalone"),
    _item("tracks_genes", "tracks", "基因注释轨道图", "cfizz.api.plot_track_files", mode="conversation", requires=("GTF/GFF",), description="CFIZZ 基因结构和标签轨道。", track_mode="standalone"),
    _item("tracks_intervals", "tracks", "BED 区间轨道图", "cfizz.api.plot_track_files", mode="conversation", requires=("BED",), description="增强子、peak 或其他区间注释轨道。", track_mode="standalone"),
    _item("tracks_mixed", "tracks", "混合轨道图", "cfizz.api.plot_track_files", mode="conversation", requires=("BigWig/GTF/BED",), description="不同类型轨道的独立组合图。", track_mode="standalone"),

    _item("compartment_diff_scatter", "differential", "Compartment 差异散点图", "cfizz.api.analyze_compartment_difference", mode="conversation", requires=("处理组 E1 TSV", "对照组 E1 TSV"), description="A/B 转换及稳定 compartment 的差异散点图。"),
    _item("tad_diff_stacked", "differential", "TAD 边界差异分类图", "cfizz.api.analyze_tad_difference", mode="conversation", requires=("处理组 Boundaries TSV", "对照组 Boundaries TSV"), description="Stable、Boundary shift 与 Unique boundary 堆叠柱状图。"),
    _item("loop_diff_stacked", "differential", "Loop 差异分类图", "cfizz.api.analyze_loop_difference", mode="conversation", requires=("处理组 Loops", "对照组 Loops"), description="gain、lost 与 common loop 堆叠柱状图。"),
    _item("compartment_diff_region", "differential", "Compartment 差异区域图", "cfizz.api.generate_multi_compartment", mode="conversation", requires=("差异结果", "多样本 E1/OE"), description="A_to_B/B_to_A 区域的多样本比较。"),
    _item("tad_diff_region", "differential", "TAD 差异区域图", "cfizz.api.quick_plot_integrated", mode="conversation", requires=("差异边界结果或每个样本的 Boundaries TSV", "多样本 Hi-C/Insulation"), hic_count=(2, None), description="Unique/shift 边界区域的多样本比较；可用一个全局差异边界表，或每个 Hi-C 样本各选一份普通 boundaries 表，范围按差异事件自动定位；可沿用 CFIZZ 整合接口放置轨道。", track_mode="integrated"),
    _item("loop_diff_region", "differential", "Loop 差异区域图", "cfizz.api.plot_multi_heatmap_with_loops", mode="conversation", requires=("差异 Loop", "多样本 Hi-C"), hic_count=(2, None), description="gain/lost loop 的区域对比。"),
    _item("tad_diff_pileup", "differential", "差异 TAD Boundary Pileup", "cfizz.api.plot_tad_boundary_pileup_from_files", mode="conversation", requires=("差异 Boundaries", "多样本 Hi-C", "可选 expected-cis"), hic_count=(2, None), description="gain/lost 边界的 O/E 聚合比较。"),
    _item("loop_diff_apa", "differential", "差异 Loop APA", "cfizz.api.plot_multi_apa_heatmap", mode="conversation", requires=("差异 Loops", "多样本 Hi-C"), hic_count=(2, None), description="gain/lost loop 在多个样本中的 APA 对比。"),
]


def _role(minimum: int = 0, maximum: int | None = None, *, label: str, per_anchor: bool = False) -> Dict[str, Any]:
    return {"min": minimum, "max": maximum, "label": label, "per_anchor": per_anchor}


def _contract(
    roles: Dict[str, Dict[str, Any]],
    *,
    allowed_roles: tuple[str, ...] | None = None,
    any_of: tuple[Dict[str, Any], ...] = (),
    pairing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "roles": roles,
        "allowed_roles": list(allowed_roles or tuple(roles)),
        "any_of": list(any_of),
        "pairing": pairing,
    }


_HIC_ONE = _role(1, 1, label="Hi-C")
_HIC_TWO = _role(2, 2, label="Hi-C")
_HIC_MULTI = _role(2, None, label="Hi-C")
_OPTIONAL = _role(0, None, label="可选轨道")
_PAIR_INSULATION = {
    "anchor_role": "hic", "companion_roles": ["insulation"], "requires_confirmation": True,
}
_PAIR_LOOPS = {
    "anchor_role": "hic", "companion_roles": ["loops"], "requires_confirmation": True,
}
_PAIR_COMPARTMENT = {
    "anchor_role": "hic", "companion_roles": ["compartment", "oe"], "requires_confirmation": True,
}

# Every catalogue row has an explicit machine-readable contract.  UI labels
# remain explanatory only; no component is allowed to infer scientific input
# roles by searching for words such as "TAD" or "Loop".
INPUT_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "hic_triangle": _contract({"hic": _HIC_ONE, "signal": _OPTIONAL, "gene_annotation": _OPTIONAL, "intervals": _OPTIONAL}),
    "hic_square": _contract({"hic": _HIC_ONE}),
    "hic_oe": _contract({"hic": _HIC_ONE}),
    "hic_multi": _contract({"hic": _HIC_TWO}),
    "hic_triangle_multi": _contract({"hic": _HIC_TWO, "signal": _OPTIONAL, "gene_annotation": _OPTIONAL, "intervals": _OPTIONAL}),
    "compartment": _contract({"hic": _HIC_ONE, "compartment": _role(1, 1, label="E1")}),
    "compartment_multi": _contract(
        {"hic": _HIC_MULTI, "compartment": _role(1, 1, label="E1", per_anchor=True), "oe": _role(1, 1, label="O/E", per_anchor=True)},
        pairing=_PAIR_COMPARTMENT,
    ),
    "compartment_eigenvector": _contract({"hic": _HIC_ONE, "compartment": _role(1, 1, label="E1")}),
    "compartment_saddle": _contract({"hic": _role(1, None, label="Hi-C"), "compartment": _role(1, None, label="E1")}),
    "tad_insulation": _contract({"hic": _HIC_ONE, "insulation": _role(1, 1, label="Insulation"), "signal": _OPTIONAL, "gene_annotation": _OPTIONAL, "intervals": _OPTIONAL}),
    "tad_insulation_track": _contract({"hic": _HIC_ONE, "insulation": _role(1, 1, label="Insulation")}),
    "tad_boundary_square": _contract({"hic": _HIC_ONE, "boundaries": _role(1, 1, label="TAD 边界")}),
    "tad_multi": _contract(
        {"hic": _HIC_MULTI, "insulation": _role(1, 1, label="Insulation", per_anchor=True)},
        pairing=_PAIR_INSULATION,
    ),
    "tad_boundary_pileup": _contract({"hic": _role(1, None, label="Hi-C"), "boundaries": _role(1, None, label="TAD 边界")}),
    "loop_heatmap": _contract({"hic": _HIC_ONE, "loops": _role(1, 1, label="Loop")}),
    "loop_multi": _contract(
        {"hic": _HIC_MULTI, "loops": _role(1, 1, label="Loop", per_anchor=True)},
        pairing=_PAIR_LOOPS,
    ),
    "loop_apa": _contract({"hic": _HIC_ONE, "loops": _role(1, 1, label="Loop")}),
    "loop_apa_multi": _contract(
        {"hic": _HIC_MULTI, "loops": _role(1, 1, label="Loop", per_anchor=True)},
        pairing=_PAIR_LOOPS,
    ),
    "tracks_integrated": _contract(
        {"hic": _role(1, None, label="Hi-C"), "signal": _OPTIONAL, "gene_annotation": _OPTIONAL, "intervals": _OPTIONAL},
        any_of=({"roles": ["signal", "gene_annotation", "intervals"], "min": 1},),
    ),
    "tracks_signal": _contract({"signal": _role(1, None, label="BigWig")}),
    "tracks_genes": _contract({"gene_annotation": _role(1, None, label="GTF/GFF")}),
    "tracks_intervals": _contract({"intervals": _role(1, None, label="BED")}),
    "tracks_mixed": _contract(
        {"signal": _OPTIONAL, "gene_annotation": _OPTIONAL, "intervals": _OPTIONAL},
        any_of=({"roles": ["signal", "gene_annotation", "intervals"], "min": 1},),
    ),
    "compartment_diff_scatter": _contract({"compartment": _role(2, 2, label="E1")}),
    "tad_diff_stacked": _contract({"boundaries": _role(2, 2, label="TAD 边界")}),
    "loop_diff_stacked": _contract({"loops": _role(2, 2, label="Loop")}),
    "compartment_diff_region": _contract({"hic": _HIC_MULTI, "compartment": _role(2, None, label="E1"), "oe": _role(2, None, label="O/E")}),
    "tad_diff_region": _contract({"hic": _HIC_MULTI, "insulation": _role(2, None, label="Insulation"), "boundaries": _role(1, None, label="差异边界或每样本 Boundaries")}),
    "loop_diff_region": _contract({"hic": _HIC_MULTI, "loops": _role(1, None, label="差异 Loop")}),
    "tad_diff_pileup": _contract({"hic": _HIC_MULTI, "boundaries": _role(1, None, label="差异边界")}),
    "loop_diff_apa": _contract({"hic": _HIC_MULTI, "loops": _role(1, None, label="差异 Loop")}),
}

if set(INPUT_CONTRACTS) != {item["id"] for item in FIGURE_TYPES}:  # pragma: no cover - import-time invariant
    missing = {item["id"] for item in FIGURE_TYPES} - set(INPUT_CONTRACTS)
    extra = set(INPUT_CONTRACTS) - {item["id"] for item in FIGURE_TYPES}
    raise RuntimeError(f"CFIZZ input contracts mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
for _figure_item in FIGURE_TYPES:
    _figure_item["input_contract"] = INPUT_CONTRACTS[_figure_item["id"]]
    hic_rule = INPUT_CONTRACTS[_figure_item["id"]]["roles"].get("hic")
    if hic_rule:
        _figure_item["input_cardinality"] = {"hic": {"min": hic_rule["min"], "max": hic_rule["max"]}}


FIGURE_TYPE_BY_ID = {item["id"]: item for item in FIGURE_TYPES}
if len(FIGURE_TYPE_BY_ID) != len(FIGURE_TYPES):  # pragma: no cover - import-time invariant
    raise RuntimeError("CFIZZ figure catalogue contains duplicate IDs")

DIRECT_FIGURE_TYPE_IDS = {
    item["id"] for item in FIGURE_TYPES
    if item["ready"] and item["selection_mode"] == "direct"
}
# A conversation workflow is still a first-class FigureSpec.  Its input
# binding is performed by the allow-listed render adapter, never by generated
# Python.  Keeping all registered IDs here prevents the old failure mode where
# the model understood a CFIZZ request correctly but FigureSpec rejected it
# before the official API could be called.
READY_FIGURE_TYPE_IDS = {
    item["id"] for item in FIGURE_TYPES if item["ready"]
}


def figure_type_catalog() -> List[Dict[str, Any]]:
    return [dict(item) for item in FIGURE_TYPES]


def figure_track_mode(figure_type: str | None) -> str:
    """Return the declared CFIZZ track capability for a figure type.

    This is intentionally a catalogue lookup instead of a UI/backend list of
    special cases.  It keeps the rule auditable when another official CFIZZ
    renderer gains an integrated track panel.
    """
    return str((FIGURE_TYPE_BY_ID.get(str(figure_type)) or {}).get("track_mode", "none"))


def supports_integrated_tracks(figure_type: str | None) -> bool:
    return figure_track_mode(figure_type) == "integrated"


def direct_figure_type_ids() -> set[str]:
    return set(DIRECT_FIGURE_TYPE_IDS)
