"""Stable public CFIZZ APIs for single-sample Hi-C visualizations.

These functions perform input/output orchestration only.  All figure creation
is delegated to CFIZZ's existing ``cfizz.viz`` or ``cfizz.api.integrated``
implementations so downstream applications do not invent alternative styles.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Optional, Sequence

import numpy as np


def _read_matrix(source_path: str, chrom: str, start: int, end: int, resolution: int, balance: bool):
    from cfizz.io import read_cooler

    matrix = read_cooler(source_path, resolution=resolution).fetch(chrom, start, end, balance=balance)
    return np.nan_to_num(matrix, nan=0.0)


def _save_cfizz_figure(fig, output: str, dpi: int, formats: Sequence[str]) -> None:
    from cfizz.viz.layout import save_figure_multi_format

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    save_figure_multi_format(fig, output, dpi=dpi, formats=list(formats))


def plot_hic_square(
    *, source_path: str, chrom: str, start: int, end: int, resolution: int,
    balance: bool, output: str, dpi: int = 300, cmap: str = "Reds",
    color_scale: str = "linear", vmin: Optional[float] = None,
    vmax: Optional[float] = None, plot_size: float = 4.0,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render a square contact map with CFIZZ's canonical heatmap API."""
    from cfizz.viz.heatmap import generate_heatmap

    target = Path(output)
    generate_heatmap(
        file_path=source_path,
        output_dir=str(target.parent),
        chrom=chrom,
        resolution=resolution,
        start_pos=start,
        end_pos=end,
        balance=balance,
        color_scale=color_scale,
        vmin=vmin,
        vmax=vmax,
        cmap=cmap,
        plot_size=plot_size,
        output_prefix=target.name,
        formats=tuple(formats),
        dpi=dpi,
    )


