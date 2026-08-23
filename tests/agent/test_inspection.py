from pathlib import Path
import tempfile
import unittest

from cfizz.agent import DataInspector


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DataInspectorTests(unittest.TestCase):
    def test_inspects_demo_gtf(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        result = inspector.inspect("demo/data/FOXJ1.gtf", "gtf")

        self.assertTrue(result.usable)
        self.assertEqual(result.type, "gtf")
        self.assertIn("chr17", result.metadata["chromosomes"])
        self.assertGreater(result.metadata["parsed_rows"], 0)

    def test_mcool_is_usable_without_eager_scientific_imports(self):
        inspector = DataInspector([str(PROJECT_ROOT)])
        result = inspector.inspect("demo/data/hiPSC_nor_chr17.mcool", "mcool")

        self.assertTrue(result.usable)
        self.assertEqual(result.type, "mcool")
        self.assertIn(result.metadata["inspection_level"], {"file_only", "full"})

    def test_rejects_path_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as allowed:
            inspector = DataInspector([allowed])
            result = inspector.inspect(str(PROJECT_ROOT / "README.md"))

        self.assertFalse(result.usable)
        self.assertIn("不在已授权目录", result.error)

    def test_translates_windows_drive_path_for_wsl(self):
        inspector = DataInspector(["/mnt/e"])
        resolved = inspector.resolve_path(r"E:\2026.7.25加测数据分析\case_analysis\51_5\sample.mcool")
        self.assertEqual(resolved, Path("/mnt/e/2026.7.25加测数据分析/case_analysis/51_5/sample.mcool"))

    def test_translates_quoted_windows_path(self):
        converted = DataInspector.platform_path(r'"E:\data folder\sample.mcool"')
        self.assertEqual(converted, Path("/mnt/e/data folder/sample.mcool"))


if __name__ == "__main__":
    unittest.main()
