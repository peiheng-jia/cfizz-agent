"""Translate a validated FigureSpec into stable cfizz rendering calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional

from .figure_spec import FigureSpecValidator, ValidationResult
from .companions import companion_resolution, discover_companion, is_differential_tad_table


_WORKFLOW_FIGURE_TYPES = {
    "hic_multi", "compartment_multi", "compartment_eigenvector",
    "compartment_saddle", "tad_multi", "tad_boundary_pileup",
    "loop_multi", "loop_apa_multi", "tracks_signal", "tracks_genes",
    "tracks_intervals", "tracks_mixed", "tracks_integrated", "compartment_diff_scatter",
    "tad_diff_stacked", "loop_diff_stacked", "compartment_diff_region",
    "tad_diff_region", "loop_diff_region", "tad_diff_pileup",
    "loop_diff_apa",
}


def _ordered_panels_for_render(panels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return panels in the visual order expected by CFIZZ's integrated API.

    A conversational patch may add a gene/interval panel after an existing
    signal panel (``add_panel`` is append-oriented).  CFIZZ's integrated
    renderer treats the first track as the one closest to the Hi-C panel, so
    passing that append order through makes gene annotations appear below
    ATAC/other signals.  Classify by layer kind and keep the original order
    within each class; this only normalises the Hi-C → annotation → signal
    contract and does not draw anything outside CFIZZ.
    """

    def priority(item: Dict[str, Any]) -> int:
        layers = item.get("layers", [])
        kinds = {layer.get("kind") for layer in layers if isinstance(layer, dict)}
        if item.get("kind") == "hic_heatmap" or "hic" in kinds:
            return 0
        if kinds & {"genes", "intervals"} or item.get("id") == "annotation_panel":
            return 1
        if "bigwig" in kinds or item.get("id") == "signal_panel":
            return 2
        return 3

    indexed = list(enumerate(panels))
    indexed.sort(key=lambda pair: (priority(pair[1]), pair[0]))
    return [panel for _, panel in indexed]


@dataclass(frozen=True)
class GenomeRange:
    chrom: str
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass
class RenderRequest:
    entrypoint: str
    kwargs: Dict[str, Any]
    output_prefix: str
    expected_artifacts: List[str]
    validation: ValidationResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entrypoint": self.entrypoint,
            "kwargs": self._serialize(self.kwargs),
            "output_prefix": self.output_prefix,
            "expected_artifacts": self.expected_artifacts,
            "validation": self.validation.to_dict(),
        }

    @classmethod
    def _serialize(cls, value: Any) -> Any:
        if isinstance(value, GenomeRange):
            return asdict(value)
        if isinstance(value, dict):
            return {key: cls._serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._serialize(item) for item in value]
        return value


@dataclass(frozen=True)
class RenderResult:
    success: bool
    artifacts: List[str]
    request: RenderRequest
    error: Optional[str] = None


