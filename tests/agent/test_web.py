import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertEqual((await self.client.get("/api/health")).json()["status"], "ok")
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
        self.assertIn('id="svgDownload"', response.text)
        self.assertIn('id="pngDownload"', response.text)
        self.assertIn('id="pdfDownload"', response.text)
        self.assertIn('data-i18n="combinedWorkflows"', response.text)
        stylesheet = await self.client.get("/assets/app.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".workspace", stylesheet.text)

    async def test_reference_and_dataset_scan_endpoints(self):
        references = await self.client.get("/api/references")
        self.assertEqual(references.status_code, 200)
        self.assertEqual(references.json()["references"][0]["id"], "hg38")
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
