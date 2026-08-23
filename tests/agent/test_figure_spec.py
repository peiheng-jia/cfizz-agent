import unittest

from cfizz.agent.figure_spec import FigureSpecValidator
from cfizz.agent.inspection import InspectionResult


class StubInspector:
    def inspect_many(self, sources):
        return {
            source["id"]: InspectionResult(
                path=source["path"],
                type=source["type"],
                exists=True,
                readable=True,
                metadata={
                    "inspection_level": "full",
                    "resolutions": [10_000, 100_000],
                    "chromosomes": ["chr17"],
                    "chromsizes": {"chr17": 83_257_441},
                },
            )
            for source in sources
        }


def minimal_spec():
    return {
        "schema_version": "0.1",
        "figure_id": "demo",
        "title": "Demo",
        "data_sources": [
            {"id": "hic", "type": "mcool", "path": "sample.mcool"},
        ],
        "viewport": {
            "chrom": "chr17",
            "start": 10_000_000,
            "end": 12_000_000,
            "coordinate_system": "0-based-half-open",
        },
        "panels": [
            {
                "id": "main",
                "kind": "hic_heatmap",
                "layers": [
                    {"id": "hic_layer", "kind": "hic", "source_id": "hic", "visible": True, "style": {}},
                ],
            },
        ],
    }


class FigureSpecValidatorTests(unittest.TestCase):
    def test_resolves_auto_resolution_and_defaults(self):
        validator = FigureSpecValidator(StubInspector())
        result = validator.validate(minimal_spec())

        self.assertTrue(result.valid)
        self.assertEqual(result.resolved_spec["analysis"]["resolution"], 10_000)
        self.assertEqual(result.resolved_spec["export"]["dpi"], 300)
        self.assertEqual(result.resolved_spec["layout"]["font_size"], 5)
        self.assertIn("resolution_auto_selected", [issue.code for issue in result.warnings])

    def test_rejects_missing_layer_source(self):
        spec = minimal_spec()
        spec["panels"][0]["layers"][0]["source_id"] = "missing"
        result = FigureSpecValidator().validate(spec, inspect_files=False)

        self.assertFalse(result.valid)
        self.assertIn("missing_source", [issue.code for issue in result.errors])

    def test_rejects_unavailable_explicit_resolution(self):
        spec = minimal_spec()
        spec["analysis"] = {"resolution": 25_000}
        result = FigureSpecValidator(StubInspector()).validate(spec)

        self.assertFalse(result.valid)
        self.assertIn("resolution_unavailable", [issue.code for issue in result.errors])

    def test_rejects_layer_source_type_mismatch(self):
        spec = minimal_spec()
        spec["data_sources"][0]["type"] = "gtf"
        result = FigureSpecValidator().validate(spec, inspect_files=False)

        self.assertFalse(result.valid)
        self.assertIn("source_type_mismatch", [issue.code for issue in result.errors])

    def test_rejects_out_of_range_figure_font_size(self):
        spec = minimal_spec()
        spec["layout"] = {"font_size": 40}
        result = FigureSpecValidator().validate(spec, inspect_files=False)

        self.assertFalse(result.valid)
        self.assertIn("invalid_font_size", [issue.code for issue in result.errors])


if __name__ == "__main__":
    unittest.main()
