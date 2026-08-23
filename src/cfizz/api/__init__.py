"""
API module for cfizz package.

High-level API functions for common workflows.
"""

from .quickplot import quick_plot_integrated
from .visualization import (
    plot_hic_compartment,
    plot_hic_loop_apa,
    plot_hic_loops,
    plot_hic_oe,
    plot_hic_square,
    plot_hic_tad_square,
    plot_tad_insulation_track,
)
from .workflows import (
    analyze_compartment_difference,
    analyze_loop_difference,
    analyze_tad_difference,
    generate_multi_compartment,
    generate_multi_heatmap,
    generate_multi_saddle,
    generate_single_saddle,
    plot_bed_tracks,
    plot_bw_tracks,
    plot_eigenvector,
    plot_gtf_tracks,
    plot_heatmap_with_tad_boundaries,
    plot_mixed_tracks,
    plot_multi_apa_heatmap,
    plot_multi_heatmap_with_loops,
    plot_multi_tad_boundary_pileup,
    plot_eigenvector_from_file,
    plot_tad_boundary_pileup_from_files,
    plot_track_files,
)

__all__ = [
    "quick_plot_integrated",
    "plot_hic_square",
    "plot_hic_oe",
    "plot_hic_compartment",
    "plot_hic_loops",
    "plot_hic_loop_apa",
    "plot_tad_insulation_track",
    "plot_hic_tad_square",
    "generate_multi_heatmap",
    "generate_multi_compartment",
    "plot_eigenvector",
    "generate_single_saddle",
    "generate_multi_saddle",
    "plot_heatmap_with_tad_boundaries",
    "plot_multi_tad_boundary_pileup",
    "plot_eigenvector_from_file",
    "plot_tad_boundary_pileup_from_files",
    "plot_track_files",
    "plot_multi_heatmap_with_loops",
    "plot_multi_apa_heatmap",
    "plot_bw_tracks",
    "plot_gtf_tracks",
    "plot_bed_tracks",
    "plot_mixed_tracks",
    "analyze_compartment_difference",
    "analyze_tad_difference",
    "analyze_loop_difference",
]
