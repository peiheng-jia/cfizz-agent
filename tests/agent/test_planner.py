import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from cfizz.agent.planner import (
    DeepSeekPlanner,
    OpenAIPlanner,
    PlannerRegistry,
    ParameterEdit,
    PlannedEdit,
    PlannerOutput,
    ReferenceRequest,
    RuleFirstPlanner,
    build_planner_registry_from_env,
    compile_plan,
    public_figure_context,
)
from cfizz.agent.parameters import (
    ParameterCatalog,
    validate_workflow_options,
    visualization_parameter_catalog,
    workflow_parameter_names,
)
from cfizz.agent.figure_types import FIGURE_TYPES
from cfizz.agent.capabilities import CapabilityCall, TargetSelector


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def edit(edit_type, **overrides):
    values = {
        "edit_type": edit_type,
        "target_id": None,
        "chrom": None,
        "start": None,
        "end": None,
        "number_value": None,
        "string_value": None,
        "bool_value": None,
    }
    values.update(overrides)
    return PlannedEdit(**values)


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.call = None

    def parse(self, **kwargs):
        self.call = kwargs
        return type("Response", (), {"output_parsed": self.output})()


class FakeClient:
    def __init__(self, output):
        self.responses = FakeResponses(output)


class FakeChatCompletions:
    def __init__(self, output):
        self.output = output
        self.call = None

    def create(self, **kwargs):
        self.call = kwargs
        message = type("Message", (), {"content": self.output.model_dump_json()})()
        return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()


class FakeDeepSeekClient:
    def __init__(self, output):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeChatCompletions(output)


class NeverAiPlanner:
    def interpret(self, message, spec):
        raise AssertionError("AI should not be called")

    def status(self):
        return {"mode": "ai-assisted", "provider": "fake", "model": "fake"}


