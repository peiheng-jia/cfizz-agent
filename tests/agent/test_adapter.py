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

    def test_render_fails_when_renderer_does_not_write_artifacts(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        validator = FigureSpecValidator(inspector)

        with tempfile.TemporaryDirectory() as output_dir:
            adapter = CfizzRenderAdapter(validator, output_dir, renderer=lambda **kwargs: None)
            result = adapter.render(self.spec, inspect_files=False)

        self.assertFalse(result.success)
        self.assertIn("没有生成预期文件", result.error)

    def test_routes_non_triangle_figure_types_to_registered_renderers(self):
        expected = {
            "hic_square": "plot_hic_square",
            "hic_oe": "plot_hic_oe",
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
        spec["workflow_options"] = {
            "loop_size": 12,
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
        self.assertEqual(request.kwargs["loop_size"], 12)
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
                ("tad_insulation_track", "plot_tad_insulation_track", 10_000),
                ("tad_boundary_square", "plot_hic_tad_square", 10_000),
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
        self.assertEqual(request.entrypoint, "cfizz.api.plot_heatmap_with_tad_boundaries")
        self.assertEqual(request.kwargs["resolution"], 10_000)
        self.assertEqual(
            [Path(path).resolve() for path in request.kwargs["insulation_paths"]],
            [path.resolve() for path in explicit_insulations],
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
