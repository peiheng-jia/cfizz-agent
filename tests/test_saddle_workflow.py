from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfizz.api.integrated import saddle_plot


class SaddleWorkflowTests(unittest.TestCase):
    def test_single_saddle_returns_a_real_error_without_drawing_a_blank_figure(self):
        failed = {
            "status": "error",
            "error": "mismatch between track and cooler bin size",
            "sample_name": "sample",
            "idx": 0,
        }
        with tempfile.TemporaryDirectory() as output_dir:
            with (
                patch.object(saddle_plot, "process_saddle_sample", return_value=failed),
                patch.object(saddle_plot, "plot_easy_saddle") as plot,
            ):
                result = saddle_plot.generate_single_saddle(
                    "sample.mcool::/resolutions/5000",
                    "sample.100kb.E1.tsv",
                    output_dir,
                    "sample",
                )

        self.assertEqual(result["status"], "error")
        self.assertIn("mismatch between track and cooler bin size", result["error"])
        plot.assert_not_called()

    def test_cache_identity_includes_the_mcool_resolution_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "sample.mcool"
            eigenvector = root / "sample.E1.tsv"
            matrix.touch()
            eigenvector.touch()

            at_50k = saddle_plot._saddle_cache_file(
                root, f"{matrix}::/resolutions/50000", str(eigenvector), "sample", 98, "cis"
            )
            at_100k = saddle_plot._saddle_cache_file(
                root, f"{matrix}::/resolutions/100000", str(eigenvector), "sample", 98, "cis"
            )

        self.assertNotEqual(at_50k.name, at_100k.name)


if __name__ == "__main__":
    unittest.main()
