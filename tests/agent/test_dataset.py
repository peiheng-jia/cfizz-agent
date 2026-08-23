import json
from pathlib import Path
import tempfile
import unittest

from cfizz.agent.dataset import (
    DatasetFile,
    DatasetScan,
    build_integrated_spec,
    build_workflow_spec,
    build_track_patch,
    prepare_workflow_selection,
    scan_dataset,
)
from cfizz.agent.figure_spec import FigureSpecValidator
from cfizz.agent.inspection import DataInspector
from cfizz.agent.references import ReferenceRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DatasetDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.inspector = DataInspector([str(PROJECT_ROOT)])
        self.references = ReferenceRegistry(PROJECT_ROOT)

    def test_reference_catalog_and_foxj1_lookup(self):
        catalog = self.references.catalog()
        self.assertEqual(catalog[0]["id"], "hg38")
        location = self.references.locate_gene("FOXJ1")
        self.assertEqual((location.chrom, location.start, location.end), ("chr17", 76136332, 76141245))
        self.assertEqual(location.source, "GTF")
        other = self.references.locate_gene("ACOX1")
        self.assertIsNotNone(other)
        self.assertEqual(other.chrom, "chr17")
        self.assertIn("ACOX1", catalog[0]["bundled_genes"])

    def test_scans_demo_as_experiment_directory(self):
        scan = scan_dataset("demo/data", self.inspector, self.references, gene="FOXJ1")
        self.assertEqual(scan.gene["gene"], "FOXJ1")
        self.assertEqual(len([item for item in scan.files if item.role == "hic" and item.usable]), 2)
        self.assertGreaterEqual(len([item for item in scan.files if item.role == "signal" and item.usable]), 8)
        self.assertTrue(any(item["id"] == "integrated" for item in scan.capabilities))
        self.assertFalse(scan.missing)

    def test_case_without_gtf_reports_project_reference_genes(self):
        scan = scan_dataset("demo/cases/2121401", self.inspector, self.references)
        notice = "；".join(scan.warnings)
        self.assertIn("项目公共参考 Ensembl 110 / GRCh38.p14", notice)
        self.assertIn("62,754 条人类基因记录", notice)
        self.assertNotIn("只提供 FOXJ1", notice)

    def test_complete_reference_locates_arbitrary_symbol_and_ensembl_id(self):
        tp53 = self.references.locate_gene("TP53")
        by_id = self.references.locate_gene("ENSG00000141510")
        self.assertIsNotNone(tp53)
        self.assertEqual((tp53.chrom, tp53.start, tp53.end), ("chr17", 7_661_778, 7_687_538))
        self.assertEqual(by_id.gene, "TP53")
        self.assertEqual(tp53.source, "Ensembl 110")
        self.assertTrue(self.references.has_complete_annotation())
        self.assertEqual(self.references.complete_gene_count(), 62_754)

    def test_complete_reference_suggests_transposed_gene_symbol(self):
        self.assertIn("MYC", self.references.suggest_genes("MCY"))

    def test_complete_reference_extracts_all_genes_in_viewport(self):
        path = Path(self.references.region_annotation_track("chr8", 127_235_433, 128_242_951))
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        names = set(__import__("re").findall(r'gene_name "([^"]+)"', text))
        self.assertIn("MYC", names)
        self.assertGreater(len(names), 1)
        self.assertGreaterEqual(self.references.annotation_gene_count(str(path)), len(names))

    def test_builds_valid_foxj1_like_spec_from_scan(self):
        scan = scan_dataset("demo/data", self.inspector, self.references, gene="FOXJ1")
        spec = build_integrated_spec(scan, "dataset_test", self.references, gene="FOXJ1")
        self.assertEqual(spec["figure_type"], "hic_triangle")
        self.assertEqual(spec["viewport"]["chrom"], "chr17")
        self.assertEqual(spec["viewport"]["focus_label"], "FOXJ1")
        self.assertGreaterEqual(len(spec["data_sources"]), 10)
        result = FigureSpecValidator(self.inspector).validate(spec)
        self.assertTrue(result.valid, json.dumps([item.to_dict() for item in result.errors], ensure_ascii=False))

    def test_adds_scanned_bigwigs_to_an_existing_hic_figure(self):
        scan = scan_dataset("demo/data", self.inspector, self.references)
        full = build_integrated_spec(scan, "track_patch_source", self.references)
        hic_only = json.loads(json.dumps(full))
        hic_only["data_sources"] = [source for source in hic_only["data_sources"] if source["type"] in {"cool", "mcool"}][:1]
        hic_source_ids = {source["id"] for source in hic_only["data_sources"]}
        hic_only["panels"] = [{
            "id": "hic_panel", "kind": "hic_heatmap", "label": "Hi-C", "height_cm": "auto",
            "layers": [layer for layer in full["panels"][0]["layers"] if layer["source_id"] in hic_source_ids],
        }]
        patch = build_track_patch(scan, hic_only, self.inspector, {"signal"})
        self.assertGreaterEqual(patch["added"]["bigwig"], 8)
        self.assertTrue(any(operation["op"] == "add_panel" for operation in patch["operations"]))
        source_ops = [operation for operation in patch["operations"] if operation["op"] == "add_source"]
        self.assertTrue(all(operation["source"]["type"] == "bigwig" for operation in source_ops))

    def test_reselecting_existing_tracks_is_a_noop(self):
        scan = scan_dataset("demo/data", self.inspector, self.references)
        full = json.loads((PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").read_text(encoding="utf-8"))
        existing_ids = {source["id"] for source in full["data_sources"]}
        for index, item in enumerate(scan.files):
            if item.role == "signal" and item.path not in {source["path"] for source in full["data_sources"]}:
                full["data_sources"].append({"id": f"existing_signal_{index}", "type": item.type, "path": item.path, "label": item.label})
        patch = build_track_patch(scan, full, self.inspector, {"signal"})
        self.assertEqual(patch["added"]["bigwig"], 0)
        self.assertTrue(patch["already_present"])
        self.assertEqual(patch["operations"], [])

    def test_text_roles_are_inferred_from_structure_before_filename(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            insulation = root / "ambiguous_a.tsv"
            boundary = root / "ambiguous_b.tsv"
            insulation.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_10000\tis_boundary_10000\n"
                "chr1\t0\t10000\t0.1\tFalse\n"
                "chr1\t10000\t20000\t-0.2\tTrue\n"
                "chr1\t20000\t30000\t0.3\tFalse\n"
                "chr1\t30000\t40000\t0.4\tFalse\n",
                encoding="utf-8",
            )
            boundary.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_10000\tis_boundary_10000\n"
                "chr1\t10000\t20000\t-0.2\tTrue\n"
                "chr1\t90000\t100000\t-0.4\tTrue\n"
                "chr1\t220000\t230000\t-0.6\tTrue\n",
                encoding="utf-8",
            )

            scan = scan_dataset(str(root), self.inspector, self.references)
            by_name = {item.name: item for item in scan.files}
            self.assertEqual(by_name[insulation.name].role, "insulation")
            self.assertEqual(by_name[insulation.name].allowed_roles, ["insulation", "boundaries"])
            self.assertEqual(by_name[boundary.name].role, "boundaries")
            self.assertEqual(by_name[boundary.name].allowed_roles, ["boundaries"])

    def test_role_override_is_limited_to_structurally_compatible_roles(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            table = root / "renamed.tsv"
            table.write_text(
                "chrom\tstart\tend\tlog2_insulation_score_10000\tis_boundary_10000\n"
                "chr1\t0\t10000\t0.1\tFalse\n"
                "chr1\t10000\t20000\t-0.2\tTrue\n"
                "chr1\t20000\t30000\t0.3\tFalse\n",
                encoding="utf-8",
            )
            overrides = {str(table.resolve()): {"role": "boundaries", "sample": "sample_A"}}
            scan = scan_dataset(str(root), self.inspector, self.references, file_overrides=overrides)
            self.assertEqual(scan.files[0].role, "boundaries")
            self.assertEqual(scan.files[0].sample, "sample_A")

            with self.assertRaisesRegex(ValueError, "不能标记为 loops"):
                scan_dataset(
                    str(root), self.inspector, self.references,
                    file_overrides={str(table.resolve()): {"role": "loops"}},
                )

    def test_only_headerless_bedpe_is_offered_as_a_cfizz_loop_input(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            raw = root / "renamed_calls.txt"
            summary = root / "loop_plot_summary.tsv"
            headered = root / "differential_loops.tsv"
            raw.write_text(
                "chr1\t10000\t20000\tchr1\t50000\t60000\t.\t.\t.\t.\t.\t.\t.\t.\t.\t.\n",
                encoding="utf-8",
            )
            summary.write_text(
                "sample\ttotal\tStable\tLow_FE\nA\t100\t80\t20\n",
                encoding="utf-8",
            )
            headered.write_text(
                "chrom1\tstart1\tend1\tchrom2\tstart2\tend2\tis_stable\n"
                "chr1\t10000\t20000\tchr1\t50000\t60000\tTrue\n",
                encoding="utf-8",
            )

            scan = scan_dataset(str(root), self.inspector, self.references)
            by_name = {item.name: item for item in scan.files}
            self.assertEqual(by_name[raw.name].role, "loops")
            self.assertEqual(by_name[raw.name].allowed_roles, ["loops"])
            self.assertEqual(by_name[summary.name].role, "unknown")
            self.assertEqual(by_name[summary.name].allowed_roles, [])
            self.assertEqual(by_name[headered.name].role, "unknown")
            self.assertEqual(by_name[headered.name].allowed_roles, [])

    def test_boundary_context_and_called_interval_tables_get_constrained_roles(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            context = root / "51_5_boundary_context.tsv"
            called = root / "called_boundaries.tsv"
            context.write_text(
                "sample\tchrom\tstart\tend\tdist_to_upstream\tdist_to_downstream\tadaptive_threshold\n"
                "A\tchr1\t880000.0\t890000.0\t10000\t20000\t30000\n",
                encoding="utf-8",
            )
            called.write_text(
                "chrom\tstart\tend\nchr1\t10000\t20000\nchr1\t90000\t100000\n",
                encoding="utf-8",
            )

            scan = scan_dataset(str(root), self.inspector, self.references)
            by_name = {item.name: item for item in scan.files}
            self.assertEqual(by_name[context.name].role, "boundaries")
            self.assertEqual(by_name[context.name].allowed_roles, ["boundaries"])
            self.assertEqual(by_name[context.name].sample, "51_5")
            self.assertEqual(by_name[called.name].role, "boundaries")
            # A called-boundary TSV is an analysis result.  It must not be
            # relabelled as a generic ``intervals`` track: CFIZZ's intervals
            # layer accepts BED only.
            self.assertEqual(by_name[called.name].allowed_roles, ["boundaries"])

    def test_coordinate_tsv_is_not_offered_as_generic_interval_track(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            table = root / "annotations.tsv"
            table.write_text(
                "chrom\tstart\tend\n"
                "chr1\t10000\t20000\n"
                "chr1\t50000\t60000\n",
                encoding="utf-8",
            )
            scan = scan_dataset(str(root), self.inspector, self.references)
            detected = next(item for item in scan.files if item.name == table.name)
            self.assertEqual(detected.role, "unknown")
            self.assertEqual(detected.allowed_roles, [])
            self.assertTrue(any("只接受 BED" in value for value in detected.role_evidence))

    def test_prepare_workflow_rejects_interval_tsv_before_figurespec_build(self):
        def item(path, role, source_type):
            return DatasetFile(
                path=path,
                name=Path(path).name,
                type=source_type,
                role=role,
                assay="Hi-C" if role == "hic" else "intervals",
                sample="A",
                group=None,
                label=Path(path).stem,
                usable=True,
                resolutions=[5_000] if role == "hic" else [],
                auto_role=role,
                allowed_roles=[role],
                auto_sample="A",
            )

        scan = DatasetScan(
            root="/experiment",
            files=[
                item("/experiment/A.mcool", "hic", "mcool"),
                # This is the stale/incorrect state that previously reached
                # FigureSpec validation as intervals + tsv.
                item("/experiment/A.tsv", "intervals", "tsv"),
            ],
            reference={}, gene=None, chromosomes=["chr1"], resolutions=[5_000],
            capabilities=[], missing=[],
        )
        with self.assertRaisesRegex(ValueError, "区间轨道必须是 BED"):
            prepare_workflow_selection(scan, "hic_triangle")

    def test_result_only_tad_workflow_does_not_invent_a_hic_requirement(self):
        """The boundary-difference contract is executable with two TSVs alone.

        This guards the regression where the picker correctly accepted two
        ``tad_tsv`` files, but the session constructor rebuilt an integrated
        Hi-C spec and failed later with "没有可用 .cool/.mcool".
        """
        def item(path, sample):
            return DatasetFile(
                path=path,
                name=Path(path).name,
                type="tad_tsv",
                role="boundaries",
                assay="TAD 边界",
                sample=sample,
                group=None,
                label=Path(path).stem,
                usable=True,
                resolutions=[],
                auto_role="boundaries",
                allowed_roles=["boundaries"],
                auto_sample=sample,
            )

        scan = DatasetScan(
            root="/experiment",
            files=[
                item("/experiment/A.10b.boundaries.tsv", "A"),
                item("/experiment/A.50b.boundaries.tsv", "A"),
            ],
            reference={}, gene=None, chromosomes=["chr1"], resolutions=[],
            capabilities=[], missing=[],
        )
        selected, _ = prepare_workflow_selection(scan, "tad_diff_stacked")
        spec = build_workflow_spec(
            selected, "tad_only", "tad_diff_stacked", self.references,
        )
        self.assertEqual({source["type"] for source in spec["data_sources"]}, {"tad_tsv"})
        self.assertNotIn("cool", {source["type"] for source in spec["data_sources"]})
        self.assertNotIn("mcool", {source["type"] for source in spec["data_sources"]})
        result = FigureSpecValidator(self.inspector).validate(spec, inspect_files=False)
        self.assertTrue(result.valid, json.dumps([item.to_dict() for item in result.errors], ensure_ascii=False))

    def test_paired_workflow_requires_explicit_unique_confirmed_bindings(self):
        def item(path, role, sample):
            return DatasetFile(
                path=path,
                name=Path(path).name,
                type="mcool" if role == "hic" else "insulation_tsv",
                role=role,
                assay="Hi-C" if role == "hic" else "insulation",
                sample=sample,
                group=None,
                label=sample,
                usable=True,
                resolutions=[5_000] if role == "hic" else [],
                auto_role=role,
                allowed_roles=[role],
                auto_sample=sample,
            )

        scan = DatasetScan(
            root="/experiment",
            files=[
                item("/experiment/A.mcool", "hic", "A"),
                item("/experiment/B.mcool", "hic", "B"),
                item("/experiment/A.insulation.tsv", "insulation", "A"),
                item("/experiment/B.insulation.tsv", "insulation", "B"),
            ],
            reference={}, gene=None, chromosomes=["chr1"], resolutions=[5_000],
            capabilities=[], missing=[],
        )
        bindings = [
            {
                "sample": "A", "anchor_path": "/experiment/A.mcool",
                "companions": {"insulation": "/experiment/A.insulation.tsv"},
            },
            {
                "sample": "B", "anchor_path": "/experiment/B.mcool",
                "companions": {"insulation": "/experiment/B.insulation.tsv"},
            },
        ]
        with self.assertRaisesRegex(ValueError, "确认"):
            prepare_workflow_selection(scan, "tad_multi", bindings, False)

        paired, normalized = prepare_workflow_selection(scan, "tad_multi", bindings, True)
        self.assertEqual([entry["sample"] for entry in normalized], ["A", "B"])
        self.assertEqual(
            {file.name: file.sample for file in paired.files},
            {"A.mcool": "A", "B.mcool": "B", "A.insulation.tsv": "A", "B.insulation.tsv": "B"},
        )

        duplicated = [
            bindings[0],
            {
                "sample": "B", "anchor_path": "/experiment/B.mcool",
                "companions": {"insulation": "/experiment/A.insulation.tsv"},
            },
        ]
        with self.assertRaisesRegex(ValueError, "不能重复使用"):
            prepare_workflow_selection(scan, "tad_multi", duplicated, True)


if __name__ == "__main__":
    unittest.main()