class CfizzRenderAdapter:
    """Allow-listed adapter for heatmap and integrated-track rendering."""

    def __init__(
        self,
        validator: FigureSpecValidator,
        output_root: str,
        renderer: Optional[Callable[..., Any]] = None,
    ):
        self.validator = validator
        self.output_root = Path(output_root).expanduser().resolve()
        self.renderer = renderer

    def build_request(self, spec: Dict[str, Any], inspect_files: bool = True) -> RenderRequest:
        validation = self.validator.validate(spec, inspect_files=inspect_files)
        if not validation.valid:
            messages = "; ".join(issue.message for issue in validation.errors)
            raise ValueError(f"FigureSpec 无法渲染：{messages}")

        resolved = validation.resolved_spec
        if resolved["analysis"].get("normalization", "raw") != "raw":
            raise ValueError("当前 heatmap 适配器仅支持 raw；O/E 将在下一阶段接入。")

        sources = {source["id"]: source for source in resolved["data_sources"]}
        hic_layers = []
        track_layers = []
        # Keep annotation tracks above signal tracks even when a user adds a
        # gene panel after the signal panel in a later conversational edit.
        for panel in _ordered_panels_for_render(resolved["panels"]):
            for layer in panel.get("layers", []):
                if not layer.get("visible", True):
                    continue
                if layer["kind"] == "hic":
                    hic_layers.append(layer)
                elif layer["kind"] in {"bigwig", "genes", "intervals"}:
                    track_layers.append((panel, layer))

        figure_type = resolved.get("figure_type", "hic_triangle")
        if figure_type in _WORKFLOW_FIGURE_TYPES:
            return self._build_workflow_request(
                resolved, validation, sources, hic_layers, track_layers
            )

        if not hic_layers:
            raise ValueError("当前渲染器至少需要一个可见 Hi-C 图层。")

        analysis = resolved["analysis"]
        resolution = analysis.get("resolution")
        if resolution == "auto":
            raise ValueError("无法自动确定分辨率；请安装 cooler 以读取 mcool 元数据，或明确指定分辨率。")

        if figure_type == "tad_insulation":
            # Match every Hi-C source to its own insulation file.  This is the
            # contract in CFIZZ's multi-sample TAD example; reusing sample 1's
            # boundaries for every panel is scientifically wrong.
            explicit_insulations = [item for item in sources.values() if item.get("type") == "insulation_tsv"]
            discovered_resolutions = set()
            for index, layer in enumerate(hic_layers):
                source = sources[layer["source_id"]]
                source_path = str(self.validator.inspector.resolve_path(source["path"])) if self.validator.inspector else source["path"]
                sample_tokens = {
                    str(value).lower() for value in (source.get("sample"), source.get("label"), Path(source_path).stem)
                    if value
                }
                matched = [item for item in explicit_insulations if str(item.get("sample") or item.get("label") or "").lower() in sample_tokens]
                if not matched and len(hic_layers) == 1 and len(explicit_insulations) == 1:
                    matched = explicit_insulations
                if matched:
                    item = matched[0]
                    insulation_path = str(self.validator.inspector.resolve_path(item["path"])) if self.validator.inspector else item["path"]
                    insulation_resolution = item.get("resolution")
                else:
                    companion = discover_companion(source_path, "insulation")
                    if companion is None:
                        raise ValueError(
                            f"未找到与第 {index + 1} 个样本 {Path(source_path).name} 匹配的 CFIZZ insulation TSV；"
                            "多样本 TAD 图要求每个 Hi-C 样本绑定自己的 insulation 文件。"
                        )
                    insulation_path = str(companion.path)
                    insulation_resolution = companion.resolution
                if insulation_resolution:
                    discovered_resolutions.add(int(insulation_resolution))
                tad_style = layer.setdefault("style", {})
                tad_style.update({
                    "triangle_ratio": 1,
                    "flip_vertical": bool(index % 2),
                    "insulation_path": insulation_path,
                    "window_size": tad_style.get("window_size", 100_000),
                    "boundary_cmap": tad_style.get("boundary_cmap", "Blues_r"),
                    "boundary_alpha": tad_style.get("boundary_alpha", 0.9),
                })
            if len(discovered_resolutions) > 1:
                raise ValueError("多样本 insulation 文件的分辨率不一致，不能放在同一张 CFIZZ TAD 对比图中。")
            if discovered_resolutions:
                resolution = discovered_resolutions.pop()
        elif figure_type == "loop_heatmap" and len(hic_layers) > 1:
            for index, layer in enumerate(hic_layers):
                source = sources[layer["source_id"]]
                source_path = str(self.validator.inspector.resolve_path(source["path"])) if self.validator.inspector else source["path"]
                companion = discover_companion(source_path, "loops")
                if companion is None:
                    raise ValueError(
                        f"未找到与第 {index + 1} 个样本 {Path(source_path).name} 匹配的 Loop BEDPE/TSV；"
                        "多样本 Loop 图要求每个 Hi-C 样本绑定自己的 loop calls。"
                    )
                style = layer.setdefault("style", {})
                style.update({
                    "triangle_ratio": 1,
                    "flip_vertical": bool(index % 2),
                    "loops_path": str(companion.path),
                    "loop_color": style.get("loop_color", "blue"),
                    "loop_alpha": style.get("loop_alpha", 0.6),
                    "loop_size": style.get("loop_size", 10),
                })
                if companion.resolution:
                    resolution = int(companion.resolution)
        elif figure_type not in {"hic_triangle", "hic_triangle_multi"}:
            return self._build_single_cooler_request(
                resolved, validation, sources, hic_layers[0], figure_type, resolution
            )

        hics = []
        for layer in hic_layers:
            source = sources[layer["source_id"]]
            style = layer.get("style", {})
            hic = {
                "file": str(self.validator.inspector.resolve_path(source["path"])) if self.validator.inspector else source["path"],
                "name": layer.get("label") or source.get("label") or source.get("sample"),
                "cmap": style.get("cmap", "Reds"),
                "triangle_ratio": style.get("triangle_ratio", 0.5),
                "flip_vertical": style.get("flip_vertical", False),
                "color_scale": style.get("color_scale", "linear"),
                "balance": analysis.get("balance", True),
                "resolution": resolution,
            }
            for key in ("insulation_path", "window_size", "boundary_cmap", "boundary_alpha", "loops_path", "loop_color", "loop_alpha", "loop_size"):
                if key in style:
                    hic[key] = style[key]
            hics.append(hic)

        tracks = []
        track_heights = []
        for panel, layer in track_layers:
            source = sources[layer["source_id"]]
            style = layer.get("style", {})
            track = {
                "file": str(self.validator.inspector.resolve_path(source["path"])) if self.validator.inspector else source["path"],
                "name": None if style.get("show_title") is False else (layer.get("label") or source.get("label")),
                "color": style.get("color", "#333333"),
            }
            for key in (
                "alpha", "labels", "fontsize", "gtf_style", "color_utr",
                "border_color", "color_backbone", "line_width", "plot_type",
                "min_value", "max_value", "y_scale_group",
            ):
                if key in style:
                    track[key] = style[key]
            tracks.append(track)
            if layer.get("height_cm") is not None:
                track_heights.append(float(layer["height_cm"]))
            else:
                height = panel.get("height_cm", 1.0)
                track_heights.append(1.0 if height == "auto" else float(height) / max(1, len(panel.get("layers", []))))

        output_basename = self._safe_basename(resolved["export"].get("output_basename", resolved["figure_id"]))
        output_prefix_path = self.output_root / output_basename
        formats = resolved["export"].get("formats", ["svg", "png", "pdf"])
        expected_artifacts = [f"{output_prefix_path}.{extension}" for extension in formats]
        viewport = resolved["viewport"]
        layout = resolved["layout"]

        kwargs = {
            "hics": hics,
            "tracks": tracks or None,
            "region": GenomeRange(viewport["chrom"], viewport["start"], viewport["end"]),
            "output": str(output_prefix_path),
            "n_tracks": len(tracks),
            "track_heights_cm": track_heights or None,
            "width_cm": layout.get("width_cm", 12),
            "gap_cm": layout.get("gap_cm", 0.15),
            "left_margin_cm": layout.get("left_margin_cm", 1.5),
            "right_margin_cm": layout.get("right_margin_cm", 2),
            "font_size": layout.get("font_size", 5),
            "dpi": resolved["export"].get("dpi", 300),
            "formats": formats,
            "resolution": resolution,
            "balance": analysis.get("balance", True),
        }
        return RenderRequest(
            entrypoint="cfizz.api.quick_plot_integrated",
            kwargs=kwargs,
            output_prefix=str(output_prefix_path),
            expected_artifacts=expected_artifacts,
            validation=validation,
        )

    def _build_workflow_request(
        self,
        resolved: Dict[str, Any],
        validation: ValidationResult,
        sources: Dict[str, Dict[str, Any]],
        hic_layers: List[Dict[str, Any]],
        track_layers: List[Any],
    ) -> RenderRequest:
        """Bind a registered workflow to one official public CFIZZ API."""
        figure_type = resolved["figure_type"]
        selected = set(resolved.get("workflow_source_ids") or [])
        selected_sources = {
            key: value for key, value in sources.items()
            if not selected or key in selected
        }
        if selected - set(sources):
            missing = ", ".join(sorted(selected - set(sources)))
            raise ValueError(f"工作流引用了不存在的数据源：{missing}。")

        def path(source: Dict[str, Any]) -> str:
            raw = source["path"]
            return str(self.validator.inspector.resolve_path(raw)) if self.validator.inspector else raw

        def typed(*types: str) -> List[Dict[str, Any]]:
            return [item for item in selected_sources.values() if item.get("type") in set(types)]

        hic_sources = typed("cool", "mcool")
        viewport = resolved["viewport"]
        analysis = resolved["analysis"]
        resolution = analysis.get("resolution")
        if resolution == "auto":
            raise ValueError("该 CFIZZ 工作流需要明确分辨率。")
        export = resolved["export"]
        formats = export.get("formats", ["svg", "png", "pdf"])
        output_basename = self._safe_basename(export.get("output_basename", resolved["figure_id"]))
        output_prefix = str(self.output_root / output_basename)
        sample_names = [item.get("sample") or item.get("label") or Path(path(item)).stem for item in hic_sources]
        common = {
            "chrom": viewport["chrom"], "start": viewport["start"], "end": viewport["end"],
            "resolution": int(resolution), "balance": analysis.get("balance", True),
        }

        entrypoint = ""
        kwargs: Dict[str, Any] = {}
        expected = [f"{output_prefix}.{fmt}" for fmt in formats]

        if figure_type == "hic_multi":
            if len(hic_sources) < 2:
                raise ValueError("多样本 Hi-C 对比至少需要两个 cool/mcool 数据源。")
            entrypoint = "cfizz.api.generate_multi_heatmap"
            kwargs = {
                "file_paths": [path(item) for item in hic_sources],
                "output_dir": str(self.output_root), "sample_names": sample_names,
                "chrom": common["chrom"], "resolution": common["resolution"],
                "start_pos": common["start"], "end_pos": common["end"],
                "balance": common["balance"], "output_prefix": output_prefix,
                "formats": tuple(formats), "dpi": export.get("dpi", 300),
            }
        elif figure_type == "tad_diff_region":
            # The differential-region example is an integrated CFIZZ figure,
            # not a single-sample insulation plot.  Each Hi-C panel must carry
            # its own insulation grid; the selected differential boundary TSV
            # determines the viewport but is not substituted for insulation.
            if len(hic_sources) < 2:
                raise ValueError("TAD 差异区域图至少需要两个明确选择的 cool/mcool 数据源。")
            explicit_insulations = typed("insulation_tsv")
            if len(explicit_insulations) < len(hic_sources):
                raise ValueError(
                    "TAD 差异区域图需要每个 Hi-C 样本各自对应的 Insulation TSV；"
                    "请在文件配对面板中补齐并确认样本对应关系。"
                )
            insulation_paths = self._selected_companions(
                hic_sources, explicit_insulations, path, "Insulation"
            )
            insulation_resolutions: list[int] = []
            for insulation_path in insulation_paths:
                candidate = next(
                    (
                        item for item in explicit_insulations
                        if str(Path(path(item)).resolve()).casefold()
                        == str(Path(insulation_path).resolve()).casefold()
                    ),
                    None,
                )
                value = (candidate or {}).get("resolution") if candidate else None
                value = value or companion_resolution(insulation_path)
                if value:
                    insulation_resolutions.append(int(value))
            if len(set(insulation_resolutions)) > 1:
                values = "、".join(f"{value:,} bp" for value in sorted(set(insulation_resolutions)))
                raise ValueError(f"所选 Insulation 文件的 bin 宽度不一致（{values}），不能进行 TAD 差异区域对比。")
            if insulation_resolutions:
                common["resolution"] = insulation_resolutions[0]

            # A workflow may receive either the optional global differential
            # table or one ordinary boundary grid per selected Hi-C sample.
            # Ordinary grids are compared by the viewport selector; the
            # renderer itself obtains boundary overlays from each Insulation
            # file.  Never accept several window sizes for one sample because
            # that makes both pairing and the selected range ambiguous.
            boundary_sources = typed("tad_tsv")
            differential_boundaries = [
                item for item in boundary_sources if is_differential_tad_table(path(item))
            ]
            if len(differential_boundaries) > 1:
                raise ValueError(
                    "TAD 差异区域图只能选择一个全局 differential boundary TSV；"
                    "请取消多余的差异结果文件。"
                )
            if differential_boundaries and len(boundary_sources) > 1:
                raise ValueError(
                    "已选择全局 differential boundary TSV，不能再混选各样本的普通 boundaries TSV。"
                )
            if not differential_boundaries:
                if len(boundary_sources) < len(hic_sources):
                    raise ValueError(
                        "TAD 差异区域图需要每个 Hi-C 样本各自对应一个 boundaries TSV，"
                        "或选择一个全局 differential boundary TSV。"
                    )
                # Validates sample pairing and rejects multiple boundary
                # windows belonging to the same sample.
                self._selected_companions(
                    hic_sources, boundary_sources, path, "TAD boundaries"
                )

            options = self._validated_workflow_options(
                figure_type, resolved.get("workflow_options") or {}
            )
            layout = resolved["layout"]
            hics = []
            for index, (source, insulation_path) in enumerate(zip(hic_sources, insulation_paths)):
                hics.append({
                    "file": path(source),
                    "name": sample_names[index],
                    "cmap": options.get("cmap", "Reds"),
                    "triangle_ratio": options.get("triangle_ratio", 0.5),
                    "flip_vertical": bool(index % 2),
                    "color_scale": options.get("color_scale", "linear"),
                    "balance": common["balance"],
                    "resolution": common["resolution"],
                    "insulation_path": insulation_path,
                    "window_size": options.get("window_size", 100_000),
                    "boundary_cmap": options.get("boundary_cmap", "Blues"),
                    "boundary_alpha": options.get("boundary_alpha", 0.9),
                })

            tracks = []
            track_heights = []
            for panel, layer in track_layers:
                source = selected_sources.get(layer.get("source_id"))
                if source is None:
                    continue
                style = layer.get("style", {})
                track = {
                    "file": path(source),
                    "name": None if style.get("show_title") is False else (layer.get("label") or source.get("label")),
                    "color": style.get("color", "#333333"),
                }
                for key in (
                    "alpha", "labels", "fontsize", "gtf_style", "color_utr",
                    "border_color", "color_backbone", "line_width", "plot_type",
                    "min_value", "max_value", "y_scale_group",
                ):
                    if key in style:
                        track[key] = style[key]
                tracks.append(track)
                if layer.get("height_cm") is not None:
                    track_heights.append(float(layer["height_cm"]))
                else:
                    height = panel.get("height_cm", 1.0)
                    track_heights.append(1.0 if height == "auto" else float(height) / max(1, len(panel.get("layers", []))))

            entrypoint = "cfizz.api.quick_plot_integrated"
            kwargs = {
                "hics": hics,
                "tracks": tracks or None,
                "region": GenomeRange(common["chrom"], common["start"], common["end"]),
                "output": output_prefix,
                "n_tracks": len(tracks),
                "track_heights_cm": track_heights or None,
                "width_cm": layout.get("width_cm", 12),
                "gap_cm": layout.get("gap_cm", 0.15),
                "left_margin_cm": layout.get("left_margin_cm", 1.5),
                "right_margin_cm": layout.get("right_margin_cm", 2),
                "font_size": layout.get("font_size", 5),
                "dpi": export.get("dpi", 300),
                "formats": formats,
                "resolution": common["resolution"],
                "balance": common["balance"],
            }
        elif figure_type == "tad_multi":
            if not hic_sources:
                raise ValueError("TAD 对比需要至少一个 cool/mcool。")
            explicit_insulations = typed("insulation_tsv")
            companions = []
            for source in hic_sources:
                source_path = path(source)
                source_sample = re.sub(
                    r"[^a-z0-9]+", "", str(source.get("sample") or Path(source_path).stem).casefold()
                )
                exact = [
                    item for item in explicit_insulations
                    if re.sub(
                        r"[^a-z0-9]+", "", str(item.get("sample") or Path(path(item)).stem).casefold()
                    ) == source_sample
                ]
                matched = exact or [
                    item for item in explicit_insulations
                    if source_sample and source_sample in re.sub(
                        r"[^a-z0-9]+", "", str(item.get("sample") or item.get("label") or Path(path(item)).stem).casefold()
                    )
                ]
                if not matched and len(hic_sources) == 1 and len(explicit_insulations) == 1:
                    matched = explicit_insulations
                if len(matched) > 1:
                    names = "、".join(Path(path(item)).name for item in matched)
                    raise ValueError(f"{Path(source_path).name} 匹配到多个 insulation 文件（{names}）；请在生成前明确确认样本对应关系。")
                if not matched:
                    raise ValueError(
                        f"未找到与 {Path(source_path).name} 匹配的 CFIZZ insulation TSV；"
                        "多样本 TAD 图要求每个 Hi-C 样本绑定自己的 insulation 文件。"
                    )
                explicit_path = path(matched[0])
                companions.append((
                    explicit_path,
                    matched[0].get("resolution") or companion_resolution(explicit_path),
                ))
            insulation_paths = [item[0] for item in companions]
            companion_resolutions = {int(item[1]) for item in companions if item[1]}
            if len(companion_resolutions) > 1:
                values = "、".join(f"{value:,} bp" for value in sorted(companion_resolutions))
                raise ValueError(f"所选 insulation 文件的 bin 宽度不一致（{values}），不能放在同一张 TAD 对比图中。")
            # TAD boundary rows are indexed using the insulation bin width.
            # Using the current Hi-C display resolution here (for example
            # 5 kb with a 10 kb insulation file) makes CFIZZ reject every row
            # with “Interval length must match resolution”.
            if companion_resolutions:
                common["resolution"] = companion_resolutions.pop()
            entrypoint = "cfizz.api.plot_heatmap_with_tad_boundaries"
            kwargs = {
                "mcool_paths": [path(item) for item in hic_sources],
                "insulation_paths": insulation_paths, "sample_names": sample_names,
                "output_path": output_prefix, **common, "dpi": export.get("dpi", 300),
            }
        elif figure_type in {"loop_multi", "loop_diff_region"}:
            if not hic_sources:
                raise ValueError("Loop 区域对比需要 cool/mcool 数据源。")
            explicit_loops = typed("loop_tsv", "bedpe")
            loops_paths = (
                [path(explicit_loops[0])] * len(hic_sources)
                if figure_type == "loop_diff_region" and len(explicit_loops) == 1
                else self._selected_companions(hic_sources, explicit_loops, path, "Loop")
            )
            entrypoint = "cfizz.api.plot_multi_heatmap_with_loops"
            kwargs = {
                "mcool_paths": [path(item) for item in hic_sources],
                "loops_paths": loops_paths, "sample_names": sample_names,
                "output_path": output_prefix, **common, "dpi": export.get("dpi", 300),
            }
        elif figure_type in {"loop_apa_multi", "loop_diff_apa"}:
            if not hic_sources:
                raise ValueError("多样本 APA 需要 cool/mcool 数据源。")
            explicit_loops = typed("loop_tsv", "bedpe")
            loops_paths = (
                [path(explicit_loops[0])] * len(hic_sources)
                if figure_type == "loop_diff_apa" and len(explicit_loops) == 1
                else self._selected_companions(hic_sources, explicit_loops, path, "Loop")
            )
            entrypoint = "cfizz.api.plot_multi_apa_heatmap"
            kwargs = {
                "mcool_paths": [path(item) for item in hic_sources],
                "loops_paths": loops_paths, "sample_names": sample_names,
                "output_path": output_prefix, "resolution": common["resolution"],
                "balance": common["balance"], "dpi": export.get("dpi", 300),
            }
        elif figure_type in {"tad_boundary_pileup", "tad_diff_pileup"}:
            if not hic_sources:
                raise ValueError("TAD boundary pileup 需要 cool/mcool 数据源。")
            explicit_boundaries = typed("tad_tsv")
            boundary_paths = (
                [path(explicit_boundaries[0])] * len(hic_sources)
                if figure_type == "tad_diff_pileup" and explicit_boundaries
                else self._companions(hic_sources, path, "boundaries")
            )
            entrypoint = "cfizz.api.plot_tad_boundary_pileup_from_files"
            kwargs = {
                "mcool_paths": [path(item) for item in hic_sources],
                "boundary_paths": boundary_paths, "sample_names": sample_names,
                "output_path": output_prefix, "resolution": common["resolution"],
                "balance": common["balance"], "dpi": export.get("dpi", 300),
            }
        elif figure_type in {"compartment_multi", "compartment_diff_region"}:
            if not hic_sources:
                raise ValueError("多样本 Compartment 图需要 cool/mcool 数据源用于匹配 E1 和 O/E 结果。")
            eig_paths = self._selected_companions(
                hic_sources, typed("compartment_tsv"), path, "E1"
            )
            oe_paths = self._selected_companions(
                hic_sources, typed("oe_npy"), path, "O/E"
            )
            entrypoint = "cfizz.api.generate_multi_compartment"
            kwargs = {
                "eig_tsv_paths": eig_paths, "oe_npy_paths": oe_paths,
                "output_dir": str(self.output_root), "sample_names": sample_names,
                "chrom": common["chrom"], "resolution": common["resolution"],
                "start_pos": common["start"], "end_pos": common["end"],
                "group_name": output_basename,
            }
            expected = []
        elif figure_type == "compartment_saddle":
            if not hic_sources:
                raise ValueError("Compartment saddle 需要 cool/mcool 和 E1 数据。")
            eig_paths = self._companions(hic_sources, path, "compartment")
            if len(hic_sources) == 1:
                entrypoint = "cfizz.api.generate_single_saddle"
                kwargs = {
                    "cool_file": self._cooler_uri(path(hic_sources[0]), hic_sources[0].get("type"), common["resolution"]), "eigenvector_file": eig_paths[0],
                    "output_dir": str(self.output_root), "sample_name": sample_names[0],
                    "cache_dir": str(self.output_root / Path("cache")), "nproc": 1,
                }
            else:
                entrypoint = "cfizz.api.generate_multi_saddle"
                kwargs = {
                    "cool_files": [self._cooler_uri(path(item), item.get("type"), common["resolution"]) for item in hic_sources],
                    "eigenvector_files": eig_paths, "output_dir": str(self.output_root),
                    "sample_names": sample_names, "cache_dir": str(self.output_root / Path("cache")),
                    "max_workers": 1, "nproc": 1,
                }
            expected = []
        elif figure_type == "compartment_eigenvector":
            eig = typed("compartment_tsv")
            if not eig and hic_sources:
                eig_path = self._companions(hic_sources[:1], path, "compartment")[0]
            elif eig:
                eig_path = path(eig[0])
            else:
                raise ValueError("E1 特征向量轨道需要 E1 TSV。")
            entrypoint = "cfizz.api.plot_eigenvector_from_file"
            kwargs = {
                "eigenvector_path": eig_path, "output": output_prefix,
                "formats": tuple(formats), **{key: common[key] for key in ("chrom", "start", "end", "resolution")},
                "dpi": export.get("dpi", 300),
            }
        elif figure_type in {"tracks_signal", "tracks_genes", "tracks_intervals", "tracks_mixed"}:
            allowed = {
                "tracks_signal": {"bigwig"}, "tracks_genes": {"gtf", "gff"},
                "tracks_intervals": {"bed"}, "tracks_mixed": {"bigwig", "gtf", "gff", "bed"},
            }[figure_type]
            track_sources = [item for item in selected_sources.values() if item.get("type") in allowed]
            if not track_sources:
                raise ValueError("该独立轨道图缺少匹配的 BigWig/GTF/BED 数据源。")
            track_configs = []
            for item in track_sources:
                source_type = "gtf" if item.get("type") == "gff" else item["type"]
                track_configs.append({"file": path(item), "type": source_type, "name": item.get("label")})
            entrypoint = "cfizz.api.plot_track_files"
            kwargs = {
                "tracks": track_configs, "chrom": common["chrom"],
                "start": common["start"], "end": common["end"],
                "output": output_prefix, "width": resolved["layout"].get("width_cm", 12),
                "left_margin": resolved["layout"].get("left_margin_cm", 1.5),
                "right_margin": resolved["layout"].get("right_margin_cm", 2),
                "dpi": export.get("dpi", 300),
            }
            expected = [f"{output_prefix}.{fmt}" for fmt in formats]
        elif figure_type in {"compartment_diff_scatter", "tad_diff_stacked", "loop_diff_stacked"}:
            if figure_type == "compartment_diff_scatter":
                inputs = typed("compartment_tsv")
                entrypoint = "cfizz.api.analyze_compartment_difference"
                names = ("control_e1_path", "treatment_e1_path")
            elif figure_type == "tad_diff_stacked":
                inputs = typed("tad_tsv")
                entrypoint = "cfizz.api.analyze_tad_difference"
                names = ("control_boundaries_path", "treatment_boundaries_path")
            else:
                inputs = typed("loop_tsv", "bedpe")
                entrypoint = "cfizz.api.analyze_loop_difference"
                names = ("control_loops_path", "treatment_loops_path")
            if len(inputs) < 2:
                raise ValueError("差异分析需要明确选择处理组和对照组两个结果文件。")
            kwargs = {
                "comparison": "treatment--control", "output_root": str(self.output_root),
                "run_mode": "all", names[0]: path(inputs[0]), names[1]: path(inputs[1]),
            }
            if figure_type == "tad_diff_stacked":
                kwargs["window_mult"] = 10
            expected = []
        elif figure_type == "tracks_integrated":
            # Integrated direct figures use the same official renderer as an
            # assembled workflow, but retain this compatibility route for
            # older specs that still use the catalogue id.
            delegated = dict(resolved)
            delegated["figure_type"] = "hic_triangle"
            return self.build_request(delegated, inspect_files=False)
        else:
            raise ValueError(
                f"{figure_type} 已登记为 CFIZZ 工作流，但当前数据绑定尚不完整；"
                "请提供目录扫描结果中要求的计算产物。"
            )

        kwargs.update(self._validated_workflow_options(figure_type, resolved.get("workflow_options") or {}))

        return RenderRequest(
            entrypoint=entrypoint, kwargs=kwargs, output_prefix=output_prefix,
            expected_artifacts=expected, validation=validation,
        )

    @staticmethod
    def _companions(hic_sources, path_getter, kind: str) -> List[str]:
        result = []
        for source in hic_sources:
            companion = discover_companion(path_getter(source), kind)
            if companion is None:
                raise ValueError(f"没有找到与 {Path(path_getter(source)).name} 匹配的 {kind} 结果文件。")
            result.append(str(companion.path))
        return result

    @staticmethod
    def _selected_companions(hic_sources, candidates, path_getter, kind: str) -> List[str]:
        """Bind only explicitly selected companion files to Hi-C samples.

        ``prepare_workflow_selection`` rewrites both sides of a confirmed
        pairing to the same sample name.  Matching that value here makes the
        browser confirmation authoritative and prevents the adapter from
        silently rediscovering a different file elsewhere in the directory.
        The containment fallback keeps older programmatic FigureSpecs usable
        when their inferred sample labels still contain product prefixes.
        """
        if not candidates:
            raise ValueError(f"当前工作流没有选择任何 {kind} 文件。")

        def sample_key(source) -> str:
            value = source.get("sample") or source.get("label") or Path(path_getter(source)).stem
            return re.sub(r"[^a-z0-9]+", "", str(value).casefold())

        result: List[str] = []
        used: set[str] = set()
        for source in hic_sources:
            source_path = path_getter(source)
            key = sample_key(source)
            exact = [item for item in candidates if sample_key(item) == key]
            matched = exact or [
                item for item in candidates
                if key and (key in sample_key(item) or sample_key(item) in key)
            ]
            matched = [
                item for item in matched
                if str(Path(path_getter(item)).resolve()).casefold() not in used
            ]
            if len(matched) > 1:
                names = "、".join(Path(path_getter(item)).name for item in matched)
                raise ValueError(
                    f"{Path(source_path).name} 匹配到多个 {kind} 文件（{names}）；"
                    "请在生成前明确确认样本对应关系。"
                )
            if not matched:
                raise ValueError(
                    f"未找到已确认属于 {Path(source_path).name} 的 {kind} 文件；"
                    "请返回文件配对面板检查样本名和对应关系。"
                )
            selected_path = str(Path(path_getter(matched[0])).resolve())
            used.add(selected_path.casefold())
            result.append(selected_path)
        return result

    @staticmethod
    def _validated_workflow_options(figure_type: str, options: Dict[str, Any]) -> Dict[str, Any]:
        """Allow typed scientific parameters, never arbitrary API kwargs."""
        allowed = {
            "hic_multi": {"cmap", "color_scale", "vmin", "vmax", "plot_size"},
            "compartment_multi": {"vmin", "vmax", "plot_size", "bar_height_ratio"},
            "compartment_diff_region": {"vmin", "vmax", "plot_size", "bar_height_ratio"},
            "compartment_saddle": {"n_bins", "contact_type", "heatmap_size", "vmin", "vmax"},
            "tad_multi": {"window_size", "cmap", "vmin", "vmax", "color_scale", "plot_size", "triangle_ratio", "boundary_cmap", "boundary_alpha"},
            "tad_boundary_pileup": {"flank", "vmin", "vmax", "cmap", "method", "color_scale", "top_n", "plot_size"},
            "tad_diff_pileup": {"flank", "vmin", "vmax", "cmap", "method", "color_scale", "top_n", "plot_size"},
            "loop_multi": {"cmap", "vmin", "vmax", "color_scale", "loop_color", "loop_alpha", "loop_size", "plot_size"},
            "loop_diff_region": {"cmap", "vmin", "vmax", "color_scale", "loop_color", "loop_alpha", "loop_size", "plot_size"},
            "loop_apa_multi": {"window", "corner_size", "min_distance", "vmin", "vmax", "cmap", "plot_size"},
            "loop_diff_apa": {"window", "corner_size", "min_distance", "vmin", "vmax", "cmap", "plot_size"},
            "tad_diff_region": {"window_size", "cmap", "color_scale", "triangle_ratio", "boundary_cmap", "boundary_alpha"},
        }.get(figure_type, set())
        result = {}
        for key, value in options.items():
            if key not in allowed:
                raise ValueError(f"{figure_type} 不支持工作流参数 {key!r}。")
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, bool) and key not in {"balance"}:
                raise ValueError(f"工作流参数 {key!r} 的类型无效。")
            result[key] = value
        return result

    @staticmethod
    def _cooler_uri(path: str, source_type: Optional[str], resolution: int) -> str:
        if source_type == "mcool" and "::" not in path:
            return f"{path}::/resolutions/{resolution}"
        return path

    def render(self, spec: Dict[str, Any], inspect_files: bool = True) -> RenderResult:
        request = self.build_request(spec, inspect_files=inspect_files)
        self.output_root.mkdir(parents=True, exist_ok=True)
        try:
            renderer = self.renderer or self._load_renderer(request.entrypoint)
            renderer(**request.kwargs)
        except Exception as exc:
            return RenderResult(False, [], request, f"绘图失败：{exc}")
        artifacts = [path for path in request.expected_artifacts if Path(path).exists()]
        if not request.expected_artifacts:
            # CFIZZ differential-analysis APIs create a documented result
            # directory with several plots instead of one fixed basename.
            artifacts = sorted(
                str(path) for path in self.output_root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".svg", ".png", ".pdf"}
            )
        missing = [path for path in request.expected_artifacts if path not in artifacts]
        if missing:
            names = ", ".join(Path(path).name for path in missing)
            return RenderResult(False, artifacts, request, f"绘图过程结束，但没有生成预期文件：{names}")
        return RenderResult(True, artifacts, request)

    def _build_single_cooler_request(
        self,
        resolved: Dict[str, Any],
        validation: ValidationResult,
        sources: Dict[str, Dict[str, Any]],
        hic_layer: Dict[str, Any],
        figure_type: str,
        resolution: int,
    ) -> RenderRequest:
        entrypoints = {
            "hic_square": "cfizz.api.plot_hic_square",
            "hic_oe": "cfizz.api.plot_hic_oe",
            "compartment": "cfizz.api.plot_hic_compartment",
            "loop_heatmap": "cfizz.api.plot_hic_loops",
            "loop_apa": "cfizz.api.plot_hic_loop_apa",
            "tad_insulation_track": "cfizz.api.plot_tad_insulation_track",
            "tad_boundary_square": "cfizz.api.plot_hic_tad_square",
        }
        if figure_type not in entrypoints:
            raise ValueError(f"图类型 {figure_type!r} 尚未接入渲染器。")
        source = sources[hic_layer["source_id"]]
        source_path = str(self.validator.inspector.resolve_path(source["path"])) if self.validator.inspector else source["path"]
        viewport = resolved["viewport"]
        companion_path = None
        oe_companion_path = None
        if figure_type in {"compartment", "loop_heatmap", "loop_apa", "tad_insulation_track", "tad_boundary_square"}:
            source_types = {
                "compartment": {"compartment_tsv", "tsv"},
                "loop_heatmap": {"loop_tsv", "bedpe"},
                "loop_apa": {"loop_tsv", "bedpe"},
                "tad_insulation_track": {"insulation_tsv"},
                "tad_boundary_square": {"tad_tsv", "tsv"},
            }
            explicit = next((item for item in sources.values() if item.get("type") in source_types[figure_type] and item["id"] != source["id"]), None)
            if explicit:
                companion_path = str(self.validator.inspector.resolve_path(explicit["path"])) if self.validator.inspector else explicit["path"]
                companion_resolution = explicit.get("resolution")
            else:
                kind = (
                    "compartment" if figure_type == "compartment"
                    else "insulation" if figure_type == "tad_insulation_track"
                    else "boundaries" if figure_type == "tad_boundary_square"
                    else "loops"
                )
                companion = discover_companion(source_path, kind)
                if companion is None:
                    requirement = (
                        "预计算 E1 TSV" if kind == "compartment"
                        else "CFIZZ Insulation TSV" if kind == "insulation"
                        else "CFIZZ Boundaries TSV" if kind == "boundaries"
                        else "Loop BEDPE/TSV"
                    )
                    raise ValueError(f"未找到与 {Path(source_path).name} 匹配的{requirement}；请把配套文件放在样本目录或 output 目录。")
                companion_path = str(companion.path)
                companion_resolution = companion.resolution
            if companion_resolution:
                resolution = companion_resolution
            if figure_type == "compartment":
                oe_companion = discover_companion(source_path, "oe", chrom=viewport["chrom"])
                if oe_companion is not None:
                    oe_companion_path = str(oe_companion.path)
        export = resolved["export"]
        output_basename = self._safe_basename(export.get("output_basename", resolved["figure_id"]))
        output_prefix = str(self.output_root / output_basename)
        formats = export.get("formats", ["svg", "png", "pdf"])
        hic_style = hic_layer.get("style", {})
        cmap = hic_style.get("cmap", "Reds")
        if figure_type == "compartment" and cmap == "Reds" and "positive_color" not in hic_style:
            cmap = None  # Preserve cfizz.viz.compartment's canonical blue-white-red default.
        return RenderRequest(
            entrypoint=entrypoints[figure_type],
            kwargs={
                "source_path": source_path,
                "companion_path": companion_path,
                "oe_companion_path": oe_companion_path,
                "chrom": viewport["chrom"],
                "start": viewport["start"],
                "end": viewport["end"],
                "resolution": resolution,
                "balance": resolved["analysis"].get("balance", True),
                "output": output_prefix,
                "dpi": export.get("dpi", 300),
                "formats": formats,
                "cmap": cmap,
                "positive_color": hic_style.get("positive_color", "red"),
                "negative_color": hic_style.get("negative_color", "blue"),
                "vmin": hic_style.get("vmin", -2),
                "vmax": hic_style.get("vmax", 2),
                "plot_size": hic_style.get("plot_size", 4.0),
                "bar_height_ratio": hic_style.get("bar_height_ratio", 0.3),
                "loop_color": hic_style.get("loop_color", "blue"),
                "loop_alpha": hic_style.get("loop_alpha", 0.6),
                "loop_size": hic_style.get("loop_size", 2),
                "window_size": hic_style.get("window_size", 100_000),
                "boundary_color": hic_style.get("boundary_color", "#d62728"),
                "boundary_width": hic_style.get("boundary_width", 2),
                "font_size": resolved["layout"].get("font_size", 5),
            },
            output_prefix=output_prefix,
            expected_artifacts=[f"{output_prefix}.{extension}" for extension in formats],
            validation=validation,
        )

    @staticmethod
    def _load_renderer(entrypoint: str) -> Callable[..., Any]:
        try:
            if not entrypoint.startswith("cfizz.api."):
                raise RuntimeError(f"Agent 只允许调用 CFIZZ 正式 API：{entrypoint}")
            import cfizz.api as public_api
            name = entrypoint.rsplit(".", 1)[-1]
            return getattr(public_api, name)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(f"缺少 cfizz 绘图依赖：{exc}") from exc

    @staticmethod
    def _safe_basename(value: str) -> str:
        candidate = Path(value).name
        safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in candidate)
        if not safe or safe in {".", ".."}:
            raise ValueError("输出文件名无效。")
        return safe
