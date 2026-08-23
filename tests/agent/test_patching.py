import json
from pathlib import Path
import unittest

from cfizz.agent import FigurePatchEngine, FigurePatchError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


class FigurePatchEngineTests(unittest.TestCase):
    def test_updates_layer_by_stable_id_without_mutating_input(self):
        spec = load_spec()
        result = FigurePatchEngine().apply(spec, {
            "operations": [{
                "op": "update",
                "target_kind": "layer",
                "target_id": "atac_normal_layer",
                "field": "style.color",
                "value": "#000000",
            }],
        })

        original_color = spec["panels"][2]["layers"][0]["style"]["color"]
        changed_color = result.spec["panels"][2]["layers"][0]["style"]["color"]
        self.assertEqual(original_color, "#666666")
        self.assertEqual(changed_color, "#000000")
        self.assertEqual(result.impact, "render_only")

    def test_scientific_parameter_change_is_classified(self):
        result = FigurePatchEngine().apply(load_spec(), {
            "operations": [{
                "op": "update",
                "target_kind": "analysis",
                "field": "resolution",
                "value": 100_000,
            }],
        })
        self.assertEqual(result.impact, "scientific_recompute")

    def test_workflow_option_updates_are_nested_and_classified(self):
        """Workflow controls must be patchable without opening arbitrary paths."""
        loop_spec = load_spec()
        loop_spec["figure_type"] = "loop_multi"
        result = FigurePatchEngine().apply(loop_spec, {
            "operations": [{
                "op": "update",
                "target_kind": "figure",
                "field": "workflow_options.loop_size",
                "value": 20,
            }],
        })
        self.assertEqual(result.spec["workflow_options"]["loop_size"], 20)
        self.assertEqual(result.impact, "render_only")

        tad_spec = load_spec()
        tad_spec["figure_type"] = "tad_multi"
        scientific = FigurePatchEngine().apply(tad_spec, {
            "operations": [{
                "op": "update",
                "target_kind": "figure",
                "field": "workflow_options.window_size",
                "value": 200_000,
            }],
        })
        self.assertEqual(scientific.spec["workflow_options"]["window_size"], 200_000)
        self.assertEqual(scientific.impact, "scientific_recompute")

    def test_workflow_option_does_not_allow_arbitrary_nested_field(self):
        with self.assertRaisesRegex(FigurePatchError, "不存在可编辑参数|不允许修改|非法字段"):
            FigurePatchEngine().apply(load_spec(), {
                "operations": [{
                    "op": "update",
                    "target_kind": "figure",
                    "field": "workflow_options.__private",
                    "value": 1,
                }],
            })

    def test_invalid_patch_is_rejected_transactionally(self):
        spec = load_spec()
        original_end = spec["viewport"]["end"]
        with self.assertRaises(FigurePatchError):
            FigurePatchEngine().apply(spec, {
                "operations": [{
                    "op": "update",
                    "target_kind": "viewport",
                    "field": "end",
                    "value": 10,
                }],
            })
        self.assertEqual(spec["viewport"]["end"], original_end)

    def test_source_cannot_be_removed_while_referenced(self):
        with self.assertRaisesRegex(FigurePatchError, "仍被图层引用"):
            FigurePatchEngine().apply(load_spec(), {
                "operations": [{"op": "remove_source", "source_id": "atac_normal"}],
            })

    def test_cascade_removes_source_and_referencing_layer(self):
        result = FigurePatchEngine().apply(load_spec(), {
            "operations": [{"op": "remove_source", "source_id": "atac_normal", "cascade": True}],
        })
        source_ids = {source["id"] for source in result.spec["data_sources"]}
        layer_ids = {layer["id"] for panel in result.spec["panels"] for layer in panel["layers"]}
        self.assertNotIn("atac_normal", source_ids)
        self.assertNotIn("atac_normal_layer", layer_ids)
        self.assertEqual(result.impact, "data_reload")


if __name__ == "__main__":
    unittest.main()
