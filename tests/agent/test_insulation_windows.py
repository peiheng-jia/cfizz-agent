from pathlib import Path
import tempfile
import unittest

from cfizz.agent.insulation_windows import common_insulation_windows, insulation_window_sizes, select_insulation_window


class InsulationWindowTests(unittest.TestCase):
    def test_choices_require_score_and_boundary_columns_in_every_file(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tsv"
            second = Path(directory) / "second.tsv"
            first.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_50000\tis_boundary_50000\t"
                "log2_insulation_score_100000\tis_boundary_100000\t"
                "log2_insulation_score_500000\tis_boundary_500000\t"
                "log2_insulation_score_200000\n",
                encoding="utf-8",
            )
            second.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_100000\tis_boundary_100000\t"
                "log2_insulation_score_500000\tis_boundary_500000\n",
                encoding="utf-8",
            )
            self.assertEqual(insulation_window_sizes(first), (50_000, 100_000, 500_000))
            self.assertEqual(common_insulation_windows((first, second)), (100_000, 500_000))
            self.assertEqual(select_insulation_window((first, second)), 100_000)
            self.assertEqual(select_insulation_window((first, second), 500_000), 500_000)

    def test_default_follows_columns_in_a_different_file(self):
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "other.insulation.tsv"
            table.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_75000\tis_boundary_75000\t"
                "log2_insulation_score_250000\tis_boundary_250000\n",
                encoding="utf-8",
            )
            self.assertEqual(insulation_window_sizes(table), (75_000, 250_000))
            self.assertEqual(select_insulation_window((table,)), 75_000)
            with self.assertRaisesRegex(ValueError, "可选窗口：75 kb、250 kb"):
                select_insulation_window((table,), 100_000)


if __name__ == "__main__":
    unittest.main()