def plot_hic_oe(
    *, source_path: str, chrom: str, start: int, end: int, resolution: int,
    balance: bool, output: str, dpi: int = 300, cmap: str = "RdBu_r",
    vmin: float = 0.25, vmax: float = 4, plot_size: float = 4.0,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Calculate O/E and render it with CFIZZ's canonical O/E plot."""
    from cfizz.analyze.oe import calculate_oe_matrix
    from cfizz.viz.heatmap import plot_oe_heatmap

    matrix = _read_matrix(source_path, chrom, start, end, resolution, balance)
    oe, _ = calculate_oe_matrix(matrix)
    fig = plot_oe_heatmap(
        oe, start=start, resolution=resolution, chrom=chrom,
        title="Observed / Expected", cmap=cmap, vmin=vmin, vmax=vmax,
        plot_size=plot_size,
    )
    _save_cfizz_figure(fig, output, dpi, formats)


def plot_hic_compartment(
    *, source_path: str, companion_path: str, chrom: str, start: int, end: int,
    resolution: int, balance: bool, output: str, dpi: int = 300,
    oe_companion_path: Optional[str] = None, cmap=None,
    vmin: float = -2, vmax: float = 2, plot_size: float = 4.0,
    bar_height_ratio: float = 0.3,
    positive_color: str = "red", negative_color: str = "blue",
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render A/B compartments with CFIZZ's canonical compartment API."""
    from cfizz.analyze.oe import get_npy_cache_path, load_or_compute_oe_matrix
    from cfizz.viz.compartment import plot_compartment

    target = Path(output)
    cache_dir = target.parent / ".compartment_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sample_name = Path(source_path).stem
    oe_path = Path(get_npy_cache_path(str(cache_dir), sample_name, chrom, resolution))
    if oe_companion_path:
        supplied = Path(oe_companion_path)
        if supplied.suffix.lower() == ".npy":
            oe_path = supplied
        elif not oe_path.exists():
            np.save(oe_path, np.loadtxt(supplied))
    if not oe_path.exists():
        load_or_compute_oe_matrix(
            mcool_path=source_path, chrom=chrom, resolution=resolution,
            output_dir=str(cache_dir), sample_name=sample_name,
            balance=balance, force_recompute=False,
        )
    result = plot_compartment(
        eig_tsv_path=companion_path,
        oe_npy_path=str(oe_path),
        output_dir=str(cache_dir),
        chrom=chrom,
        start_pos=start,
        end_pos=end,
        resolution=resolution,
        sample_name=sample_name,
        vmin=vmin,
        vmax=vmax,
        plot_size=plot_size,
        bar_height_ratio=bar_height_ratio,
        cmap=cmap,
        positive_color=positive_color,
        negative_color=negative_color,
        formats=tuple(formats),
        dpi=dpi,
    )
    generated_png = Path(result["output_file"])
    generated_prefix = generated_png.with_suffix("")
    generated = [generated_prefix.with_suffix(f".{fmt}") for fmt in formats]
    if result.get("status") != "success" or not all(path.exists() for path in generated):
        raise ValueError("CFIZZ plot_compartment 没有生成完整的图形产物。")
    for fmt, path in zip(formats, generated):
        shutil.copyfile(path, f"{output}.{fmt}")


def plot_hic_loops(
    *, source_path: str, companion_path: str, chrom: str, start: int, end: int,
    resolution: int, balance: bool, output: str, dpi: int = 300,
    cmap: str = "Reds", loop_color: str = "blue", loop_alpha: float = 0.6,
    loop_size: float = 2, color_scale: str = "linear",
    vmin: Optional[float] = None, vmax: Optional[float] = None,
    plot_size: float = 4.0,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render loop calls with CFIZZ's canonical loop heatmap."""
    from cfizz.viz.loop import plot_heatmap_with_loops

    plot_heatmap_with_loops(
        mcool_path=source_path, loops_path=companion_path,
        chrom=chrom, start=start, end=end, resolution=resolution,
        output_path=output, cmap=cmap, balance=balance, dpi=dpi,
        color_scale=color_scale, vmin=vmin, vmax=vmax, plot_size=plot_size,
        loop_color=loop_color, loop_alpha=loop_alpha, loop_size=loop_size,
        formats=list(formats),
    )


def plot_hic_loop_apa(
    *, source_path: str, companion_path: str, resolution: int, balance: bool,
    output: str, dpi: int = 300, cmap: str = "Reds",
    window: int = 5, corner_size: int = 3, min_distance: int = 10,
    vmin: Optional[float] = None, vmax: Optional[float] = None,
    plot_size: float = 4.0,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render loop APA with CFIZZ's canonical integrated APA API."""
    from cfizz.api.integrated.apa_pileup import plot_apa_heatmap

    plot_apa_heatmap(
        mcool_path=source_path, loops_path=companion_path,
        output_path=output, resolution=resolution, balance=balance,
        window=window, corner_size=corner_size, min_distance=min_distance,
        vmin=vmin, vmax=vmax, cmap=cmap, plot_size=plot_size,
        dpi=dpi, formats=list(formats),
    )


def plot_tad_insulation_track(
    *, companion_path: str, chrom: str, start: int, end: int,
    output: str, dpi: int = 300, window_size: int = 100_000,
    color: str = "#1f77b4", boundary_color: str = "#d62728",
    show_boundaries: bool = True, width_cm: float = 25.4,
    height_cm: float = 5.08,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render a standalone insulation track with CFIZZ's TAD API."""
    from cfizz.io.insulation import read_insulation_scores
    from cfizz.viz.tad import plot_insulation_track

    data = read_insulation_scores(
        companion_path, windows=window_size, chrom=chrom, start=start, end=end,
    )
    fig = plot_insulation_track(
        data, chrom=chrom, start=start, end=end, window_size=window_size,
        color=color, color_boundary=boundary_color,
        show_boundaries=show_boundaries,
        figsize=(width_cm / 2.54, height_cm / 2.54), dpi=dpi,
    )
    _save_cfizz_figure(fig, output, dpi, formats)


def plot_hic_tad_square(
    *, source_path: str, companion_path: str, chrom: str, start: int, end: int,
    resolution: int, balance: bool, output: str, dpi: int = 300,
    cmap: str = "Reds", boundary_color: str = "#d62728",
    boundary_width: int = 2, color_scale: str = "linear",
    vmin: Optional[float] = None, vmax: Optional[float] = None,
    plot_size: float = 4.0,
    formats: Sequence[str] = ("png", "svg", "pdf"), **_: object,
) -> None:
    """Render a square Hi-C matrix plus boundary calls using CFIZZ only."""
    import pandas as pd
    from cfizz.viz.tad import plot_tad_heatmap_with_boundaries

    matrix = _read_matrix(source_path, chrom, start, end, resolution, balance)
    boundaries = pd.read_csv(companion_path, sep="\t")
    required = {"chrom", "start", "end"}
    if not required.issubset(boundaries.columns):
        raise ValueError("CFIZZ TAD boundary table must contain chrom/start/end columns.")
    finite = matrix[np.isfinite(matrix)]
    if vmax is None:
        vmax = float(np.percentile(finite[finite > 0], 95)) if np.any(finite > 0) else 1.0
    if vmin is None:
        vmin = 0.0
    fig = plot_tad_heatmap_with_boundaries(
        matrix, boundaries, resolution=resolution, chrom=chrom, start=start, end=end,
        vmin=vmin, vmax=vmax, cmap=cmap, color_boundary=boundary_color,
        boundary_width=boundary_width, color_scale=color_scale,
        figsize=(plot_size, plot_size), dpi=dpi,
    )
    _save_cfizz_figure(fig, output, dpi, formats)
