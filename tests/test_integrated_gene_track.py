from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt

from cfizz.api.integrated.quick_plot import quick_plot_integrated
from cfizz.api.integrated.tracks.simple import GenomeRange, SimpleTrack, plot_gtf_tracks, plot_mixed_tracks
from cfizz.api.workflows import plot_track_files


class IntegratedGeneTrackTests(unittest.TestCase):
    @staticmethod
    def _overlapping_gtf(directory: str, count: int = 12) -> Path:
        path = Path(directory) / "overlapping.gtf"
        path.write_text("".join(
            f'chr1\ttest\texon\t1000\t9000\t.\t+\t.\tgene_id "G{index}"; gene_name "GENE{index}";\n'
            for index in range(count)
        ), encoding="utf-8")
        return path

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

    def test_gtf_track_grows_for_packed_rows_without_changing_font_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._overlapping_gtf(directory)
            region = GenomeRange("chr1", 0, 10_000)
            track = SimpleTrack(str(path), "gtf", fontsize=9)
            required = track.required_height_cm(region, 5, 0.4)
            self.assertGreater(required, 4)
            self.assertEqual(track.config.fontsize, 9)

            with patch("matplotlib.pyplot.show"):
                plot_gtf_tracks([track], region, width=5, track_heights=[0.4])
            figure = plt.gcf()
            try:
                axis = figure.axes[0]
                self.assertGreater(axis.get_ylim()[1], 20)
                self.assertGreater(figure.get_size_inches()[1] * 2.54, required)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                labels = [text for text in axis.texts if text.get_text().startswith("GENE")]
                self.assertEqual(len(labels), 12)
                self.assertTrue(all(text.get_fontsize() == 9 for text in labels))
                boxes = [text.get_window_extent(renderer) for text in labels]
                self.assertFalse(any(a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i + 1:]))
            finally:
                plt.close(figure)

    def test_integrated_layout_expands_gtf_height_and_reuses_loaded_track(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._overlapping_gtf(directory)
            with patch("cfizz.api.integrated.quick_plot.HeatmapTracks.plot") as render:
                quick_plot_integrated(
                    hics=[{"file": "unused.mcool"}],
                    tracks=[{"file": str(path), "fontsize": 9}],
                    region=GenomeRange("chr1", 0, 10_000),
                    output=str(Path(directory) / "figure"),
                    track_heights_cm=[0.4],
                    width_cm=5,
                    font_size=5,
                )
            kwargs = render.call_args.kwargs
            layout = kwargs["layout"]
            physical_height = layout.tracks_axes[0][3] * layout.total_height_cm
            self.assertGreater(physical_height, 4)
            self.assertEqual(kwargs["prepared_tracks"][0].config.fontsize, 9)

    def test_mixed_tracks_expand_only_the_dense_gtf_track(self):
        with tempfile.TemporaryDirectory() as directory:
            gtf = SimpleTrack(str(self._overlapping_gtf(directory)), "gtf", fontsize=9)
            bed_path = Path(directory) / "intervals.bed"
            bed_path.write_text("chr1\t1000\t2000\tinterval\n", encoding="utf-8")
            bed = SimpleTrack(str(bed_path), "bed")
            with patch("matplotlib.pyplot.show"):
                plot_mixed_tracks(
                    [gtf, bed], GenomeRange("chr1", 0, 10_000),
                    width=5, track_heights=[0.4, 0.8],
                )
            figure = plt.gcf()
            try:
                heights = [axis.get_position().height * figure.get_size_inches()[1] * 2.54 for axis in figure.axes]
                self.assertAlmostEqual(heights[0], 0.8)
                self.assertGreater(heights[1], 4)
            finally:
                plt.close(figure)

    def test_agent_standalone_track_entrypoint_uses_auto_gtf_height(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._overlapping_gtf(directory)
            output = Path(directory) / "standalone"
            plot_track_files(
                tracks=[{"file": str(path), "type": "gtf", "fontsize": 9}],
                chrom="chr1", start=0, end=10_000,
                output=str(output), formats=("png",),
                width=5, track_heights=[0.4], dpi=100,
            )
            self.assertTrue(output.with_suffix(".png").exists())
            height_px = plt.imread(output.with_suffix(".png")).shape[0]
            self.assertGreater(height_px, 220)
