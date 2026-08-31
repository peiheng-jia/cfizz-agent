import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import httpx

from cfizz.agent.adapter import CfizzRenderAdapter
from cfizz.agent.intent import IntentResult
from cfizz.agent.web import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": ""})
        self.environment.start()
        self.runtime = tempfile.TemporaryDirectory()
        self.app = create_app(str(PROJECT_ROOT), self.runtime.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.app.state.workspace.jobs.shutdown()
        self.runtime.cleanup()
        self.environment.stop()

    async def test_health_and_workspace_page(self):
        health = (await self.client.get("/api/health")).json()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["api_revision"], 13)
        planner = (await self.client.get("/api/planner")).json()
        self.assertIn(planner["mode"], {"rules", "ai-assisted"})
        catalog = (await self.client.get("/api/planners")).json()
        self.assertEqual([item["id"] for item in catalog["providers"]], ["local", "openai", "deepseek"])
        self.assertTrue(catalog["providers"][0]["available"])
        figure_types = (await self.client.get("/api/figure-types")).json()["figure_types"]
        self.assertGreaterEqual(len([item for item in figure_types if item["ready"]]), 30)
        self.assertNotIn("distance_decay", [item["id"] for item in figure_types])
        self.assertIn("compartment", [item["id"] for item in figure_types if item["ready"]])
        self.assertIn("tad_boundary_pileup", [item["id"] for item in figure_types if item["selection_mode"] == "conversation"])
        self.assertIn("tad_insulation_track", [item["id"] for item in figure_types if item["selection_mode"] == "direct"])
        self.assertEqual(
            {item["category"] for item in figure_types},
            {"basic_hic", "comparison", "compartment", "tad", "loop", "pileup", "tracks", "differential"},
        )
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("CFIZZ Agent", response.text)
        self.assertIn('id="apiKey"', response.text)
        self.assertIn('id="chatTab"', response.text)
        self.assertIn('id="historyPanel"', response.text)
        self.assertIn('id="renderOverlay"', response.text)
        self.assertIn('id="languageSelect"', response.text)
        self.assertIn('<details id="datasetSourceShelf"', response.text)
        self.assertIn('id="datasetSourceList"', response.text)
        self.assertIn('id="datasetSourceCount"', response.text)
        self.assertIn('id="datasetAuthorizationBanner"', response.text)
        self.assertIn('id="dismissDatasetAuthorization"', response.text)
        self.assertIn('id="datasetRegionMode"', response.text)
        self.assertIn('id="datasetRegionStatus"', response.text)
        self.assertIn('id="referenceBuildStatus"', response.text)
        self.assertIn('data-reference-build="hg38"', response.text)
        self.assertIn('id="dataDialog"', response.text)
        self.assertIn('id="confirmDataDialog"', response.text)
        self.assertIn('id="chooseLocalFiles"', response.text)
        self.assertIn('id="chooseLocalFolder"', response.text)
        self.assertIn('id="localDatasetFiles"', response.text)
        self.assertIn('id="localDatasetFolder"', response.text)
        self.assertIn('webkitdirectory', response.text)
        self.assertIn('id="localUploadProgress"', response.text)
        self.assertIn('class="server-data-import"', response.text)
        self.assertIn('data-i18n="addDataStep"', response.text)
        self.assertIn('data-i18n="regionSettingsStep"', response.text)
        self.assertIn('id="apiDialog"', response.text)
        self.assertIn('id="figureTypeDialog"', response.text)
        self.assertIn('id="openDataDialog"', response.text)
        self.assertIn('id="openApiDialog"', response.text)
        self.assertIn('id="openFigureTypeDialog"', response.text)
        self.assertIn('id="figureTypeChoices"', response.text)
        self.assertIn('class="figure-availability-legend"', response.text)
        self.assertIn('class="figure-dialog-body"', response.text)
        self.assertIn('class="figure-settings-section figure-control-dock"', response.text)
        self.assertLess(response.text.index('id="figureTypeSelect"'), response.text.index('id="datasetResults"'))
        self.assertIn('id="figureReadinessHint"', response.text)
        self.assertIn('id="figureReadinessAction"', response.text)
        self.assertIn('id="previewQuality"', response.text)
        self.assertIn('id="figureZoomControl"', response.text)
        self.assertIn('id="figureZoom"', response.text)
        self.assertIn('id="fitFigure"', response.text)
        self.assertIn('id="svgDownload"', response.text)
        self.assertIn('id="pngDownload"', response.text)
        self.assertIn('id="pdfDownload"', response.text)
        self.assertNotIn('data-i18n-placeholder="chatPlaceholder" disabled', response.text)
        self.assertIn('data-i18n="inputDetails"', response.text)
        self.assertIn('欢迎使用 CFIZZ Agent。请先导入数据或载入示例开始绘图', response.text)
        self.assertNotIn('有的标签重叠了', response.text)
        stylesheet = await self.client.get("/assets/app.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".workspace", stylesheet.text)
        self.assertIn("backdrop-filter: none", stylesheet.text)
        self.assertIn(".figure-dialog-body", stylesheet.text)
        self.assertIn(".workflow-catalog.configuring > #workflowCatalog", stylesheet.text)
        script = await self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("function includedDatasetSources()", script.text)
        self.assertIn("source_paths:", script.text)
        self.assertIn("const preview = svg || png", script.text)
        self.assertIn("function updateFigureReadinessHint", script.text)
        self.assertIn("function projectedDatasetPathsForFigure", script.text)
        self.assertIn("选择后将自动补齐已导入的匹配文件", script.text)
        self.assertIn("function applyFigureZoom()", script.text)
        self.assertIn("function previewDatasetRegion", script.text)
        self.assertIn("function openDialog", script.text)
        self.assertIn("function updateDatasetWorkspaceSummary", script.text)
        self.assertNotIn("if (!options.refresh) closeDialog('dataDialog')", script.text)
        self.assertIn("const managingData = Boolean($('dataDialog')?.open)", script.text)
        self.assertIn("function syncFigureTypeTrigger", script.text)
        self.assertIn("function renderFigureTypeChoices", script.text)
        self.assertIn("function loadReferenceBuilds", script.text)
        self.assertIn("async function importLocalDataset", script.text)
        self.assertIn("function uploadLocalChunk", script.text)
        self.assertIn("LOCAL_UPLOAD_CHUNK_BYTES", script.text)
        self.assertIn("function selectedReferenceAnnotationPath", script.text)
        self.assertIn("function applyReferenceAnnotationChoice", script.text)
        self.assertIn("option.dataset.availability", script.text)
        self.assertIn("figure-choice-state", script.text)
        self.assertIn("function closeWorkflowConfigurator", script.text)
        self.assertNotIn("panel.scrollIntoView({behavior:'smooth'", script.text)
        self.assertIn("preview_only:true", script.text)
        self.assertIn("gene:datasetQuery()", script.text)
        self.assertIn("localStorage.setItem('cfizz-figure-zoom'", script.text)
        self.assertIn("document.createElement('details')", script.text)
        self.assertIn("fileSection.open = true", script.text)
        self.assertIn(".figure-choice-options button.is-ready", stylesheet.text)
        self.assertIn(".figure-choice-options button.is-missing", stylesheet.text)
        self.assertIn(".local-data-import", stylesheet.text)
        self.assertIn(".local-upload-progress", stylesheet.text)
        logo = await self.client.get("/assets/cfizz-brand-mark.png")
        self.assertEqual(logo.status_code, 200)
        self.assertEqual(logo.headers["content-type"], "image/png")
        self.assertGreater(len(logo.content), 1000)

    async def test_visualization_parameter_catalog_and_typed_session_updates(self):
        response = await self.client.get(
            "/api/visualization-parameters",
            params={"figure_type": "compartment_diff_scatter"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        figure = response.json()["figure_types"][0]
        self.assertEqual(figure["figure_type"], "compartment_diff_scatter")
        parameters = {item["parameter"]: item for item in figure["parameters"]}
        self.assertEqual(
            parameters["workflow_options.stable_a_color"]["group"], "color",
        )
        self.assertEqual(
            parameters["workflow_options.point_size"]["default_value"], 1,
        )
        self.assertEqual(
            (await self.client.get(
                "/api/visualization-parameters",
                params={"figure_type": "not-a-figure"},
            )).status_code,
            404,
        )

        created = await self.client.post(
            "/api/sessions",
            json={"session_id": "parameter_api", "spec": load_spec()},
        )
        self.assertEqual(created.status_code, 200, created.text)
        current = await self.client.get("/api/sessions/parameter_api/parameters")
        self.assertEqual(current.status_code, 200, current.text)
        gene_color = next(
            item for item in current.json()["parameters"]
            if item.get("target_id") == "genes_layer"
            and item["parameter"] == "style.color"
        )
        self.assertIn("comparison", gene_color)
        self.assertIn("default", gene_color["comparison"])

        changed = await self.client.post(
            "/api/sessions/parameter_api/parameters",
            json={
                "target_kind": "layer",
                "target_id": "genes_layer",
                "parameter": "style.color",
                "value": "#0072B2",
                "render": False,
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        changed_color = next(
            item for item in changed.json()["parameters"]
            if item.get("target_id") == "genes_layer"
            and item["parameter"] == "style.color"
        )
        self.assertEqual(changed_color["current_value"], "#0072B2")
        self.assertTrue(changed_color["is_modified"])

        confirmation = await self.client.post(
            "/api/sessions/parameter_api/parameters",
            json={
                "target_kind": "analysis",
                "parameter": "resolution",
                "value": 100_000,
                "render": False,
            },
        )
        self.assertEqual(confirmation.status_code, 409, confirmation.text)
        self.assertTrue(confirmation.json()["detail"]["requires_confirmation"])
        confirmed = await self.client.post(
            "/api/sessions/parameter_api/parameters",
            json={
                "target_kind": "analysis",
                "parameter": "resolution",
                "value": 100_000,
                "confirm_scientific_change": True,
                "render": False,
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["spec"]["analysis"]["resolution"], 100_000)

    async def test_blank_chat_session_accepts_first_message_without_rendering(self):
        """A fresh page can chat before any figure or data source is loaded."""
        blank = await self.client.post("/api/sessions/blank", json={"session_id": "blank_chat"})
        self.assertEqual(blank.status_code, 200, blank.text)
        payload = blank.json()
        self.assertTrue(payload["spec"]["metadata"]["draft"])
        self.assertEqual(payload["spec"]["data_sources"], [])

        response = await self.client.post(
            "/api/sessions/blank_chat/chat",
            json={"message": "你好", "render": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["job"])

    async def test_reference_and_dataset_scan_endpoints(self):
        references = await self.client.get("/api/references")
        self.assertEqual(references.status_code, 200)
        self.assertEqual(references.json()["references"][0]["id"], "hg38")
        self.assertEqual(
            [item["id"] for item in references.json()["references"]],
            ["hg38"],
        )
        response = await self.client.post("/api/datasets/scan", json={"path": "demo/data", "gene": "FOXJ1"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("FOXJ1", payload["summary"])
        self.assertFalse(payload["missing"])
        self.assertTrue(any(item["id"] == "integrated" for item in payload["capabilities"]))

    async def test_upload_dataset_file_is_scoped_to_authorized_scan_directory(self):
        directory = Path(self.runtime.name) / "upload-data"
        directory.mkdir()
        authorized = await self.client.post("/api/datasets/authorize", json={"path": str(directory)})
        self.assertEqual(authorized.status_code, 200, authorized.text)
        response = await self.client.post(
            "/api/datasets/upload",
            content=b"##gff-version 3\n",
            headers={"x-filename": "%E5%9F%BA%E5%9B%A0.gtf", "x-target-path": str(directory)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["filename"], "基因.gtf")
        self.assertEqual((directory / "基因.gtf").read_bytes(), b"##gff-version 3\n")

    async def test_local_dataset_upload_preserves_folder_structure_and_completes(self):
        session_id = "chat_upload_test"
        upload_id = "batch_12345678"
        content = b"##gff-version 3\nchr1\ttest\tgene\t1\t10\t.\t+\t.\tgene_id \"G1\";\n"
        relative_path = "case-a/%E5%9F%BA%E5%9B%A0.gtf"
        first = await self.client.post(
            f"/api/datasets/uploads/{session_id}/{upload_id}/files",
            content=content[:17],
            headers={
                "x-relative-path": relative_path,
                "x-file-size": str(len(content)),
                "x-chunk-offset": "0",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["complete"])
        second = await self.client.post(
            f"/api/datasets/uploads/{session_id}/{upload_id}/files",
            content=content[17:],
            headers={
                "x-relative-path": relative_path,
                "x-file-size": str(len(content)),
                "x-chunk-offset": "17",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["complete"])

        completed = await self.client.post(
            f"/api/datasets/uploads/{session_id}/{upload_id}/complete"
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        payload = completed.json()
        self.assertEqual(payload["file_count"], 1)
        root = Path(payload["path"])
        self.assertEqual(root, Path(self.runtime.name) / "uploads" / session_id / upload_id)
        self.assertEqual((root / "case-a" / "基因.gtf").read_bytes(), content)
        scanned = await self.client.post("/api/datasets/scan", json={"path": str(root)})
        self.assertEqual(scanned.status_code, 200, scanned.text)
        self.assertEqual(scanned.json()["files"][0]["name"], "基因.gtf")

    async def test_local_dataset_upload_rejects_traversal_and_unsupported_files(self):
        endpoint = "/api/datasets/uploads/chat_upload_test/batch_12345678/files"
        traversal = await self.client.post(
            endpoint,
            content=b"x",
            headers={
                "x-relative-path": quote("../escape.gtf"),
                "x-file-size": "1",
                "x-chunk-offset": "0",
            },
        )
        self.assertEqual(traversal.status_code, 422, traversal.text)
        unsupported = await self.client.post(
            endpoint,
            content=b"x",
            headers={
                "x-relative-path": quote("malware.exe"),
                "x-file-size": "1",
                "x-chunk-offset": "0",
            },
        )
        self.assertEqual(unsupported.status_code, 422, unsupported.text)

    async def test_create_session_from_dataset_builds_integrated_spec(self):
        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "dataset_session",
            "path": "demo/data",
            "gene": "FOXJ1",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "FOXJ1")
        self.assertEqual(payload["spec"]["figure_type"], "hic_triangle")
        self.assertGreaterEqual(len(payload["spec"]["panels"]), 2)
        self.assertIsNone(payload["job"])

    async def test_dataset_region_preview_does_not_persist_a_session(self):
        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "viewport_preview_test",
            "path": "demo/data",
            "gene": "chr17:75Mb-76Mb",
            "preview_only": True,
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["preview_only"])
        self.assertIsNone(payload["job"])
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr17")
        self.assertEqual(payload["spec"]["viewport"]["start"], 75_000_000)
        self.assertEqual(payload["spec"]["viewport"]["end"], 76_000_000)
        self.assertNotIn("viewport_preview_test", self.app.state.workspace.sessions)
        self.assertFalse((Path(self.runtime.name) / "sessions" / "viewport_preview_test.json").exists())

    async def test_manual_compartment_region_is_not_replaced_by_auto_window(self):
        case_dir = PROJECT_ROOT / "demo" / "cases" / "2121401"
        hic = case_dir / "1_2_pairs_result" / "51_5" / "51_5_1000.mcool"
        e1 = case_dir / "1_3_hicviz_output" / "1_computation" / "compartment" / "1_1.51_5_1000.51_5_1000.100kb.E1.tsv"
        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "manual_compartment_preview",
            "path": str(hic.parent),
            "source_paths": [str(hic.parent), str(e1.parent)],
            "selected_paths": [str(hic), str(e1)],
            "figure_type": "compartment",
            "gene": "chr1:1Mb-3Mb",
            "preview_only": True,
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        viewport = response.json()["spec"]["viewport"]
        self.assertEqual(viewport["chrom"], "chr1")
        self.assertEqual(viewport["start"], 1_000_000)
        self.assertEqual(viewport["end"], 3_000_000)
        self.assertEqual(viewport["focus_label"].upper(), "CHR1:1MB-3MB")
        self.assertNotIn("manual_compartment_preview", self.app.state.workspace.sessions)

    async def test_create_session_combines_hic_and_track_from_separate_sources(self):
        hic_root = Path(self.runtime.name) / "hic-source"
        track_root = Path(self.runtime.name) / "track-source"
        hic_root.mkdir()
        track_root.mkdir()
        hic_path = hic_root / "sample.mcool"
        track_path = track_root / "sample.bw"
        shutil.copy2(PROJECT_ROOT / "demo/data/hiPSC_nor_chr17.mcool", hic_path)
        shutil.copy2(PROJECT_ROOT / "demo/data/hiPSC_nor_chr17_mean.bw", track_path)

        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "cross_source_session",
            "path": str(hic_root),
            "source_paths": [str(hic_root), str(track_root)],
            "selected_paths": [str(hic_path), str(track_path)],
            "figure_type": "hic_triangle",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        selected_sources = {
            (source["type"], str(Path(source["path"]).resolve()))
            for source in payload["spec"]["data_sources"]
        }
        self.assertIn(("mcool", str(hic_path.resolve())), selected_sources)
        self.assertIn(("bigwig", str(track_path.resolve())), selected_sources)
        self.assertFalse(payload["dataset_scan"]["missing"])

    async def test_integrated_workflow_uses_registered_renderer(self):
        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "integrated_workflow",
            "path": "demo/data",
            "gene": "FOXJ1",
            "figure_type": "tracks_integrated",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        request = CfizzRenderAdapter(
            self.app.state.workspace.validator,
            str(Path(self.runtime.name) / "integrated-request"),
        ).build_request(payload["spec"])
        self.assertEqual(request.entrypoint, "cfizz.api.quick_plot_integrated")

    async def test_tad_boundary_workflow_uses_selected_tsvs_without_hic(self):
        """A result-only workflow must not fail because the directory has no
        selected cooler file.

        The UI enables this action when exactly two compatible boundary files
        are checked.  Keep the API and adapter on that same contract so a
        later execution cannot reintroduce the misleading missing-Hi-C error.
        """
        case_dir = PROJECT_ROOT / "demo" / "cases" / "2121401"
        boundaries = sorted(
            case_dir.glob(
                "1_3_hicviz_output/1_computation/tad/51_5/"
                "2_0.51_5_1000.10000.*b.boundaries.tsv"
            )
        )
        self.assertGreaterEqual(len(boundaries), 2)
        selected = [str(path.resolve()) for path in boundaries[:2]]
        response = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "tad_result_only",
            "path": str(case_dir),
            "selected_paths": selected,
            "figure_type": "tad_diff_stacked",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            {source["type"] for source in payload["spec"]["data_sources"]},
            {"tad_tsv"},
        )
        request = CfizzRenderAdapter(
            self.app.state.workspace.validator,
            str(Path(self.runtime.name) / "tad-diff-request"),
        ).build_request(payload["spec"])
        self.assertEqual(request.entrypoint, "cfizz.api.analyze_tad_difference")
        self.assertEqual(
            {request.kwargs["control_boundaries_path"], request.kwargs["treatment_boundaries_path"]},
            set(selected),
        )

    async def test_workflow_uses_exact_files_selected_in_its_configurator(self):
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        first = str((data_dir / "51_5_1000.mcool").resolve())
        second = str((data_dir / "51_6_1000.mcool").resolve())
        created = await self.client.post("/api/sessions/from-dataset", json={
            "session_id": "workflow_exact_inputs",
            "path": str(data_dir),
            "selected_paths": [first],
            "figure_type": "hic_triangle",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)

        response = await self.client.post(
            "/api/sessions/workflow_exact_inputs/workflow",
            json={
                "figure_type": "hic_multi",
                "dataset_path": str(data_dir),
                "selected_paths": [first, second],
                "reference_build": "hg38",
                "resolution": 5000,
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        hic_paths = {
            str(Path(source["path"]).resolve())
            for source in payload["spec"]["data_sources"]
            if source["type"] in {"cool", "mcool"}
        }
        self.assertEqual(hic_paths, {first, second})
        self.assertEqual(payload["spec"]["figure_type"], "hic_multi")
        self.assertIn("使用所选文件", payload["revision"]["summary"])

    async def test_chat_previews_and_confirms_fixed_workflow_from_dataset_path(self):
        """A chat request must use the same bounded CFIZZ workflow contract as the UI."""
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        first = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "chat_workflow_preview",
            "hic_path": str((data_dir / "51_5_1000.mcool").resolve()),
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(first.status_code, 200, first.text)

        preview = await self.client.post(
            "/api/sessions/chat_workflow_preview/chat",
            json={
                "message": f"数据路径是 {data_dir}，我要画双样本三角 Hi-C 对比图",
                "render": False,
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_payload = preview.json()
        self.assertEqual(preview_payload["intent"]["action"], "clarify")
        self.assertEqual(preview_payload["intent"]["planner"], "chat:workflow")
        self.assertIn("51_5_1000.mcool", preview_payload["intent"]["reply"])
        self.assertIn("51_6_1000.mcool", preview_payload["intent"]["reply"])
        self.assertIn("请回复“确认”", preview_payload["intent"]["reply"])
        self.assertEqual(preview_payload["version_id"], "v0001")

        confirmed = await self.client.post(
            "/api/sessions/chat_workflow_preview/chat",
            json={"message": "确认", "render": False},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        payload = confirmed.json()
        self.assertEqual(payload["intent"]["action"], "workflow_apply")
        self.assertEqual(payload["spec"]["figure_type"], "hic_triangle_multi")
        self.assertEqual(payload["version_id"], "v0002")
        hic_paths = {
            str(Path(source["path"]).resolve())
            for source in payload["spec"]["data_sources"]
            if source["type"] in {"cool", "mcool"}
        }
        self.assertEqual(
            hic_paths,
            {
                str((data_dir / "51_5_1000.mcool").resolve()),
                str((data_dir / "51_6_1000.mcool").resolve()),
            },
        )

    async def test_chat_workflow_reuses_path_from_previous_turn(self):
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "chat_workflow_history",
            "hic_path": str((data_dir / "51_5_1000.mcool").resolve()),
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)

        path_turn = await self.client.post(
            "/api/sessions/chat_workflow_history/chat",
            json={"message": f"我的实验数据路径是 {data_dir}", "render": False},
        )
        self.assertEqual(path_turn.status_code, 200, path_turn.text)
        follow_up = await self.client.post(
            "/api/sessions/chat_workflow_history/chat",
            json={"message": "然后画双样本三角 Hi-C 对比图", "render": False},
        )
        self.assertEqual(follow_up.status_code, 200, follow_up.text)
        payload = follow_up.json()
        self.assertEqual(payload["intent"]["planner"], "chat:workflow")
        self.assertIn("51_5_1000.mcool", payload["intent"]["reply"])
        self.assertIn("51_6_1000.mcool", payload["intent"]["reply"])

    async def test_chat_direct_hic_path_auto_discovers_required_companion(self):
        """A pasted HIC file should use the same CFIZZ companion resolver as the UI."""
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "chat_direct_hic",
            "hic_path": str((data_dir / "51_5_1000.mcool").resolve()),
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)

        response = await self.client.post(
            "/api/sessions/chat_direct_hic/chat",
            json={
                "message": f"用 {data_dir / '51_5_1000.mcool'} 画 TAD 绝缘图",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "clarify")
        self.assertEqual(payload["intent"]["planner"], "chat:workflow")
        self.assertIn("51_5_1000.mcool", payload["intent"]["reply"])
        self.assertIn("insulation.tsv", payload["intent"]["reply"])
        self.assertIn("请回复“确认”", payload["intent"]["reply"])

    async def test_chat_single_hic_for_two_sample_workflow_requests_missing_input(self):
        """The conversation path must enforce the workflow's exact HIC count."""
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "chat_hic_count",
            "hic_path": str((data_dir / "51_5_1000.mcool").resolve()),
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)

        response = await self.client.post(
            "/api/sessions/chat_hic_count/chat",
            json={
                "message": f"用 {data_dir / '51_5_1000.mcool'} 画双样本三角 Hi-C 对比图",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "clarify")
        self.assertIn("至少需要 2 个 Hi-C 文件", payload["intent"]["reply"])
        self.assertIn("明确给出文件路径", payload["intent"]["reply"])

    async def test_ai_workflow_identification_returns_to_chat_confirmation(self):
        class FakeWorkflowPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "fake", "model": "test"}

            def interpret(self, message, spec, history=None):
                return IntentResult(
                    "workflow",
                    "将执行双样本 Hi-C 对比。",
                    {"workflow_request": {"figure_type": "hic_multi", "source_ids": [], "options": {}}},
                    planner="fake:workflow",
                )

        self.app.state.workspace.planner_registry.planners["deepseek"] = FakeWorkflowPlanner()
        data_dir = PROJECT_ROOT / "demo" / "cases" / "2121401" / "data"
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "ai_chat_workflow",
            "hic_path": str((data_dir / "51_5_1000.mcool").resolve()),
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/ai_chat_workflow/chat",
            json={
                "message": f"请用 {data_dir} 生成双样本 Hi-C 图",
                "provider": "deepseek",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "clarify")
        self.assertEqual(payload["intent"]["planner"], "chat:workflow")
        self.assertIn("51_5_1000.mcool", payload["intent"]["reply"])

    async def test_chat_can_add_bigwigs_from_a_user_directory_to_current_hic(self):
        created = await self.client.post("/api/sessions", json={"session_id": "add_tracks", "spec": load_spec()})
        self.assertEqual(created.status_code, 200)
        service = self.app.state.workspace
        session = service.get("add_tracks")
        # Reduce the fixture to Hi-C only so the directory files are new.
        hic_sources = [source for source in session.current_spec["data_sources"] if source["type"] in {"cool", "mcool"}]
        hic_ids = {source["id"] for source in hic_sources}
        spec = session.current_spec
        spec["data_sources"] = hic_sources
        spec["panels"] = [{
            "id": "hic_panel", "kind": "hic_heatmap", "label": "Hi-C", "height_cm": "auto",
            "layers": [layer for layer in spec["panels"][0]["layers"] if layer["source_id"] in hic_ids],
        }]
        service.sessions.pop("add_tracks")
        service.create("add_tracks", spec, replace=True)
        directory = PROJECT_ROOT / "demo" / "data"
        response = await self.client.post(
            "/api/sessions/add_tracks/chat",
            json={"message": f"把这个目录里的 BigWig 加到当前图下面：{directory}", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "data:directory")
        bigwig_layers = [
            layer for panel in payload["spec"]["panels"] for layer in panel.get("layers", [])
            if layer.get("kind") == "bigwig"
        ]
        self.assertGreaterEqual(len(bigwig_layers), 8)

    async def test_colloquial_add_tracks_directory_is_scanned_before_ai(self):
        created = await self.client.post("/api/sessions", json={"session_id": "colloquial_tracks", "spec": load_spec()})
        self.assertEqual(created.status_code, 200)
        directory = PROJECT_ROOT / "demo" / "data"
        response = await self.client.post(
            "/api/sessions/colloquial_tracks/chat",
            json={
                "message": f"加一些track图，{directory}",
                "provider": "deepseek",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "data:directory")
        self.assertEqual(payload["intent"]["action"], "patch")

    async def test_directory_scan_followup_reuses_recent_user_path(self):
        created = await self.client.post("/api/sessions", json={"session_id": "track_followup", "spec": load_spec()})
        self.assertEqual(created.status_code, 200)
        service = self.app.state.workspace
        directory = PROJECT_ROOT / "demo" / "data"
        service.remember_dialogue(
            "track_followup",
            f"加一些track图，{directory}",
            "请说明轨道类型。",
        )
        response = await self.client.post(
            "/api/sessions/track_followup/chat",
            json={"message": "你自己识别一下", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "data:directory")
        self.assertEqual(payload["intent"]["action"], "patch")

    async def test_chat_adds_second_hic_as_cfizz_comparison(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "hic_comparison",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr11", "start": 15_470_000, "end": 17_240_000,
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/hic_comparison/chat",
            json={
                "message": "将 demo/cases/2121401/data/51_6_1000.mcool 作为新的 Hi-C 图层与当前图并排比较",
                "provider": "deepseek",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "data:hic-comparison")
        self.assertEqual(payload["intent"]["action"], "patch")
        hic_layers = [
            layer for panel in payload["spec"]["panels"] for layer in panel.get("layers", [])
            if layer.get("kind") == "hic"
        ]
        self.assertEqual(len(hic_layers), 2)
        self.assertEqual(hic_layers[0]["style"]["triangle_ratio"], 1)
        self.assertFalse(hic_layers[0]["style"]["flip_vertical"])
        self.assertEqual(hic_layers[1]["label"], "51_6_1000")
        self.assertEqual(hic_layers[1]["style"]["triangle_ratio"], 1)
        self.assertTrue(hic_layers[1]["style"]["flip_vertical"])
        adapter_request = CfizzRenderAdapter(
            self.app.state.workspace.validator,
            str(Path(self.runtime.name) / "comparison_request"),
        ).build_request(payload["spec"])
        self.assertEqual(adapter_request.entrypoint, "cfizz.api.quick_plot_integrated")
        self.assertEqual(len(adapter_request.kwargs["hics"]), 2)

    async def test_dataset_tracks_endpoint_adds_tracks_without_a_second_hic(self):
        spec = load_spec()
        hic_sources = [source for source in spec["data_sources"] if source["type"] in {"cool", "mcool"}][:1]
        hic_ids = {source["id"] for source in hic_sources}
        spec["data_sources"] = hic_sources
        spec["panels"] = [{
            "id": "hic_panel", "kind": "hic_heatmap", "label": "Hi-C", "height_cm": "auto",
            "layers": [layer for layer in spec["panels"][0]["layers"] if layer["source_id"] in hic_ids],
        }]
        created = await self.client.post("/api/sessions", json={"session_id": "button_tracks", "spec": spec})
        self.assertEqual(created.status_code, 200)
        response = await self.client.post(
            "/api/sessions/button_tracks/tracks/from-dataset",
            json={"path": "demo/data", "reference_build": "hg38"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json()["added"]["bigwig"], 1)
        repeated = await self.client.post(
            "/api/sessions/button_tracks/tracks/from-dataset",
            json={"path": "demo/data", "reference_build": "hg38"},
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["already_present"])
        self.assertEqual(repeated.json()["added"]["bigwig"], 0)

    async def test_configures_and_removes_api_without_echoing_key(self):
        secret = "browser-only-secret-key"
        registry = self.app.state.workspace.planner_registry

        class FakeConfiguredPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "deepseek-chat", "privacy": "内存配置"}

        def configure(provider, api_key, model):
            self.assertEqual((provider, api_key, model), ("deepseek", secret, "deepseek-chat"))
            registry.planners[provider] = FakeConfiguredPlanner()
            registry.unavailable.pop(provider, None)
            registry.default_provider = provider

        with patch.object(registry, "configure_runtime", side_effect=configure):
            response = await self.client.post("/api/planners/configure", json={
                "provider": "deepseek", "api_key": secret, "model": "deepseek-chat",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, response.text)
        self.assertEqual(response.json()["default_provider"], "deepseek")
        configured = next(item for item in response.json()["providers"] if item["id"] == "deepseek")
        self.assertTrue(configured["available"])

        removed = await self.client.delete("/api/planners/deepseek")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["default_provider"], "local")

    async def test_rejects_invalid_runtime_api_key(self):
        response = await self.client.post("/api/planners/configure", json={
            "provider": "openai", "api_key": "short", "model": None,
        })
        self.assertEqual(response.status_code, 422)

    async def test_loading_demo_replaces_stale_session_with_reference_defaults(self):
        first = await self.client.post("/api/sessions/demo", json={"session_id": "demo_replace"})
        self.assertEqual(first.status_code, 200)
        await self.client.post(
            "/api/sessions/demo_replace/chat",
            json={"message": "把图里的字体调大一点", "render": False},
        )
        second = await self.client.post("/api/sessions/demo", json={"session_id": "demo_replace"})
        payload = second.json()
        self.assertEqual(payload["version_id"], "v0001")
        self.assertEqual(payload["spec"]["layout"]["font_size"], 5)
        self.assertEqual(len(payload["spec"]["panels"][2]["layers"]), 8)

    async def test_create_chat_patch_and_get_session(self):
        created = await self.client.post("/api/sessions", json={"session_id": "test", "spec": load_spec()})
        self.assertEqual(created.status_code, 200)
        response = await self.client.post(
            "/api/sessions/test/chat",
            json={"message": "把 ATAC normal 改成绿色", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["version_id"], "v0002")
        self.assertIsNone(payload["job"])
        current = (await self.client.get("/api/sessions/test")).json()
        color = current["spec"]["panels"][2]["layers"][0]["style"]["color"]
        self.assertEqual(color, "#009E73")

    async def test_chat_applies_recommended_compartment_diff_palette_without_ai(self):
        spec = load_spec()
        spec["figure_type"] = "compartment_diff_scatter"
        spec["workflow_options"] = {}
        created = await self.client.post(
            "/api/sessions",
            json={"session_id": "compartment_diff_palette", "spec": spec},
        )
        self.assertEqual(created.status_code, 200, created.text)

        response = await self.client.post(
            "/api/sessions/compartment_diff_palette/chat",
            json={"message": "你推荐一套吧", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        self.assertEqual(payload["version_id"], "v0002")
        self.assertEqual(payload["spec"]["workflow_options"], {
            "stable_a_color": "#0072B2",
            "stable_b_color": "#009E73",
            "a_to_b_color": "#E69F00",
            "b_to_a_color": "#D55E00",
            "control_density_color": "#56B4E9",
            "treatment_density_color": "#CC79A7",
        })

    async def test_restores_a_selected_history_version_and_renders_it(self):
        await self.client.post("/api/sessions", json={"session_id": "restore_history", "spec": load_spec()})
        changed = await self.client.post(
            "/api/sessions/restore_history/chat",
            json={"message": "把图里的字体调大一点", "render": False},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["version_id"], "v0002")
        restored = await self.client.post("/api/sessions/restore_history/restore/v0001")
        self.assertEqual(restored.status_code, 200, restored.text)
        payload = restored.json()
        self.assertEqual(payload["version_id"], "v0001")
        self.assertTrue(payload["can_redo"])
        self.assertEqual(len(payload["history"]), 2)
        self.assertIsNotNone(payload["job"])

    async def test_scientific_change_waits_for_confirmation(self):
        await self.client.post("/api/sessions", json={"session_id": "test", "spec": load_spec()})
        response = (await self.client.post(
            "/api/sessions/test/chat",
            json={"message": "分辨率改成 100k", "render": False},
        )).json()
        self.assertTrue(response["intent"]["requires_confirmation"])
        self.assertEqual(response["version_id"], "v0001")

    async def test_ai_planner_result_flows_through_patch_engine(self):
        class FakeAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "fake", "model": "test"}

            def interpret(self, message, spec):
                return IntentResult(
                    "patch",
                    "将 ATAC normal 改成绿色。",
                    {
                        "summary": "AI test edit",
                        "operations": [{
                            "op": "update",
                            "target_kind": "layer",
                            "target_id": "atac_normal_layer",
                            "field": "style.color",
                            "value": "#009E73",
                        }],
                    },
                    planner="fake:test",
                )

        self.app.state.workspace.planner_registry.planners["deepseek"] = FakeAiPlanner()
        await self.client.post("/api/sessions", json={"session_id": "ai", "spec": load_spec()})
        response = await self.client.post(
            "/api/sessions/ai/chat",
            json={
                "message": "用更像森林的颜色表现第一条开放染色质轨道",
                "provider": "deepseek",
                "render": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "fake:test")
        self.assertEqual(payload["spec"]["panels"][2]["layers"][0]["style"]["color"], "#009E73")

    async def test_gene_name_in_layout_complaint_is_sent_to_ai_not_gene_locator(self):
        class FakeAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

            def interpret(self, message, spec, history=None):
                self.called = message
                return IntentResult(
                    "patch",
                    "MYC 标签拥挤，增高基因注释面板。",
                    {"summary": "解决 MYC 标签重叠", "operations": [{
                        "op": "update", "target_kind": "panel", "target_id": "annotation_panel",
                        "field": "height_cm", "value": 2.2,
                    }]},
                    planner="deepseek:test",
                )

        planner = FakeAiPlanner()
        self.app.state.workspace.planner_registry.planners["deepseek"] = planner
        await self.client.post("/api/sessions", json={"session_id": "gene_label_overlap", "spec": load_spec()})
        response = await self.client.post(
            "/api/sessions/gene_label_overlap/chat",
            json={"message": "MYC这个标签重叠了，有点看不清，你可以处理吗", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(planner.called, "MYC这个标签重叠了，有点看不清，你可以处理吗")
        self.assertEqual(payload["intent"]["planner"], "deepseek:test")
        self.assertEqual(payload["spec"]["viewport"], load_spec()["viewport"])
        annotation = next(panel for panel in payload["spec"]["panels"] if panel["id"] == "annotation_panel")
        self.assertEqual(annotation["height_cm"], 2.2)

    async def test_ai_decides_hic_annotation_question_is_not_a_gene_lookup(self):
        class FakeAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

            def interpret(self, message, spec, history=None):
                self.called = message
                return IntentResult(
                    "answer",
                    "这些是下方基因轨道溢出的标签，不是 Hi-C 热图自身的注释。",
                    planner="deepseek:test",
                )

        planner = FakeAiPlanner()
        self.app.state.workspace.planner_registry.planners["deepseek"] = planner
        await self.client.post("/api/sessions", json={"session_id": "hic_gene_question", "spec": load_spec()})
        before = (await self.client.get("/api/sessions/hic_gene_question")).json()
        response = await self.client.post(
            "/api/sessions/hic_gene_question/chat",
            json={"message": "Hi-C图上有一些标注，这是什么，也是基因吗？", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(planner.called, "Hi-C图上有一些标注，这是什么，也是基因吗？")
        self.assertEqual(payload["intent"]["action"], "answer")
        self.assertEqual(payload["version_id"], before["version_id"])

    async def test_ai_gene_semantics_are_resolved_against_local_hg38(self):
        class FakeAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

            def interpret(self, message, spec, history=None):
                return IntentResult(
                    "reference",
                    "识别为绘制 MYC 基因轨道。",
                    {"reference_request": {"kind": "annotate_gene", "gene_symbol": "MYC"}},
                    planner="deepseek:test",
                )

        self.app.state.workspace.planner_registry.planners["deepseek"] = FakeAiPlanner()
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "ai_reference_gene",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/ai_reference_gene/chat",
            json={"message": "帮我画一下MYC", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr8")
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "MYC")
        self.assertIn("deepseek:test→reference:hg38", payload["intent"]["planner"])

    async def test_square_comparison_does_not_claim_gene_track_was_rendered(self):
        class FakeAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

            def interpret(self, message, spec, history=None):
                return IntentResult(
                    "answer",
                    "当前图已经包含 MYC 基因轨道，无需再添加。",
                    planner="deepseek:test",
                )

        spec = load_spec()
        spec["figure_type"] = "hic_multi"
        self.app.state.workspace.planner_registry.planners["deepseek"] = FakeAiPlanner()
        await self.client.post("/api/sessions", json={"session_id": "square_gene_guard", "spec": spec})
        response = await self.client.post(
            "/api/sessions/square_gene_guard/chat",
            json={"message": "把 MYC 基因也标上", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "answer")
        self.assertIn("没有基因轨道面板", payload["intent"]["reply"])
        self.assertIn("不能声称", payload["intent"]["reply"])

    async def test_ai_gene_choice_question_is_overridden_for_explicit_draw_command(self):
        class IndecisiveAiPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

            def interpret(self, message, spec, history=None):
                return IntentResult(
                    "clarify",
                    "请选择 A 添加 TP53 基因轨道，或 B 只切换区域。",
                    planner="deepseek:test",
                )

        self.app.state.workspace.planner_registry.planners["deepseek"] = IndecisiveAiPlanner()
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "explicit_tp53",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/explicit_tp53/chat",
            json={"message": "画 TP53基因", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        self.assertIn("explicit-fallback", payload["intent"]["planner"])
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr17")
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "TP53")
        gene_layer = next(
            layer for panel in payload["spec"]["panels"] for layer in panel.get("layers", [])
            if layer.get("kind") == "genes"
        )
        self.assertEqual(gene_layer["label"], "TP53")

    async def test_chat_answer_does_not_modify_or_render(self):
        class AnswerPlanner:
            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "deepseek-chat"}

            async def interpret_async(self, message, spec):
                return IntentResult("answer", "当前使用 deepseek-chat。", planner="system:deepseek")

        self.app.state.workspace.planner_registry.planners["deepseek"] = AnswerPlanner()
        await self.client.post("/api/sessions", json={"session_id": "answer", "spec": load_spec()})
        before = (await self.client.get("/api/sessions/answer")).json()
        response = await self.client.post(
            "/api/sessions/answer/chat",
            json={"message": "你是什么模型", "provider": "deepseek"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "answer")
        self.assertIsNone(payload["job"])
        self.assertEqual(payload["version_id"], before["version_id"])

    async def test_gene_location_uses_reference_and_confirmation_applies_viewport(self):
        await self.client.post("/api/sessions", json={"session_id": "gene", "spec": load_spec()})
        first = await self.client.post(
            "/api/sessions/gene/chat",
            json={"message": "FOXJ1基因可以画吗", "provider": "deepseek", "render": False},
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_payload = first.json()
        self.assertEqual(first_payload["intent"]["action"], "clarify")
        self.assertIn("76,136,332", first_payload["intent"]["reply"])
        self.assertEqual(first_payload["version_id"], "v0001")

        second = await self.client.post(
            "/api/sessions/gene/chat",
            json={"message": "确定", "provider": "deepseek", "render": False},
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_payload = second.json()
        self.assertEqual(second_payload["intent"]["action"], "patch")
        self.assertEqual(second_payload["version_id"], "v0002")
        self.assertEqual(second_payload["spec"]["viewport"]["chrom"], "chr17")

    async def test_gene_annotation_adds_bundled_reference_track(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "gene_track",
            "hic_path": "demo/data/hiPSC_nor_chr17.mcool",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/gene_track/chat",
            json={"message": "标注 FOXJ1 对应的基因", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        sources = payload["spec"]["data_sources"]
        self.assertTrue(any(source["id"] == "reference_genes_hg38" for source in sources))
        genes = [layer for panel in payload["spec"]["panels"] for layer in panel["layers"] if layer["kind"] == "genes"]
        self.assertEqual(len(genes), 1)

    async def test_gene_annotation_moves_chr1_window_to_gene_and_supports_other_bundled_genes(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "case_gene_window",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/case_gene_window/chat",
            json={"message": "标注 ACOX1", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr17")
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "ACOX1")
        self.assertIn("ACOX1", payload["intent"]["reply"])

    async def test_gene_drawing_command_uses_authoritative_myc_location_and_adds_track(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "draw_myc",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/draw_myc/chat",
            json={"message": "改成MYC基因", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        self.assertEqual(payload["intent"]["planner"], "reference:hg38")
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr8")
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "MYC")
        self.assertLess(payload["spec"]["viewport"]["start"], 127_735_433)
        self.assertGreater(payload["spec"]["viewport"]["end"], 127_742_951)
        genes = [
            layer for panel in payload["spec"]["panels"] for layer in panel["layers"]
            if layer["kind"] == "genes"
        ]
        self.assertEqual(len(genes), 1)
        self.assertEqual(genes[0]["label"], "MYC")

    async def test_mistyped_gene_is_clarified_without_changing_figure(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "mistyped_gene",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        before = created.json()
        response = await self.client.post(
            "/api/sessions/mistyped_gene/chat",
            json={"message": "画MCY", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "clarify")
        self.assertEqual(payload["intent"]["planner"], "reference:hg38")
        self.assertIn("MYC", payload["intent"]["reply"])
        self.assertEqual(payload["version_id"], before["version_id"])
        self.assertEqual(payload["spec"]["viewport"], before["spec"]["viewport"])

    async def test_arbitrary_gene_uses_complete_reference_region_track(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "complete_reference_gene",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post(
            "/api/sessions/complete_reference_gene/chat",
            json={"message": "标注 TP53 基因", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["action"], "patch")
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr17")
        self.assertEqual(payload["spec"]["viewport"]["focus_label"], "TP53")
        source = next(item for item in payload["spec"]["data_sources"] if item["id"] == "reference_genes_hg38")
        self.assertIn("reference_cache", source["path"])
        self.assertTrue(Path(source["path"]).is_file())
        self.assertIn("Ensembl 110", source["label"])

    async def test_user_can_replace_single_gene_track_with_all_genes_in_viewport(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "region_genes",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr8", "start": 127_235_433, "end": 128_242_951,
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        single = await self.client.post(
            "/api/sessions/region_genes/chat", json={"message": "标注 MYC", "render": False},
        )
        self.assertEqual(single.status_code, 200, single.text)
        response = await self.client.post(
            "/api/sessions/region_genes/chat",
            json={"message": "把这段上其他的基因也标出来吧", "render": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["intent"]["planner"], "reference:hg38")
        source = next(item for item in payload["spec"]["data_sources"] if item["id"] == "reference_genes_hg38")
        self.assertIn("regions", source["path"])
        self.assertEqual(
            next(layer for panel in payload["spec"]["panels"] for layer in panel["layers"] if layer["kind"] == "genes")["label"],
            "区域内全部基因",
        )
        self.assertEqual(
            next(layer for panel in payload["spec"]["panels"] for layer in panel["layers"] if layer["kind"] == "genes")["height_cm"],
            3.5,
        )
        self.assertEqual(
            next(panel for panel in payload["spec"]["panels"] if panel["id"] == "annotation_panel")["height_cm"],
            3.5,
        )

        back_to_single = await self.client.post(
            "/api/sessions/region_genes/chat",
            json={"message": "只画 BRCA1 基因", "render": False},
        )
        self.assertEqual(back_to_single.status_code, 200, back_to_single.text)
        single_payload = back_to_single.json()
        single_source = next(
            item for item in single_payload["spec"]["data_sources"]
            if item["id"] == "reference_genes_hg38"
        )
        single_layer = next(
            layer for panel in single_payload["spec"]["panels"] for layer in panel["layers"]
            if layer["kind"] == "genes"
        )
        self.assertNotIn("/regions/", single_source["path"].replace("\\", "/"))
        self.assertEqual(single_source["label"], "Genes · Ensembl 110 / hg38")
        self.assertEqual(single_payload["spec"]["viewport"]["focus_label"], "BRCA1")
        self.assertEqual(single_layer["label"], "BRCA1")
        self.assertEqual(single_layer["height_cm"], 0.5)
        self.assertEqual(
            next(panel for panel in single_payload["spec"]["panels"] if panel["id"] == "annotation_panel")["height_cm"],
            0.5,
        )

    async def test_annotation_followup_questions_answer_capabilities_without_redrawing(self):
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "annotation_questions",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        added = await self.client.post(
            "/api/sessions/annotation_questions/chat",
            json={"message": "标注 FOXJ1", "render": False},
        )
        self.assertEqual(added.status_code, 200, added.text)
        version = added.json()["version_id"]

        other_genes = await self.client.post(
            "/api/sessions/annotation_questions/chat",
            json={"message": "还有其他可以标注的吗", "render": True},
        )
        payload = other_genes.json()
        self.assertEqual(payload["intent"]["action"], "answer")
        self.assertEqual(payload["version_id"], version)
        self.assertIsNone(payload["job"])
        self.assertIn("62,754 条基因记录", payload["intent"]["reply"])
        self.assertIn("TP53", payload["intent"]["reply"])

        other_tracks = await self.client.post(
            "/api/sessions/annotation_questions/chat",
            json={"message": "除了基因注释轨道呢", "render": True},
        )
        payload = other_tracks.json()
        self.assertEqual(payload["intent"]["action"], "answer")
        self.assertEqual(payload["version_id"], version)
        self.assertIsNone(payload["job"])
        self.assertIn("BigWig", payload["intent"]["reply"])
        self.assertIn("Loop", payload["intent"]["reply"])

    async def test_create_from_hic_uses_inspected_defaults(self):
        response = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "from_hic",
            "hic_path": "demo/data/hiPSC_nor_chr17.mcool",
            "start": 75_400_000,
            "end": 76_340_000,
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["spec"]["viewport"]["chrom"], "chr17")
        self.assertEqual(payload["spec"]["analysis"]["resolution"], 10_000)
        self.assertEqual(payload["inspection"]["metadata"]["inspection_level"], "full")
        self.assertEqual(payload["spec"]["metadata"]["reference"]["id"], "hg38")

    async def test_coordinate_only_reference_is_preserved_and_never_falls_back_to_hg38(self):
        response = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "from_hic_hg19",
            "hic_path": "demo/data/hiPSC_nor_chr17.mcool",
            "reference_build": "hg19",
            "chrom": "chr17",
            "start": 75_400_000,
            "end": 76_340_000,
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        reference = payload["spec"]["metadata"]["reference"]
        self.assertEqual((reference["id"], reference["assembly"]), ("hg19", "GRCh37"))
        self.assertFalse(reference["complete"])

        chat = await self.client.post(
            "/api/sessions/from_hic_hg19/chat",
            json={"message": "标注 MYC 基因", "render": False},
        )
        self.assertEqual(chat.status_code, 200, chat.text)
        result = chat.json()
        self.assertEqual(result["intent"]["action"], "clarify")
        self.assertEqual(result["intent"]["planner"], "reference:hg19")
        self.assertIn("匹配 hg19 的 GTF/GFF", result["intent"]["reply"])
        self.assertFalse(any(
            source.get("id") == "reference_genes_hg38"
            for source in result["spec"]["data_sources"]
        ))

    async def test_create_from_hic_accepts_ready_figure_type(self):
        response = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "square_hic",
            "hic_path": "demo/data/hiPSC_nor_chr17.mcool",
            "start": 75_400_000,
            "end": 76_340_000,
            "figure_type": "hic_square",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["spec"]["figure_type"], "hic_square")

    async def test_create_compartment_session_discovers_e1_and_resolution(self):
        source = PROJECT_ROOT / "demo/cases/2121401/data/51_5_1000.mcool"
        if not source.exists():
            self.skipTest("local case 2121401 is not installed")
        response = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "case_compartment",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "start": 10_000_000,
            "end": 30_000_000,
            "figure_type": "compartment",
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        spec = response.json()["spec"]
        self.assertEqual(spec["analysis"]["resolution"], 100_000)
        self.assertEqual(spec["data_sources"][1]["type"], "compartment_tsv")
        self.assertIn("51_5_1000", spec["data_sources"][1]["path"])

    async def test_switching_to_compartment_selects_broad_informative_region(self):
        source = PROJECT_ROOT / "demo/cases/2121401/data/51_5_1000.mcool"
        if not source.exists():
            self.skipTest("local case 2121401 is not installed")
        created = await self.client.post("/api/sessions/from-hic", json={
            "session_id": "case_compartment_switch",
            "hic_path": "demo/cases/2121401/data/51_5_1000.mcool",
            "chrom": "chr1",
            "render": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        response = await self.client.post("/api/sessions/case_compartment_switch/patch", json={
            "patch": {"summary": "switch", "operations": [{
                "op": "update", "target_kind": "figure", "field": "figure_type", "value": "compartment",
            }]},
            "render": False,
        })
        self.assertEqual(response.status_code, 200, response.text)
        spec = response.json()["spec"]
        self.assertEqual(spec["analysis"]["resolution"], 100_000)
        self.assertGreaterEqual(spec["viewport"]["end"] - spec["viewport"]["start"], 20_000_000)

    async def test_invalid_session_is_404(self):
        self.assertEqual((await self.client.get("/api/sessions/missing")).status_code, 404)

    async def test_unconfigured_provider_is_rejected_without_exposing_keys(self):
        await self.client.post("/api/sessions", json={"session_id": "provider", "spec": load_spec()})
        response = await self.client.post(
            "/api/sessions/provider/chat",
            json={"message": "让这张图更清楚", "provider": "deepseek", "render": False},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("DEEPSEEK_API_KEY", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
