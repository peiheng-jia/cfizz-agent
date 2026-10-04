import json
from pathlib import Path
import re
import tempfile
import unittest

from cfizz.agent import CfizzRenderAdapter, DataInspector, FigureSpecValidator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SPEC = PROJECT_ROOT / "docs" / "examples" / "figure-spec.integrated-demo.json"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        with EXAMPLE_SPEC.open("r", encoding="utf-8") as handle:
            self.spec = json.load(handle)
        self.spec["analysis"]["resolution"] = 10_000

    def test_builds_allow_listed_render_request(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir)
            request = adapter.build_request(self.spec, inspect_files=False)

        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(len(request.kwargs["hics"]), 2)
        self.assertEqual(len(request.kwargs["tracks"]), 10)
        self.assertEqual(request.kwargs["region"].chrom, "chr17")
        self.assertEqual(request.kwargs["font_size"], 5)
        self.assertEqual(request.kwargs["width_cm"], 8)
        self.assertEqual(request.kwargs["gap_cm"], 0.1)
        self.assertEqual(request.kwargs["track_heights_cm"], [0.5] + [0.8] * 9)
        self.assertEqual(request.kwargs["hics"][1]["cmap"], "Reds")
        self.assertEqual(request.kwargs["hics"][1]["triangle_ratio"], 1)
        self.assertEqual(request.kwargs["tracks"][0]["labels"], True)
        self.assertEqual(request.kwargs["tracks"][1]["labels"], False)
        self.assertEqual(request.kwargs["tracks"][-1]["max_value"], 10)

    def test_render_supports_injected_renderer_and_collects_artifacts(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)

        with tempfile.TemporaryDirectory() as output_dir:
            def fake_renderer(**kwargs):
                Path(f"{kwargs['output']}.svg").touch()
                Path(f"{kwargs['output']}.png").touch()

            adapter = CfizzRenderAdapter(validator, output_dir, renderer=fake_renderer)
            result = adapter.render(self.spec, inspect_files=False)

        self.assertTrue(result.success)
        self.assertEqual(len(result.artifacts), 2)

    def test_render_reports_real_pipeline_stages(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        stages = []

        with tempfile.TemporaryDirectory() as output_dir:
            def fake_renderer(**kwargs):
                Path(f"{kwargs['output']}.svg").touch()
                Path(f"{kwargs['output']}.png").touch()

            adapter = CfizzRenderAdapter(validator, output_dir, renderer=fake_renderer)
            result = adapter.render(
                self.spec,
                inspect_files=False,
                progress_callback=lambda stage, progress: stages.append((stage, progress)),
            )

        self.assertTrue(result.success)
        self.assertEqual(stages[0], ("validating_inputs", 6))
        self.assertIn(("loading_renderer", 18), stages)
        self.assertIn(("reading_and_rendering", 25), stages)
        self.assertEqual(stages[-1], ("publishing_artifacts", 98))

    def test_render_fails_when_renderer_does_not_write_artifacts(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)

        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir, renderer=lambda **kwargs: None)
            result = adapter.render(self.spec, inspect_files=False)

        self.assertFalse(result.success)
        self.assertIn("没有生成预期文件", result.error)

    def test_render_propagates_structured_renderer_error(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)

        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(
                validator,
                output_dir,
                renderer=lambda **kwargs: {
                    "status": "error",
                    "error": "matrix and E1 resolutions do not match",
                },
            )
            result = adapter.render(self.spec, inspect_files=False)

        self.assertFalse(result.success)
        self.assertIn("matrix and E1 resolutions do not match", result.error)

    def test_render_without_declared_outputs_requires_at_least_one_artifact(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)

        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir, renderer=lambda **kwargs: None)
            request = adapter.build_request(self.spec, inspect_files=False)
            request.expected_artifacts = []
            adapter.build_request = lambda spec, inspect_files=True: request
            result = adapter.render(self.spec, inspect_files=False)

        self.assertFalse(result.success)
        self.assertIn("没有生成任何", result.error)

    def test_routes_non_triangle_figure_types_to_registered_renderers(self):
        expected = {
            "hic_square": "plot_hic_square",
        }
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir)
            for figure_type, suffix in expected.items():
                spec = json.loads(json.dumps(self.spec))
                spec["figure_type"] = figure_type
                request = adapter.build_request(spec, inspect_files=False)
                self.assertTrue(request.entrypoint.startswith("cfizz.api."))
                self.assertNotIn("cfizz.agent.renderers", request.entrypoint)
                self.assertTrue(request.entrypoint.endswith(suffix))
                self.assertTrue(request.kwargs["source_path"].endswith("hiPSC_nor_chr17.mcool"))

    def test_single_heatmap_registered_scale_options_reach_public_api(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "hic_square"
        layer = next(
            layer for panel in spec["panels"] for layer in panel["layers"]
            if layer["kind"] == "hic"
        )
        layer.setdefault("style", {}).update({
            "cmap": "viridis",
            "color_scale": "log",
            "vmin": 0.01,
            "vmax": 5,
            "plot_size": 6,
        })
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(
                spec, inspect_files=False,
            )
        self.assertEqual(request.kwargs["cmap"], "viridis")
        self.assertEqual(request.kwargs["color_scale"], "log")
        self.assertEqual(request.kwargs["vmin"], 0.01)
        self.assertEqual(request.kwargs["vmax"], 5)
        self.assertEqual(request.kwargs["plot_size"], 6)

    def test_standalone_track_workflow_forwards_registered_layer_styles(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "tracks_signal"
        layer = next(
            layer for panel in spec["panels"] for layer in panel["layers"]
            if layer["kind"] == "bigwig"
        )
        layer["visible"] = True
        layer["height_cm"] = 1.7
        layer.setdefault("style", {}).update({
            "color": "#0072B2",
            "alpha": 0.4,
            "plot_type": "line",
            "line_width": 1.25,
            "number_of_bins": 900,
            "summary_method": "max",
            "min_value": -1,
            "max_value": 8,
        })
        spec["workflow_source_ids"] = [layer["source_id"]]
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(
                spec, inspect_files=False,
            )
        self.assertEqual(request.entrypoint, "cfizz.api.plot_track_files")
        self.assertEqual(len(request.kwargs["tracks"]), 1)
        track = request.kwargs["tracks"][0]
        for key in (
            "color", "alpha", "plot_type", "line_width", "number_of_bins",
            "summary_method", "min_value", "max_value",
        ):
            self.assertEqual(track[key], layer["style"][key])
        self.assertEqual(request.kwargs["track_heights"], [1.7])
        self.assertEqual(request.kwargs["formats"], ("svg", "png"))

    def test_multi_hic_workflow_binds_to_official_cfizz_api(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "hic_multi"
        spec["workflow_source_ids"] = ["hic_normal", "hic_variant"]
        spec["workflow_options"] = {"cmap": "Reds", "plot_size": 4}
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec, inspect_files=False)
        self.assertEqual(request.entrypoint, "cfizz.api.generate_multi_heatmap")
        self.assertEqual(len(request.kwargs["file_paths"]), 2)
        self.assertEqual(request.kwargs["formats"], ("svg", "png"))
        self.assertEqual(request.kwargs["plot_size"], 4)

    def test_compartment_diff_scatter_forwards_palette_to_cfizz(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "compartment_diff_scatter"
        spec["data_sources"].extend([
            {
                "id": "control_e1", "type": "compartment_tsv",
                "path": "demo/results/control.100kb.E1.tsv", "sample": "control",
            },
            {
                "id": "treatment_e1", "type": "compartment_tsv",
                "path": "demo/results/treatment.100kb.E1.tsv", "sample": "treatment",
            },
        ])
        spec["workflow_source_ids"] = ["control_e1", "treatment_e1"]
        spec["workflow_options"] = {
            "stable_a_color": "#0072B2",
            "stable_b_color": "#009E73",
            "a_to_b_color": "#E69F00",
            "b_to_a_color": "#D55E00",
            "control_density_color": "#56B4E9",
            "treatment_density_color": "#CC79A7",
            "point_size": 2.5,
            "point_alpha": 0.65,
            "density_alpha": 0.35,
            "width_cm": 9,
            "height_cm": 7,
            "font_size": 8,
            "show_counts": False,
        }
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(
                spec, inspect_files=False,
            )

        self.assertEqual(request.entrypoint, "cfizz.api.analyze_compartment_difference")
        self.assertTrue(request.kwargs["control_e1_path"].endswith("control.100kb.E1.tsv"))
        self.assertTrue(request.kwargs["treatment_e1_path"].endswith("treatment.100kb.E1.tsv"))
        for key, value in spec["workflow_options"].items():
            self.assertEqual(request.kwargs[key], value)

    def test_loop_workflow_forwards_marker_options_to_official_cfizz_api(self):
        """Loop marker edits must reach CFIZZ as loop_size, not triangle_ratio."""
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "loop_multi"
        spec["data_sources"].extend([
            {
                "id": "loops_normal",
                "type": "loop_tsv",
                "path": "demo/data/hiPSC_nor.loops.bedpe",
                "sample": "hiPSC_nor",
                "label": "Normal loops",
            },
            {
                "id": "loops_variant",
                "type": "loop_tsv",
                "path": "demo/data/hiPSC_var.loops.bedpe",
                "sample": "hiPSC_var",
                "label": "Variant loops",
            },
        ])
        spec["workflow_source_ids"] = [
            "hic_normal", "hic_variant", "loops_normal", "loops_variant",
        ]
        spec["workflow_options"] = {}
        with tempfile.TemporaryDirectory() as output_dir:
            default_request = CfizzRenderAdapter(validator, output_dir).build_request(
                spec, inspect_files=False,
            )
        self.assertEqual(default_request.kwargs["loop_size"], 12)

        spec["workflow_options"] = {
            "loop_size": 20,
            "loop_color": "green",
            "loop_alpha": 0.4,
            "plot_size": 5,
        }
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(
                spec, inspect_files=False,
            )

        self.assertEqual(request.entrypoint, "cfizz.api.plot_multi_heatmap_with_loops")
        self.assertEqual(request.kwargs["loops_paths"], [
            str((PROJECT_ROOT / "demo/data/hiPSC_nor.loops.bedpe").resolve()),
            str((PROJECT_ROOT / "demo/data/hiPSC_var.loops.bedpe").resolve()),
        ])
        self.assertEqual(request.kwargs["loop_size"], 20)
        self.assertEqual(request.kwargs["loop_color"], "green")
        self.assertEqual(request.kwargs["loop_alpha"], 0.4)
        self.assertEqual(request.kwargs["plot_size"], 5)
        self.assertNotIn("triangle_ratio", request.kwargs)

    def test_multi_hic_triangle_uses_official_integrated_renderer(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "hic_triangle_multi"
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec, inspect_files=False)
        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(len(request.kwargs["hics"]), 2)
        self.assertEqual(request.kwargs["hics"][0]["triangle_ratio"], 1)
        self.assertTrue(request.kwargs["hics"][1]["flip_vertical"])

    def test_integrated_tracks_keep_annotations_above_signals_after_late_panel_addition(self):
        """A later gene-panel patch must not place genes below BigWig tracks."""
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        # Simulate the conversational edit that used to cause the bug:
        # signal_panel exists first and annotation_panel is appended later.
        spec["panels"] = [spec["panels"][0], spec["panels"][2], spec["panels"][1]]
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec, inspect_files=False)

        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertTrue(request.kwargs["tracks"][0]["file"].endswith("FOXJ1.gtf"))
        self.assertTrue(request.kwargs["tracks"][1]["file"].endswith("enhancer.chr17_75.4-76.34M.bed"))
        self.assertTrue(request.kwargs["tracks"][2]["file"].endswith("hiPSC_nor_ATAC-Seq_chr17_mean.bw"))

    def test_integrated_workflow_delegates_to_official_cfizz_renderer(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "tracks_integrated"
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec, inspect_files=False)
        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(len(request.kwargs["hics"]), 2)
        self.assertGreaterEqual(len(request.kwargs["tracks"]), 1)

    def test_integrated_function_binds_inspected_loop_and_tad_inputs_per_sample(self):
        from cfizz.agent.figure_types import FIGURE_TYPE_BY_ID

        capability = FIGURE_TYPE_BY_ID["tracks_integrated"]["function_inputs"]
        self.assertEqual(set(capability["hic_overlays"]), {"loops_path", "insulation_path"})
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "tracks_integrated"
        with tempfile.TemporaryDirectory() as root:
            for sample in ("hiPSC_nor", "hiPSC_var"):
                loop = Path(root) / f"{sample}.bedpe"
                loop.write_text("chr17\t75400000\t75410000\tchr17\t75500000\t75510000\n", encoding="utf-8")
                insulation = Path(root) / f"{sample}.insulation.tsv"
                insulation.write_text(
                    "chrom\tstart\tend\tlog2_insulation_score_50000\tis_boundary_50000\t"
                    "log2_insulation_score_100000\tis_boundary_100000\n"
                    "chr17\t75400000\t75410000\t0.2\tFalse\t0.3\tTrue\n",
                    encoding="utf-8",
                )
                spec["data_sources"].extend([
                    {"id": f"loop_{sample}", "type": "bedpe", "role": "loops", "sample": sample, "path": str(loop)},
                    {"id": f"tad_{sample}", "type": "insulation_tsv", "role": "insulation", "sample": sample, "path": str(insulation)},
                ])
            inspector = DataInspector([str(PROJECT_ROOT), root])
            request = CfizzRenderAdapter(FigureSpecValidator(inspector), root).build_request(spec, inspect_files=False)
            self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
            self.assertEqual(len(request.kwargs["tracks"]), 10)
            self.assertEqual(
                {Path(hic["loops_path"]).name for hic in request.kwargs["hics"]},
                {"hiPSC_nor.bedpe", "hiPSC_var.bedpe"},
            )
            for hic in request.kwargs["hics"]:
                self.assertEqual(hic["window_size"], 100_000)
                self.assertTrue(Path(hic["insulation_path"]).exists())

    def test_discovers_case_companions_and_uses_their_resolution(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        source = "demo/cases/2121401/data/51_5_1000.mcool"
        if not (PROJECT_ROOT / source).exists():
            self.skipTest("local case 2121401 is not installed")
        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir)
            for figure_type, suffix, resolution in (
                ("tad_insulation", "quick_plot_integrated", 10_000),
                ("compartment", "plot_hic_compartment", 100_000),
                ("loop_heatmap", "plot_hic_loops", 10_000),
                ("loop_apa", "plot_hic_loop_apa", 10_000),
            ):
                spec = json.loads(json.dumps(self.spec))
                spec["data_sources"][0]["path"] = source
                spec["data_sources"] = [spec["data_sources"][0]]
                spec["panels"] = [{"id": "hic_panel", "kind": "hic_heatmap", "layers": [spec["panels"][0]["layers"][0]]}]
                spec["figure_type"] = figure_type
                request = adapter.build_request(spec)
                self.assertTrue(request.entrypoint.startswith("cfizz.api."))
                self.assertTrue(request.entrypoint.endswith(suffix))
                self.assertEqual(request.kwargs["resolution"], resolution)
                if figure_type == "tad_insulation":
                    self.assertTrue(Path(request.kwargs["hics"][0]["insulation_path"]).exists())
                    self.assertEqual(request.kwargs["hics"][0]["triangle_ratio"], 1)
                    self.assertEqual(request.kwargs["hics"][0]["window_size"], 100_000)
                    self.assertEqual(request.kwargs["hics"][0]["boundary_cmap"], "Blues_r")
                else:
                    self.assertTrue(Path(request.kwargs["companion_path"]).exists())

    def test_tad_multi_uses_insulation_bin_width_instead_of_current_heatmap_resolution(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        paths = [
            "demo/cases/2121401/1_2_pairs_result/51_5/51_5_1000.mcool",
            "demo/cases/2121401/1_2_pairs_result/51_6/51_6_1000.mcool",
        ]
        if any(not (PROJECT_ROOT / path).exists() for path in paths):
            self.skipTest("local case 2121401 is not installed")
        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "tad_multi"
        spec["analysis"]["resolution"] = 5_000
        hic_sources = [source for source in spec["data_sources"] if source["type"] in {"cool", "mcool"}]
        hic_layers = [layer for panel in spec["panels"] for layer in panel["layers"] if layer["kind"] == "hic"]
        for source, layer, path in zip(hic_sources, hic_layers, paths):
            source["path"] = path
            source["sample"] = Path(path).stem
            layer["source_id"] = source["id"]
        spec["data_sources"] = hic_sources
        explicit_insulations = [
            PROJECT_ROOT / "demo/cases/2121401/1_3_hicviz_output/1_computation/tad/51_5/1_0.51_5_1000.10000.insulation.tsv",
            PROJECT_ROOT / "demo/cases/2121401/1_3_hicviz_output/1_computation/tad/51_6/1_0.51_6_1000.10000.insulation.tsv",
        ]
        for index, insulation_path in enumerate(explicit_insulations, 1):
            spec["data_sources"].append({
                "id": f"insulation_{index}",
                "type": "insulation_tsv",
                "path": str(insulation_path),
                "sample": insulation_path.stem,
                "label": insulation_path.stem,
            })
        spec["panels"] = [{"id": "hic_panel", "kind": "hic_heatmap", "layers": hic_layers}]
        spec["workflow_source_ids"] = [source["id"] for source in spec["data_sources"]]
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec)
        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(request.kwargs["resolution"], 10_000)
        self.assertEqual(
            [Path(hic["insulation_path"]).resolve() for hic in request.kwargs["hics"]],
            [path.resolve() for path in explicit_insulations],
        )
        self.assertEqual([hic["resolution"] for hic in request.kwargs["hics"]], [10_000, 10_000])
        self.assertEqual([hic["window_size"] for hic in request.kwargs["hics"]], [100_000, 100_000])
        self.assertEqual([hic["triangle_ratio"] for hic in request.kwargs["hics"]], [0.5, 0.5])
        self.assertEqual([hic["flip_vertical"] for hic in request.kwargs["hics"]], [False, True])
        self.assertEqual(request.kwargs["tracks"], [])
        self.assertEqual(request.kwargs["n_tracks"], 0)
        self.assertEqual(request.kwargs["region"].chrom, "chr17")
        self.assertNotIn("plot_size", request.kwargs)
        spec["workflow_options"] = {"window_size": 50_000, "boundary_cmap": "Greys"}
        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec)
        self.assertEqual([hic["window_size"] for hic in request.kwargs["hics"]], [50_000, 50_000])
        self.assertEqual([hic["boundary_cmap"] for hic in request.kwargs["hics"]], ["Greys", "Greys"])
        self.assertNotIn("window_size", request.kwargs)
        spec["workflow_options"] = {"window_size": 200_000}
        with tempfile.TemporaryDirectory() as output_dir:
            with self.assertRaisesRegex(ValueError, "可选窗口：50 kb、100 kb、500 kb"):
                CfizzRenderAdapter(validator, output_dir).build_request(spec)

    def test_saddle_uses_e1_grid_before_starting_the_expensive_workflow(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        hic_path = PROJECT_ROOT / "demo/cases/2121401/1_2_pairs_result/51_5/51_5_1000.mcool"
        e1_path = PROJECT_ROOT / (
            "demo/cases/2121401/1_3_hicviz_output/1_computation/compartment/"
            "1_1.51_5_1000.51_5_1000.100kb.E1.tsv"
        )
        if not hic_path.exists() or not e1_path.exists():
            self.skipTest("local Saddle inputs are not installed")

        spec = json.loads(json.dumps(self.spec))
        hic_source = next(source for source in spec["data_sources"] if source["type"] == "mcool")
        hic_source.update({
            "path": str(hic_path),
            "sample": "51_5_1000",
            "label": "51_5_1000",
        })
        hic_layer = next(
            layer for panel in spec["panels"] for layer in panel["layers"]
            if layer["kind"] == "hic" and layer["source_id"] == hic_source["id"]
        )
        spec["data_sources"] = [
            hic_source,
            {
                "id": "compartment_1",
                "type": "compartment_tsv",
                "path": str(e1_path),
                "sample": "51_5_1000",
                "label": "51_5_1000 E1",
                "role": "compartment",
            },
        ]
        spec["panels"] = [{
            "id": "hic_panel", "kind": "hic_heatmap", "layers": [hic_layer],
        }]
        spec["figure_type"] = "compartment_saddle"
        spec["analysis"]["resolution"] = 5_000
        spec["workflow_source_ids"] = [hic_source["id"], "compartment_1"]

        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec)

        self.assertEqual(request.entrypoint, "cfizz.api.generate_single_saddle")
        self.assertTrue(request.kwargs["cool_file"].endswith("::/resolutions/100000"))
        self.assertEqual(request.kwargs["output_prefix"], request.output_prefix)
        self.assertEqual(
            request.expected_artifacts,
            [f"{request.output_prefix}.{extension}" for extension in spec["export"]["formats"]],
        )

    def test_tad_diff_region_accepts_one_ordinary_boundary_grid_per_hic(self):
        """The differential workflow may use paired ordinary boundary grids.

        The optional global differential TSV is not present in every
        experiment directory.  In that case the viewport selector compares
        the two selected boundary grids, while the official integrated CFIZZ
        renderer still receives the paired insulation files for the actual
        figure.
        """
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)
        paths = [
            "demo/cases/2121401/1_2_pairs_result/51_5/51_5_1000.mcool",
            "demo/cases/2121401/1_2_pairs_result/51_6/51_6_1000.mcool",
        ]
        if any(not (PROJECT_ROOT / path).exists() for path in paths):
            self.skipTest("local case 2121401 is not installed")

        spec = json.loads(json.dumps(self.spec))
        spec["figure_type"] = "tad_diff_region"
        spec["analysis"]["resolution"] = 5_000
        hic_sources = [
            source for source in spec["data_sources"]
            if source["type"] in {"cool", "mcool"}
        ]
        hic_layers = [
            layer for panel in spec["panels"] for layer in panel["layers"]
            if layer["kind"] == "hic"
        ]
        for source, layer, path in zip(hic_sources, hic_layers, paths):
            source["path"] = path
            source["sample"] = Path(path).parent.name
            layer["source_id"] = source["id"]
        spec["data_sources"] = hic_sources

        auxiliary = []
        for index, (sample, resolution) in enumerate((("51_5", "51_5"), ("51_6", "51_6")), 1):
            insulation_path = PROJECT_ROOT / (
                "demo/cases/2121401/1_3_hicviz_output/1_computation/tad"
                f"/{sample}/1_0.{resolution}_1000.10000.insulation.tsv"
            )
            boundary_path = PROJECT_ROOT / (
                "demo/cases/2121401/1_3_hicviz_output/1_computation/tad"
                f"/{sample}/2_0.{resolution}_1000.10000.10b.boundaries.tsv"
            )
            if not insulation_path.exists() or not boundary_path.exists():
                self.skipTest("local TAD companion files are not installed")
            auxiliary.extend([
                {
                    "id": f"insulation_{index}", "type": "insulation_tsv",
                    "path": str(insulation_path), "sample": sample,
                    "label": insulation_path.name,
                },
                {
                    "id": f"boundaries_{index}", "type": "tad_tsv",
                    "path": str(boundary_path), "sample": sample,
                    "label": boundary_path.name,
                },
            ])
        spec["data_sources"].extend(auxiliary)
        spec["panels"] = [{"id": "hic_panel", "kind": "hic_heatmap", "layers": hic_layers}]
        spec["workflow_source_ids"] = [source["id"] for source in spec["data_sources"]]

        with tempfile.TemporaryDirectory() as output_dir:
            request = CfizzRenderAdapter(validator, output_dir).build_request(spec)

        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(request.kwargs["resolution"], 10_000)
        self.assertEqual(len(request.kwargs["hics"]), 2)
        self.assertEqual(
            [Path(item["insulation_path"]).name for item in request.kwargs["hics"]],
            [
                "1_0.51_5_1000.10000.insulation.tsv",
                "1_0.51_6_1000.10000.insulation.tsv",
            ],
        )

    def test_confirmed_companions_are_bound_by_sample_not_candidate_order(self):
        hics = [
            {"path": "/experiment/A.mcool", "sample": "A"},
            {"path": "/experiment/B.mcool", "sample": "B"},
        ]
        companions = [
            {"path": "/experiment/B.loops.txt", "sample": "B"},
            {"path": "/experiment/A.loops.txt", "sample": "A"},
        ]
        result = CfizzRenderAdapter._selected_companions(
            hics, companions, lambda item: item["path"], "Loop"
        )
        self.assertEqual(
            result,
            [
                str(Path("/experiment/A.loops.txt").resolve()),
                str(Path("/experiment/B.loops.txt").resolve()),
            ],
        )

    def test_agent_renderer_entrypoints_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "只允许调用 CFIZZ 正式 API"):
            CfizzRenderAdapter._load_renderer("cfizz.agent.renderers.render_square_heatmap")

    def test_agent_and_public_wrappers_do_not_draw_with_matplotlib(self):
        """The Agent may orchestrate CFIZZ, but must never become a renderer."""
        sources = [
            *(PROJECT_ROOT / "src" / "cfizz" / "agent").glob("*.py"),
            PROJECT_ROOT / "src" / "cfizz" / "api" / "visualization.py",
        ]
        forbidden = re.compile(r"(?:import matplotlib|from matplotlib|plt\.|\.imshow\(|\.subplots\()")
        violations = [
            str(path.relative_to(PROJECT_ROOT))
            for path in sources
            if forbidden.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(violations, [], f"Agent 层发现自行绘图代码：{violations}")


if __name__ == "__main__":
    unittest.main()
