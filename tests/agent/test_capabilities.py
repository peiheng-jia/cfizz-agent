import json
from pathlib import Path
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from cfizz.agent.capabilities import CapabilityCall, CapabilityRegistry, FigureTargetResolver, TargetSelector
from cfizz.agent.intent import SimpleIntentInterpreter
from cfizz.agent.patching import FigurePatchEngine
from cfizz.api.integrated.heatmap_tracks import HeatmapTracks


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open(encoding="utf-8") as handle:
        return json.load(handle)


class CapabilityTests(unittest.TestCase):
    def test_registry_has_unique_public_capabilities(self):
        catalog = CapabilityRegistry().model_catalog()
        ids = [item["id"] for item in catalog]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("tracks.sync_y_axis", ids)
        self.assertIn("tracks.reorder", ids)

    def test_natural_language_reorders_tracks_by_real_layer_ids(self):
        spec = load_spec()
        result = SimpleIntentInterpreter().interpret("把 ATAC normal 放到 CTCF normal 上面", spec)
        self.assertEqual(result.action, "patch")
        operation = result.patch["operations"][0]
        self.assertEqual(operation["op"], "move_layer")
        self.assertEqual(operation["layer_id"], "atac_normal_layer")
        self.assertEqual(operation["before_layer_id"], "ctcf_normal_layer")
        patched = FigurePatchEngine().apply(spec, result.patch).spec
        signal = next(panel for panel in patched["panels"] if panel["id"] == "signal_panel")
        self.assertEqual(
            [layer["id"] for layer in signal["layers"]],
            ["atac_variant_layer", "h3k27ac_normal_layer", "h3k27ac_variant_layer", "atac_normal_layer", "ctcf_normal_layer", "ctcf_variant_layer", "rna_normal_layer", "rna_variant_layer"],
        )

    def test_tracks_reorder_capability_compiles_without_panel_paths(self):
        call = CapabilityCall(
            capability_id="tracks.reorder",
            target=TargetSelector(scope="layer", ids=["rna_variant_layer"], kinds=["bigwig"]),
            arguments_json='{"before_layer_id":"atac_normal_layer"}',
        )
        compiled = CapabilityRegistry().compile(call, load_spec())
        self.assertEqual(compiled.operations, [{
            "op": "move_layer", "layer_id": "rna_variant_layer", "before_layer_id": "atac_normal_layer",
        }])

    def test_diverging_palette_updates_hic_map_and_e1_colors(self):
        spec = load_spec()
        call = CapabilityCall(
            capability_id="layer.set_diverging_palette",
            target=TargetSelector(scope="layer", ids=["hic_normal_layer"]),
            arguments_json='{"cmap":"PRGn","positive_color":"#1B7837","negative_color":"#762A83"}',
        )
        compiled = CapabilityRegistry().compile(call, spec)
        self.assertEqual([item["field"] for item in compiled.operations], [
            "style.cmap", "style.positive_color", "style.negative_color",
        ])

    def test_resolves_last_four_numeric_tracks_in_panel_order(self):
        selector = TargetSelector(
            scope="layer", panel_id="signal_panel", position="last", count=4, kinds=["bigwig"]
        )
        targets = FigureTargetResolver().resolve(selector, load_spec())
        self.assertEqual(
            [item["id"] for item in targets],
            ["ctcf_normal_layer", "ctcf_variant_layer", "rna_normal_layer", "rna_variant_layer"],
        )

    def test_natural_language_unifies_bottom_four_y_axes(self):
        spec = load_spec()
        result = SimpleIntentInterpreter().interpret("让下面四个 y 轴保持一致", spec)
        self.assertEqual(result.action, "patch")
        operations = result.patch["operations"]
        self.assertEqual(len(operations), 4)
        self.assertEqual({item["field"] for item in operations}, {"style.y_scale_group"})
        self.assertEqual(len({item["value"] for item in operations}), 1)
        patched = FigurePatchEngine().apply(spec, result.patch).spec
        signal = next(panel for panel in patched["panels"] if panel["id"] == "signal_panel")
        selected = signal["layers"][-4:]
        groups = {layer["style"]["y_scale_group"] for layer in selected}
        self.assertEqual(len(groups), 1)
        self.assertTrue(all("y_scale_group" not in layer["style"] for layer in signal["layers"][:-4]))

    def test_fixed_shared_axis_reuses_min_max_parameters(self):
        call = CapabilityCall(
            capability_id="tracks.sync_y_axis",
            target=TargetSelector(
                scope="layer", ids=["atac_normal_layer", "atac_variant_layer"], kinds=["bigwig"]
            ),
            arguments_json='{"mode":"fixed","min_value":0,"max_value":12}',
        )
        compiled = CapabilityRegistry().compile(call, load_spec())
        fields = [item["field"] for item in compiled.operations]
        self.assertEqual(fields.count("style.min_value"), 2)
        self.assertEqual(fields.count("style.max_value"), 2)

    def test_y_axes_can_be_shared_within_each_assay_without_global_sharing(self):
        spec = load_spec()
        layers = next(panel for panel in spec["panels"] if panel["id"] == "signal_panel")["layers"]
        call = CapabilityCall(
            capability_id="tracks.sync_y_axis",
            target=TargetSelector(scope="layer", ids=[layer["id"] for layer in layers], kinds=["bigwig"]),
            arguments_json='{"mode":"by_assay"}',
        )
        compiled = CapabilityRegistry().compile(call, spec)
        groups = {operation["target_id"]: operation["value"] for operation in compiled.operations}
        self.assertEqual(groups["atac_normal_layer"], groups["atac_variant_layer"])
        self.assertEqual(groups["ctcf_normal_layer"], groups["ctcf_variant_layer"])
        self.assertNotEqual(groups["atac_normal_layer"], groups["ctcf_normal_layer"])
        self.assertNotEqual(groups["atac_normal_layer"], groups["rna_normal_layer"])

    def test_natural_language_groups_y_axes_by_sequencing_assay(self):
        spec = load_spec()
        result = SimpleIntentInterpreter().interpret("按照测序技术类型选定 y 轴最大值，一种测序技术保持一致", spec)
        self.assertEqual(result.action, "patch")
        values = {operation["target_id"]: operation["value"] for operation in result.patch["operations"]}
        self.assertEqual(values["atac_normal_layer"], values["atac_variant_layer"])
        self.assertNotEqual(values["atac_normal_layer"], values["ctcf_normal_layer"])
        self.assertNotEqual(values["ctcf_normal_layer"], values["rna_normal_layer"])

    def test_renderer_unifies_group_limits_but_leaves_other_axis_alone(self):
        fig, axes = plt.subplots(3, 1)
        records = [
            {"ax": axes[0], "group": "g", "minimum": 0, "maximum": 5},
            {"ax": axes[1], "group": "g", "minimum": 1, "maximum": 12},
            {"ax": axes[2], "group": None, "minimum": 2, "maximum": 3},
        ]
        HeatmapTracks._finalize_track_y_axes(records, formatter=lambda value: str(value), font_size=5)
        self.assertEqual(axes[0].get_ylim(), (0.0, 12.0))
        self.assertEqual(axes[1].get_ylim(), (0.0, 12.0))
        self.assertEqual(axes[2].get_ylim(), (2.0, 3.0))
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
