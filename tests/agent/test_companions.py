from pathlib import Path

from cfizz.agent.companions import (
    is_differential_tad_table,
    suggest_selected_viewport,
)


DEMO = Path(__file__).parents[2] / "demo" / "cases" / "2121401"


def test_differential_tad_table_is_distinguished_from_ordinary_boundary_grid():
    ordinary = next(
        (DEMO / "1_3_hicviz_output" / "1_computation" / "tad" / "51_5").glob("*.boundaries.tsv")
    )
    differential = next(
        (DEMO / "1_4_diff_result" / "51_5--51_6" / "tad_boundary" / "10b").glob("*classification_final.tsv")
    )
    assert not is_differential_tad_table(ordinary)
    assert is_differential_tad_table(differential)


def test_tad_diff_viewport_uses_selected_ordinary_sample_boundaries():
    # Keep the fixture lookup explicit.  The demo contains large auxiliary
    # directories; a glob needlessly materialises every directory entry and
    # can fail under the memory limit of a small CI runner.
    first = DEMO / "1_3_hicviz_output" / "1_computation" / "tad" / "51_5" / "2_0.51_5_1000.10000.10b.boundaries.tsv"
    second = DEMO / "1_3_hicviz_output" / "1_computation" / "tad" / "51_6" / "2_0.51_6_1000.10000.10b.boundaries.tsv"
    suggestion = suggest_selected_viewport(
        [first, second],
        "chr1",
        0,
        2_000_000,
        kind="boundaries",
        figure_type="tad_diff_region",
        resolution=10_000,
    )
    assert suggestion.method == "derived_event_range"
    assert suggestion.start % 10_000 == 0
    assert suggestion.end > suggestion.start
    assert suggestion.end - suggestion.start < 20_000_000


def test_compartment_diff_viewport_uses_changed_e1_blocks():
    first = DEMO / "1_3_hicviz_output" / "1_computation" / "compartment" / "1_1.51_5_1000.51_5_1000.100kb.E1.tsv"
    second = DEMO / "1_3_hicviz_output" / "1_computation" / "compartment" / "1_1.51_6_1000.51_6_1000.100kb.E1.tsv"
    suggestion = suggest_selected_viewport(
        [first, second],
        "chr1",
        0,
        2_000_000,
        kind="compartment",
        figure_type="compartment_diff_region",
        resolution=100_000,
    )
    assert suggestion.method == "derived_event_range"
    assert suggestion.start % 100_000 == 0
    assert suggestion.end > suggestion.start