class PlannerTests(unittest.TestCase):
    def test_visualization_registry_covers_every_ready_figure_type(self):
        catalog = visualization_parameter_catalog()
        ready = {item["id"] for item in FIGURE_TYPES if item["ready"]}
        self.assertEqual({item["figure_type"] for item in catalog}, ready)
        for figure in catalog:
            parameters = figure["parameters"]
            self.assertTrue(parameters, figure["figure_type"])
            keys = [
                (item["target_kind"], item["parameter"], tuple(item.get("layer_kinds", [])))
                for item in parameters
            ]
            self.assertEqual(len(keys), len(set(keys)), figure["figure_type"])

        scatter = next(
            item for item in catalog
            if item["figure_type"] == "compartment_diff_scatter"
        )
        scatter_names = {item["parameter"] for item in scatter["parameters"]}
        self.assertIn("workflow_options.point_size", scatter_names)
        self.assertIn("workflow_options.width_cm", scatter_names)

    def test_workflow_validation_is_driven_by_the_registry_and_allows_auto_reset(self):
        names = workflow_parameter_names("tad_diff_stacked")
        self.assertIn("stable_color", names)
        self.assertIn("window_mult", names)
        self.assertEqual(
            validate_workflow_options(
                "tad_diff_stacked",
                {"stable_color": "#123456", "window_mult": 50},
            ),
            {"stable_color": "#123456", "window_mult": 50},
        )
        with self.assertRaisesRegex(ValueError, "不支持工作流参数"):
            validate_workflow_options("tad_diff_stacked", {"unknown": 1})

        spec = load_spec()
        spec["figure_type"] = "hic_multi"
        spec["workflow_options"] = {"vmin": 2}
        operation, scientific = ParameterCatalog(spec).compile(ParameterEdit(
            target_kind="figure",
            parameter="workflow_options.vmin",
            value=None,
        ))
        self.assertIsNone(operation["value"])
        self.assertFalse(scientific)

    def test_ai_reference_request_is_typed_but_not_allowed_to_supply_coordinates(self):
        output = PlannerOutput(
            action="reference",
            reply="定位并标注 MYC。",
            reference_request=ReferenceRequest(kind="annotate_gene", gene_symbol="MYC"),
        )
        result = compile_plan(output, load_spec(), planner="deepseek:test")
        self.assertEqual(result.action, "reference")
        self.assertEqual(result.patch["reference_request"], {
            "kind": "annotate_gene", "gene_symbol": "MYC",
        })
        self.assertNotIn("chrom", result.patch["reference_request"])

    def test_runtime_parameter_catalog_only_exposes_real_targets(self):
        catalog = ParameterCatalog(load_spec()).model_catalog()
        layer_entries = [item for item in catalog if item["target_kind"] == "layer"]
        self.assertTrue(any(item["target_id"] == "genes_layer" and item["parameter"] == "style.fontsize" for item in layer_entries))
        self.assertTrue(any(item["target_id"] == "genes_layer" and item["parameter"] == "height_cm" for item in layer_entries))
        self.assertFalse(any(item["target_id"] == "genes_layer" and item["parameter"] == "style.cmap" for item in layer_entries))
        self.assertTrue(any(item["target_id"] == "hic_normal_layer" and item["parameter"] == "style.cmap" for item in layer_entries))
        self.assertFalse(any(
            item["target_kind"] == "panel" and item["target_id"] == "annotation_panel" and item["parameter"] == "height_cm"
            for item in catalog
        ))

    def test_loop_workflow_catalog_separates_marker_size_from_triangle_height(self):
        spec = load_spec()
        spec["figure_type"] = "loop_multi"
        catalog = ParameterCatalog(spec).model_catalog()
        workflow = [
            item for item in catalog
            if item["target_kind"] == "figure" and item["parameter"].startswith("workflow_options.")
        ]
        loop_size = next(item for item in workflow if item["parameter"] == "workflow_options.loop_size")
        self.assertEqual(loop_size["current_value"], 50)
        self.assertIn("set", loop_size["operations"])
        self.assertFalse(any(item["parameter"] == "style.triangle_ratio" for item in catalog))

        output = PlannerOutput(
            action="patch", reply="缩小 Loop 圈。",
            parameter_edits=[ParameterEdit(
                target_kind="figure", parameter="workflow_options.loop_size",
                operation="set", value=20,
            )],
        )
        operation = compile_plan(output, spec).patch["operations"][0]
        self.assertEqual(operation["field"], "workflow_options.loop_size")
        self.assertEqual(operation["value"], 20.0)

        single_loop_spec = load_spec()
        single_loop_spec["figure_type"] = "loop_heatmap"
        single_loop_parameters = ParameterCatalog(single_loop_spec).model_catalog()
        self.assertFalse(any(
            item["parameter"] == "style.triangle_ratio"
            for item in single_loop_parameters
        ))
        self.assertTrue(any(
            item["parameter"] == "style.loop_size"
            for item in single_loop_parameters
        ))

    def test_local_loop_size_fallback_never_changes_triangle_height(self):
        spec = load_spec()
        spec["figure_type"] = "loop_multi"
        result = RuleFirstPlanner(NeverAiPlanner()).interpret("loop圈有点太大了，变小一些吧", spec)
        self.assertEqual(result.action, "patch")
        self.assertEqual(result.patch["operations"][0]["field"], "workflow_options.loop_size")
        self.assertNotIn("triangle_ratio", json.dumps(result.patch, ensure_ascii=False))

    def test_compartment_diff_palette_is_exposed_and_applied_locally(self):
        spec = load_spec()
        spec["figure_type"] = "compartment_diff_scatter"
        spec["workflow_options"] = {}
        parameters = {
            item["parameter"] for item in ParameterCatalog(spec).model_catalog()
            if item["target_kind"] == "figure"
        }
        expected = {
            "workflow_options.stable_a_color",
            "workflow_options.stable_b_color",
            "workflow_options.a_to_b_color",
            "workflow_options.b_to_a_color",
            "workflow_options.control_density_color",
            "workflow_options.treatment_density_color",
        }
        self.assertTrue(expected.issubset(parameters))

        result = RuleFirstPlanner(NeverAiPlanner()).interpret("你推荐一套吧", spec)
        self.assertEqual(result.action, "patch")
        self.assertEqual(
            {operation["field"] for operation in result.patch["operations"]},
            expected,
        )

    def test_generic_parameter_edit_can_increase_current_value(self):
        output = PlannerOutput(
            action="patch",
            reply="增加右侧留白。",
            parameter_edits=[ParameterEdit(
                target_kind="layout", parameter="right_margin_cm", operation="increase", value=1.25
            )],
        )
        operation = compile_plan(output, load_spec()).patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["field"]), ("layout", "right_margin_cm"))
        self.assertAlmostEqual(operation["value"], load_spec()["layout"]["right_margin_cm"] + 1.25)

    def test_generic_parameter_edit_can_toggle_real_layer(self):
        output = PlannerOutput(
            action="patch",
            reply="隐藏基因轨道。",
            parameter_edits=[ParameterEdit(
                target_kind="layer", target_id="genes_layer", parameter="visible", operation="toggle"
            )],
        )
        operation = compile_plan(output, load_spec()).patch["operations"][0]
        self.assertEqual(operation["target_id"], "genes_layer")
        self.assertFalse(operation["value"])

    def test_generic_parameter_cannot_hide_only_visible_hic_layer(self):
        spec = load_spec()
        hic_layers = [layer for panel in spec["panels"] for layer in panel["layers"] if layer["kind"] == "hic"]
        hic_layers[1]["visible"] = False
        with self.assertRaisesRegex(ValueError, "至少需要一个可见 Hi-C"):
            compile_plan(PlannerOutput(
                action="patch", reply="隐藏热图。",
                parameter_edits=[ParameterEdit(
                    target_kind="layer", target_id=hic_layers[0]["id"], parameter="visible", value=False
                )],
            ), spec)

    def test_agent_does_not_expose_non_cfizz_gene_filter_parameters(self):
        genes = [item for item in ParameterCatalog(load_spec()).model_catalog() if item.get("target_id") == "genes_layer"]
        self.assertNotIn("style.focus_gene", {item["parameter"] for item in genes})
        self.assertNotIn("style.focus_only", {item["parameter"] for item in genes})

    def test_generic_parameter_rejects_unadvertised_path_and_clamps_visual_range(self):
        with self.assertRaisesRegex(ValueError, "不存在可编辑参数"):
            compile_plan(PlannerOutput(
                action="patch", reply="任意修改。",
                parameter_edits=[ParameterEdit(
                    target_kind="layer", target_id="genes_layer", parameter="style.unknown", value=1
                )],
            ), load_spec())
        result = compile_plan(PlannerOutput(
            action="patch", reply="字体超大。",
            parameter_edits=[ParameterEdit(
                target_kind="layout", parameter="font_size", value=1000
            )],
        ), load_spec())
        self.assertEqual(result.patch["operations"][0]["value"], 24.0)
        self.assertIn("请求值 1000", result.reply)
        self.assertIn("实际应用 24", result.reply)

        with self.assertRaisesRegex(ValueError, "不能大于"):
            compile_plan(PlannerOutput(
                action="patch", reply="分辨率超大。",
                parameter_edits=[ParameterEdit(
                    target_kind="analysis", parameter="resolution", value=1_000_000_000
                )],
            ), load_spec())

    def test_generic_scientific_parameter_still_requires_confirmation(self):
        output = PlannerOutput(
            action="patch", reply="关闭 balance。",
            parameter_edits=[ParameterEdit(
                target_kind="analysis", parameter="balance", operation="set", value=False
            )],
        )
        self.assertTrue(compile_plan(output, load_spec()).requires_confirmation)

    def test_model_capability_call_compiles_relative_track_target(self):
        output = PlannerOutput(
            action="patch",
            reply="统一下面四条信号轨道的 y 轴。",
            edits=[],
            calls=[CapabilityCall(
                capability_id="tracks.sync_y_axis",
                target=TargetSelector(
                    scope="layer", panel_id="signal_panel", position="last", count=4, kinds=["bigwig"]
                ),
                arguments_json='{"mode":"auto"}',
            )],
        )
        result = compile_plan(output, load_spec(), planner="fake")
        self.assertEqual(len(result.patch["operations"]), 4)
        self.assertEqual({item["field"] for item in result.patch["operations"]}, {"style.y_scale_group"})

    def test_context_never_contains_source_paths(self):
        spec = load_spec()
        context = public_figure_context(spec)
        encoded = json.dumps(context)
        self.assertNotIn("path", encoded)
        self.assertNotIn("demo/data", encoded)
        self.assertIn("atac_normal_layer", encoded)
        self.assertEqual(context["figure_type"], spec["figure_type"])

    def test_model_plan_compiles_to_allowlisted_patch(self):
        output = PlannerOutput(
            action="patch",
            reply="把 ATAC normal 调成绿色并增高信号面板。",
            edits=[
                edit("set_layer_color", target_id="atac_normal_layer", string_value="#009E73"),
                edit("set_panel_height", target_id="signal_panel", number_value=5),
            ],
        )
        result = compile_plan(output, load_spec(), planner="fake")
        self.assertEqual(result.action, "patch")
        self.assertEqual(result.planner, "fake")
        self.assertFalse(result.requires_confirmation)
        self.assertEqual(result.patch["operations"][0]["field"], "style.color")

    def test_scientific_edit_requires_server_side_confirmation(self):
        output = PlannerOutput(
            action="patch",
            reply="把分辨率改成 5 kb。",
            edits=[edit("set_resolution", number_value=5000)],
        )
        result = compile_plan(output, load_spec())
        self.assertTrue(result.requires_confirmation)

    def test_model_can_adjust_rendered_figure_font_size(self):
        output = PlannerOutput(
            action="patch",
            reply="把图中文字调到 8 pt。",
            edits=[edit("set_figure_font_size", number_value=8)],
        )
        result = compile_plan(output, load_spec())
        operation = result.patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["field"], operation["value"]), ("layout", "font_size", 8.0))

    def test_model_can_adjust_one_layer_label_font_size(self):
        output = PlannerOutput(
            action="patch",
            reply="增大 MYC 标签。",
            edits=[edit("set_layer_font_size", target_id="genes_layer", number_value=8)],
        )
        result = compile_plan(output, load_spec())
        operation = result.patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["target_id"], operation["field"], operation["value"]),
                         ("layer", "genes_layer", "style.fontsize", 8.0))

    def test_model_output_allows_omitted_unused_null_fields(self):
        output = PlannerOutput.model_validate({
            "action": "patch", "reply": "增大标签。",
            "edits": [{"edit_type": "set_layer_font_size", "target_id": "genes_layer", "number_value": 8}],
        })
        self.assertIsNone(output.edits[0].string_value)
        self.assertEqual(compile_plan(output, load_spec()).patch["operations"][0]["field"], "style.fontsize")

    def test_model_can_increase_right_margin_for_clipped_colorbar_label(self):
        output = PlannerOutput(
            action="patch",
            reply="增加右侧留白。",
            edits=[edit("set_layout_right_margin", number_value=3.2)],
        )
        operation = compile_plan(output, load_spec()).patch["operations"][0]
        self.assertEqual((operation["target_kind"], operation["field"], operation["value"]),
                         ("layout", "right_margin_cm", 3.2))

    def test_unknown_model_target_is_rejected(self):
        output = PlannerOutput(
            action="patch",
            reply="修改颜色。",
            edits=[edit("set_layer_color", target_id="invented", string_value="red")],
        )
        with self.assertRaisesRegex(ValueError, "不存在"):
            compile_plan(output, load_spec())

    def test_openai_planner_uses_structured_parse_and_redacted_context(self):
        output = PlannerOutput(action="clarify", reply="你指的是哪条 ATAC 轨道？", edits=[])
        client = FakeClient(output)
        planner = OpenAIPlanner(client=client, model="test-model")
        result = planner.interpret(
            "ATAC 清楚一点",
            load_spec(),
            history=[
                {"role": "user", "content": r"数据在 E:\private\sample.mcool"},
                {"role": "assistant", "content": "已经载入当前图。"},
            ],
        )
        self.assertEqual(result.action, "clarify")
        self.assertEqual(client.responses.call["text_format"], PlannerOutput)
        request = client.responses.call["input"][1]["content"]
        self.assertNotIn("demo/data", request)
        self.assertNotIn("E:\\private", request)
        self.assertIn("recent_dialogue", request)

    def test_deepseek_uses_same_safe_schema_with_its_provider_identity(self):
        output = PlannerOutput(action="clarify", reply="请明确要修改哪条轨道。", edits=[])
        client = FakeDeepSeekClient(output)
        planner = DeepSeekPlanner(client=client, model="deepseek-v4-flash")
        result = planner.interpret("让信号明显些", load_spec())
        self.assertEqual(result.planner, "deepseek:deepseek-v4-flash")
        self.assertEqual(planner.status()["provider"], "deepseek")
        self.assertEqual(client.chat.completions.call["response_format"], {"type": "json_object"})
        self.assertNotIn("demo/data", client.chat.completions.call["messages"][1]["content"])

    def test_deepseek_repairs_one_invalid_plan_with_validation_feedback(self):
        invalid = PlannerOutput(
            action="patch", reply="增大标签。",
            edits=[edit("set_layer_font_size", target_id="invented", number_value=8)],
        )
        repaired = PlannerOutput(
            action="patch", reply="增大基因标签。",
            parameter_edits=[ParameterEdit(
                target_kind="layer", target_id="genes_layer", parameter="style.fontsize",
                operation="increase", value=2,
            )],
        )

        class SequentialCompletions:
            def __init__(self):
                self.outputs = [invalid, repaired]
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                output = self.outputs.pop(0)
                message = type("Message", (), {"content": output.model_dump_json()})()
                return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

        completions = SequentialCompletions()
        client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
        result = DeepSeekPlanner(client=client, model="test").interpret("这个标签再大一点", load_spec())
        self.assertEqual(len(completions.calls), 2)
        repair_payload = json.loads(completions.calls[1]["messages"][1]["content"])
        self.assertIn("previous_plan_error", repair_payload)
        self.assertEqual(result.patch["operations"][0]["target_id"], "genes_layer")

    def test_registry_rejects_unconfigured_provider(self):
        registry = PlannerRegistry(
            {"local": RuleFirstPlanner(NeverAiPlanner())},
            {"deepseek": "未设置 DEEPSEEK_API_KEY。"},
        )
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            registry.get("deepseek")

    def test_runtime_api_configuration_is_in_memory_and_redacted(self):
        registry = PlannerRegistry({"local": NeverAiPlanner()}, {"deepseek": "未配置。"})
        secret = "runtime-secret-key"
        registry.configure_runtime("deepseek", secret, model="deepseek-chat", client=object())
        status = registry.status()
        self.assertEqual(status["default_provider"], "deepseek")
        configured = next(item for item in status["providers"] if item["id"] == "deepseek")
        self.assertTrue(configured["available"])
        self.assertEqual(configured["model"], "deepseek-chat")
        self.assertNotIn(secret, json.dumps(status))
        registry.remove_runtime("deepseek")
        self.assertEqual(registry.status()["default_provider"], "local")

    def test_environment_builds_deepseek_without_exposing_credential(self):
        secret = "test-deepseek-secret"
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "",
            "DEEPSEEK_API_KEY": secret,
            "CFIZZ_AGENT_PROVIDER": "deepseek",
        }):
            registry = build_planner_registry_from_env()
            status = registry.status()
        self.assertEqual(status["default_provider"], "deepseek")
        deepseek = next(item for item in status["providers"] if item["id"] == "deepseek")
        self.assertTrue(deepseek["available"])
        self.assertEqual(deepseek["model"], "deepseek-chat")
        self.assertNotIn(secret, json.dumps(status))

    def test_rule_first_avoids_model_for_known_command(self):
        class NeverCalled:
            def interpret(self, message, spec):
                raise AssertionError("AI should not be called")

            def status(self):
                return {"mode": "ai-assisted"}

        result = RuleFirstPlanner(NeverCalled()).interpret("撤销", load_spec())
        self.assertEqual(result.action, "undo")
        self.assertEqual(result.planner, "rules")

    def test_rule_first_handles_track_order_without_calling_model(self):
        class NeverCalled:
            def interpret(self, message, spec):
                raise AssertionError("AI should not be called for a concrete track reorder")

            def status(self):
                return {"mode": "ai-assisted", "provider": "fake", "model": "test"}

        result = RuleFirstPlanner(NeverCalled()).interpret("把 ATAC normal 放到 CTCF normal 上面", load_spec())
        self.assertEqual(result.action, "patch")
        self.assertEqual(result.patch["operations"][0]["op"], "move_layer")
        self.assertEqual(result.planner, "rules")

    def test_connected_ai_gets_natural_language_before_matching_local_rules(self):
        class RecordingAi:
            def __init__(self):
                self.called = False

            def interpret(self, message, spec, history=None):
                self.called = True
                return compile_plan(
                    PlannerOutput(
                        action="patch",
                        reply="增高基因注释面板以解决标签重叠。",
                        edits=[edit("set_panel_height", target_id="annotation_panel", number_value=2.2)],
                    ),
                    spec,
                    planner="fake:ai",
                )

            def status(self):
                return {"mode": "ai-assisted", "provider": "fake", "model": "test"}

        ai = RecordingAi()
        result = RuleFirstPlanner(ai).interpret("MYC 这个标签重叠了，有点看不清", load_spec())
        self.assertTrue(ai.called)
        self.assertEqual(result.planner, "fake:ai")
        self.assertEqual(result.patch["operations"][0]["target_id"], "annotation_panel")

    def test_known_font_request_bypasses_ai_and_uses_renderer_parameter(self):
        class InvalidAi:
            def interpret(self, message, spec, history=None):
                raise AssertionError("确定的字体请求不应调用 AI")

            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

        spec = load_spec()
        spec["figure_type"] = "compartment_diff_scatter"
        spec["workflow_options"] = {"font_size": 6}
        result = RuleFirstPlanner(InvalidAi()).interpret("字体换到2pt", spec)
        self.assertEqual(result.action, "patch")
        self.assertEqual(result.patch["operations"][0]["field"], "workflow_options.font_size")
        self.assertEqual(result.patch["operations"][0]["value"], 3.0)
        self.assertNotIn("安全校验", result.reply)

    def test_ai_validation_failure_reports_exact_parameter_error(self):
        class InvalidAi:
            def interpret(self, message, spec, history=None):
                raise ValueError("patch 动作至少需要一个 edit、parameter_edit 或 capability call。")

            def status(self):
                return {"mode": "ai-assisted", "provider": "deepseek", "model": "test"}

        result = RuleFirstPlanner(InvalidAi()).interpret("有的标签重叠了", load_spec())
        self.assertEqual(result.action, "patch")
        self.assertIn("编辑计划校验未通过", result.reply)
        self.assertIn("patch 动作至少需要一个", result.reply)
        self.assertNotIn("安全校验", result.reply)
        self.assertIn("本地能力完成", result.reply)

    def test_rule_first_answers_model_identity_without_calling_model(self):
        planner = RuleFirstPlanner(NeverAiPlanner())
        planner.ai_planner.status = lambda: {
            "mode": "ai-assisted", "provider": "deepseek", "model": "deepseek-chat"
        }
        result = planner.interpret("你现在是什么模型？", load_spec())
        self.assertEqual(result.action, "answer")
        self.assertIn("DeepSeek", result.reply)
        self.assertIn("deepseek-chat", result.reply)

    def test_compile_plan_accepts_non_mutating_answer(self):
        result = compile_plan(
            PlannerOutput(action="answer", reply="当前图是 A/B compartment 图。", edits=[]),
            load_spec(),
            planner="fake:model",
        )
        self.assertEqual(result.action, "answer")
        self.assertIsNone(result.patch)


if __name__ == "__main__":
    unittest.main()
