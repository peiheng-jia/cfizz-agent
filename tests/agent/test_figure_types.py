import unittest

import cfizz.api

from cfizz.agent.figure_types import DIRECT_FIGURE_TYPE_IDS, FIGURE_TYPE_BY_ID, FIGURE_TYPES, READY_FIGURE_TYPE_IDS
from cfizz.agent.web import _validate_hic_count
from cfizz.agent.dataset import _parse_region_query
from cfizz.agent.patching import FigurePatchEngine


class FigureTypeCatalogueTests(unittest.TestCase):
    def test_every_catalogue_item_uses_a_real_public_cfizz_api(self):
        for item in FIGURE_TYPES:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["entrypoint"].startswith("cfizz.api."))
                self.assertTrue(hasattr(cfizz.api, item["entrypoint"].rsplit(".", 1)[-1]))

    def test_direct_profiles_are_validated_render_profiles(self):
        self.assertEqual(
            DIRECT_FIGURE_TYPE_IDS,
            {item["id"] for item in FIGURE_TYPES if item["selection_mode"] == "direct" and item["ready"]},
        )

    def test_every_registered_cfizz_figure_is_a_valid_workflow_profile(self):
        self.assertEqual(READY_FIGURE_TYPE_IDS, {item["id"] for item in FIGURE_TYPES})

    def test_every_figure_declares_machine_readable_input_roles(self):
        for item in FIGURE_TYPES:
            with self.subTest(item=item["id"]):
                contract = item.get("input_contract")
                self.assertIsInstance(contract, dict)
                self.assertTrue(contract.get("roles") or contract.get("any_of"))
                declared = set(contract.get("roles") or {})
                self.assertEqual(set(contract.get("allowed_roles") or []), declared)

    def test_tad_multi_contract_requires_confirmed_sample_pairing(self):
        contract = FIGURE_TYPE_BY_ID["tad_multi"]["input_contract"]
        self.assertEqual(contract["allowed_roles"], ["hic", "insulation"])
        self.assertEqual(contract["roles"]["hic"]["min"], 2)
        self.assertTrue(contract["roles"]["insulation"]["per_anchor"])
        self.assertEqual(contract["pairing"]["companion_roles"], ["insulation"])
        self.assertTrue(contract["pairing"]["requires_confirmation"])

    def test_hic_input_counts_are_explicit(self):
        self.assertEqual(FIGURE_TYPE_BY_ID["hic_triangle"]["input_cardinality"]["hic"], {"min": 1, "max": 1})
        self.assertEqual(FIGURE_TYPE_BY_ID["hic_multi"]["input_cardinality"]["hic"], {"min": 2, "max": 2})
        self.assertEqual(FIGURE_TYPE_BY_ID["compartment_multi"]["input_cardinality"]["hic"], {"min": 2, "max": None})

    def test_backend_rejects_the_wrong_hic_count(self):
        _validate_hic_count("hic_triangle", 1)
        _validate_hic_count("hic_multi", 2)
        with self.assertRaisesRegex(ValueError, "只能选择 1 个"):
            _validate_hic_count("hic_triangle", 2)
        with self.assertRaisesRegex(ValueError, "只能选择 2 个"):
            _validate_hic_count("hic_multi", 1)

    def test_dataset_accepts_gene_or_genomic_range_queries(self):
        self.assertEqual(_parse_region_query("chr1:1-2Mb"), ("chr1", 1, 2_000_000))
        self.assertEqual(_parse_region_query("17:75,636,332-76,641,245"), ("chr17", 75_636_332, 76_641_245))
        self.assertEqual(_parse_region_query("chr1"), ("chr1", 0, 2_000_000))
        self.assertIsNone(_parse_region_query("FOXJ1"))

    def test_color_alias_is_compiled_to_a_real_colormap(self):
        spec = {
            "schema_version": "0.1", "figure_type": "hic_triangle", "figure_id": "cmap_test",
            "title": "test", "data_sources": [{"id": "x", "type": "cool", "path": "sample.cool"}],
            "viewport": {"chrom": "chr1", "start": 0, "end": 2_000_000},
            "analysis": {"resolution": 5_000, "balance": True, "normalization": "raw", "shared_color_scale": True},
            "panels": [{"id": "p", "kind": "hic_heatmap", "layers": [{"id": "h", "kind": "hic", "source_id": "x", "style": {"cmap": "Reds"}}]}],
            "layout": {"width_cm": 8, "gap_cm": 0.1, "left_margin_cm": 1, "right_margin_cm": 2, "font_size": 5},
            "export": {"formats": ["svg"]},
        }
        for alias in ("purple_yellow", "YlPur", "黄色到紫色"):
            with self.subTest(alias=alias):
                result = FigurePatchEngine().apply(spec, {"operations": [{"op": "update", "target_kind": "layer", "target_id": "h", "field": "style.cmap", "value": alias}]})
                self.assertEqual(result.spec["panels"][0]["layers"][0]["style"]["cmap"], "plasma")


if __name__ == "__main__":
    unittest.main()
