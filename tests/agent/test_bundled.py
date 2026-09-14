import tempfile
from pathlib import Path
import unittest

from cfizz.agent.bundled import DEMO_DATA_ROOT, load_demo_spec, resource_checks
from cfizz.agent.figure_spec import FigureSpecValidator
from cfizz.agent.inspection import DataInspector
from cfizz.agent.references import ReferenceRegistry


class BundledResourcesTest(unittest.TestCase):
    def test_every_declared_resource_exists(self):
        missing = [str(path) for _, path in resource_checks() if not path.is_file() or path.stat().st_size <= 0]
        self.assertEqual(missing, [])

    def test_demo_spec_resolves_and_validates_outside_checkout(self):
        spec = load_demo_spec()
        self.assertTrue(all(Path(source["path"]).is_file() for source in spec["data_sources"]))
        validator = FigureSpecValidator(DataInspector([str(DEMO_DATA_ROOT)]))
        result = validator.validate(spec)
        self.assertTrue(result.valid, [issue.to_dict() for issue in result.errors])

    def test_hg38_reference_falls_back_to_installed_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = ReferenceRegistry(temporary, Path(temporary) / "cache")
            catalog = registry.catalog()
            self.assertEqual([item["id"] for item in catalog], ["hg38"])
            self.assertTrue(catalog[0]["complete"])
            self.assertGreater(catalog[0]["gene_count"], 60_000)
            myc = registry.locate_gene("MYC", "hg38")
            self.assertIsNotNone(myc)
            self.assertEqual(myc.chrom, "chr8")


if __name__ == "__main__":
    unittest.main()
