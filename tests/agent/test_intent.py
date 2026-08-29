import json
from pathlib import Path
import unittest

from cfizz.agent.intent import SimpleIntentInterpreter


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


class IntentInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.interpreter = SimpleIntentInterpreter()
        self.spec = load_spec()

    def test_parses_human_readable_region(self):
        result = self.interpreter.interpret("放大到 chr17:75.9M-76.3M", self.spec)
        self.assertEqual(result.action, "patch")
        values = [item["value"] for item in result.patch["operations"]]
        self.assertEqual(values, ["chr17", 75_900_000, 76_300_000])

    def test_scientific_change_requires_confirmation(self):
        result = self.interpreter.interpret("分辨率改成 100k", self.spec)
        self.assertTrue(result.requires_confirmation)
        self.assertEqual(result.patch["operations"][0]["value"], 100_000)

    def test_updates_named_track_color(self):
        result = self.interpreter.interpret("把 ATAC normal 改成绿色", self.spec)
        operation = result.patch["operations"][0]
        self.assertEqual(operation["target_id"], "atac_normal_layer")
        self.assertEqual(operation["value"], "#009E73")

    def test_ambiguous_track_requests_clarification(self):
        result = self.interpreter.interpret("把 ATAC 改成蓝色", self.spec)
        self.assertEqual(result.action, "clarify")

    def test_recognizes_undo(self):
        self.assertEqual(self.interpreter.interpret("撤销", self.spec).action, "undo")

    def test_increases_rendered_figure_font_size(self):
        result = self.interpreter.interpret("把图里的字体调大一点", self.spec)
        operation = result.patch["operations"][0]
        self.assertEqual(operation["target_kind"], "layout")
        self.assertEqual(operation["field"], "font_size")
        self.assertEqual(operation["value"], 6.2)

    def test_sets_explicit_rendered_figure_font_size(self):
        result = self.interpreter.interpret("图中文字调到 8 pt", self.spec)
        self.assertEqual(result.patch["operations"][0]["value"], 8.0)

    def test_compact_font_request_uses_active_workflow_parameter_and_clamps(self):
        spec = load_spec()
        spec["figure_type"] = "compartment_diff_scatter"
        spec["workflow_options"] = {"font_size": 6}

        result = self.interpreter.interpret("字体换到2pt", spec)

        operation = result.patch["operations"][0]
        self.assertEqual(result.action, "patch")
        self.assertEqual(operation["target_kind"], "figure")
        self.assertEqual(operation["field"], "workflow_options.font_size")
        self.assertEqual(operation["value"], 3.0)
        self.assertIn("请求为 2 pt", result.reply)
        self.assertIn("采用最近可用值 3 pt", result.reply)

    def test_infers_annotation_panel_for_overlapping_labels(self):
        result = self.interpreter.interpret("有的标签重叠了", self.spec)
        self.assertEqual(result.action, "patch")
        operation = result.patch["operations"][0]
        self.assertEqual(operation["target_id"], "annotation_panel")
        self.assertEqual(operation["field"], "height_cm")
        self.assertGreater(operation["value"], 1.2)

    def test_increases_right_margin_for_clipped_rightmost_label(self):
        result = self.interpreter.interpret("最右边的标签截断了，需要处理一下", self.spec)
        self.assertEqual(result.action, "patch")
        operation = result.patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["field"]), ("layout", "right_margin_cm"))
        self.assertGreater(operation["value"], self.spec["layout"]["right_margin_cm"])

    def test_switches_to_existing_figure_type(self):
        result = self.interpreter.interpret("用这个文件画 O/E 热图", self.spec)
        self.assertEqual(result.action, "patch")
        operation = result.patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["field"], operation["value"]), ("figure", "figure_type", "hic_oe"))

    def test_switches_to_compartment(self):
        result = self.interpreter.interpret("画一个 A/B compartment 图", self.spec)
        self.assertEqual(result.patch["operations"][0]["value"], "compartment")

    def test_switches_to_loop_figures(self):
        heatmap = self.interpreter.interpret("把 loops 标注到 loop 热图上", self.spec)
        apa = self.interpreter.interpret("画一个 loop APA 聚合峰值图", self.spec)
        self.assertEqual(heatmap.patch["operations"][0]["value"], "loop_heatmap")
        self.assertEqual(apa.patch["operations"][0]["value"], "loop_apa")

    def test_compartment_palette_request_uses_current_figure_context(self):
        spec = load_spec()
        spec["figure_type"] = "compartment"
        spec["panels"] = [{
            "id": "hic_panel", "kind": "hic_heatmap",
            "layers": [spec["panels"][0]["layers"][0]],
        }]
        automatic = self.interpreter.interpret("把红蓝色换掉", spec)
        self.assertEqual(automatic.action, "patch")
        self.assertEqual(automatic.patch["operations"][0]["value"], "PRGn")

        result = self.interpreter.interpret("那就换成紫绿色", spec)
        self.assertEqual(result.action, "patch")
        values = [operation["value"] for operation in result.patch["operations"]]
        self.assertEqual(values, ["PRGn", "#1B7837", "#762A83"])

    def test_compartment_diff_scatter_can_replace_the_full_palette(self):
        spec = load_spec()
        spec["figure_type"] = "compartment_diff_scatter"
        spec["workflow_options"] = {}

        for message in ("颜色都换一下吧，现在这个色系不好看", "你推荐一套吧"):
            result = self.interpreter.interpret(message, spec)
            self.assertEqual(result.action, "patch")
            operations = result.patch["operations"]
            self.assertEqual(
                [operation["field"] for operation in operations],
                [
                    "workflow_options.stable_a_color",
                    "workflow_options.stable_b_color",
                    "workflow_options.a_to_b_color",
                    "workflow_options.b_to_a_color",
                    "workflow_options.control_density_color",
                    "workflow_options.treatment_density_color",
                ],
            )
            self.assertEqual(
                [operation["value"] for operation in operations],
                ["#0072B2", "#009E73", "#E69F00", "#D55E00", "#56B4E9", "#CC79A7"],
            )


if __name__ == "__main__":
    unittest.main()
