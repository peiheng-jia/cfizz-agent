from pathlib import Path
import tempfile
import unittest

import matplotlib.pyplot as plt

from cfizz.api.integrated.tracks.simple import GenomeRange, SimpleTrack


class IntegratedGeneTrackTests(unittest.TestCase):
    def test_many_gene_labels_stay_clipped_inside_gtf_axis(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "genes.gtf"
            lines = []
            for index in range(15):
                start = 97_000 if index == 14 else 1_000 + index * 5_000
                end = 99_500 if index == 14 else start + 2_000
                attrs = f'gene_id "G{index}"; gene_name "GENE{index}";'
                lines.append(f"chr1\ttest\texon\t{start}\t{end}\t.\t+\t.\t{attrs}\n")
            path.write_text("".join(lines), encoding="utf-8")

            track = SimpleTrack(str(path), "gtf", fontsize=5)
            figure, axis = plt.subplots()
            try:
                track.plot(axis, GenomeRange("chr1", 0, 100_000))
                lower, upper = axis.get_ylim()
                self.assertEqual(len(axis.texts), 15)
                self.assertTrue(all(text.get_clip_on() for text in axis.texts))
                self.assertTrue(all(lower <= text.get_position()[1] <= upper for text in axis.texts))
                self.assertEqual(axis.texts[-1].get_ha(), "right")
            finally:
                plt.close(figure)
