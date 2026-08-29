"""Stable public entrypoints for CFIZZ's documented visualization workflows.

These functions are intentionally thin delegates.  They make the historical
``cfizz.viz``/``cfizz.analyze`` examples available through one public API
namespace without changing their rendering code or visual style.
"""

from __future__ import annotations

from typing import Any


def plot_eigenvector_from_file(
    eigenvector_path: str,
    chrom: str,
    start: int,
    end: int,
    resolution: int,
    output: str,
    formats=("svg", "png", "pdf"),
    positive_color: str = "#E41A1C",
    negative_color: str = "#377EB8",
    width_cm: float = 25.4,
    height_cm: float = 5.08,
    dpi: int = 300,
    **kwargs: Any,
):
    """Read a CFIZZ E1 table and render it with the official E1 renderer."""
    import pandas as pd
    from cfizz.viz.compartment import plot_eigenvector

    data = pd.read_csv(eigenvector_path, sep="\t")
    outputs = []
    for fmt in formats:
        path = f"{output}.{fmt}"
        figure = plot_eigenvector(
            data, chrom, start, end, resolution,
            color_up=positive_color,
            color_down=negative_color,
            figsize=(width_cm / 2.54, height_cm / 2.54),
            save_path=path,
            dpi=dpi,
            **kwargs,
        )
        import matplotlib.pyplot as plt
        plt.close(figure)
        outputs.append(path)
    return outputs


def plot_tad_boundary_pileup_from_files(
    mcool_paths,
    boundary_paths,
    output_path: str,
    **kwargs: Any,
):
    """File-oriented delegate for CFIZZ's boundary-pileup renderer."""
    import pandas as pd
    from cfizz.viz.pileup import plot_multi_tad_boundary_pileup

    boundaries = [pd.read_csv(path, sep="\t") for path in boundary_paths]
    return plot_multi_tad_boundary_pileup(
        mcool_paths=mcool_paths,
        boundaries_list=boundaries,
        output_path=output_path,
        **kwargs,
    )


def plot_track_files(
    tracks,
    chrom: str,
    start: int,
    end: int,
    output: str,
    formats=("svg", "png", "pdf"),
    **kwargs: Any,
):
    """Render validated BigWig/GTF/BED configs via CFIZZ ``quick_plot``."""
    from cfizz.api.integrated.tracks.simple import GenomeRange, quick_plot

    outputs = []
    for fmt in formats:
        target = f"{output}.{fmt}"
        quick_plot(
            tracks=tracks,
            region=GenomeRange(chrom, start, end),
            output=target,
            **kwargs,
        )
        outputs.append(target)
    return outputs


def generate_multi_heatmap(*args: Any, **kwargs: Any):
    from cfizz.viz.heatmap import generate_multi_heatmap as render
    return render(*args, **kwargs)


def generate_multi_compartment(*args: Any, **kwargs: Any):
    from cfizz.viz.compartment import generate_multi_compartment as render
    return render(*args, **kwargs)


def plot_eigenvector(*args: Any, **kwargs: Any):
    from cfizz.viz.compartment import plot_eigenvector as render
    return render(*args, **kwargs)


def generate_single_saddle(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.saddle_plot import generate_single_saddle as render
    return render(*args, **kwargs)


def generate_multi_saddle(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.saddle_plot import generate_multi_saddle as render
    return render(*args, **kwargs)


def plot_heatmap_with_tad_boundaries(*args: Any, **kwargs: Any):
    from cfizz.viz.heatmap_tad_ext import plot_heatmap_with_tad_boundaries as render
    return render(*args, **kwargs)


def plot_multi_tad_boundary_pileup(*args: Any, **kwargs: Any):
    from cfizz.viz.pileup import plot_multi_tad_boundary_pileup as render
    return render(*args, **kwargs)


def plot_multi_heatmap_with_loops(*args: Any, **kwargs: Any):
    from cfizz.viz.loop import plot_multi_heatmap_with_loops as render
    return render(*args, **kwargs)


def plot_multi_apa_heatmap(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.apa_pileup import plot_multi_apa_heatmap as render
    return render(*args, **kwargs)


def plot_bw_tracks(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.tracks.simple import plot_bw_tracks as render
    return render(*args, **kwargs)


def plot_gtf_tracks(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.tracks.simple import plot_gtf_tracks as render
    return render(*args, **kwargs)


def plot_bed_tracks(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.tracks.simple import plot_bed_tracks as render
    return render(*args, **kwargs)


def plot_mixed_tracks(*args: Any, **kwargs: Any):
    from cfizz.api.integrated.tracks.simple import plot_mixed_tracks as render
    return render(*args, **kwargs)


def analyze_compartment_difference(*args: Any, **kwargs: Any):
    from cfizz.analyze.compartment import analyze_single_comparison as render
    return render(*args, **kwargs)


def analyze_tad_difference(*args: Any, **kwargs: Any):
    from cfizz.analyze.tad import analyze_single_comparison_window as render
    return render(*args, **kwargs)


def analyze_loop_difference(*args: Any, **kwargs: Any):
    from cfizz.analyze.loop import analyze_single_comparison_loops as render
    return render(*args, **kwargs)
